"""Pull all 21 iSDAsoil layers for Kaduna onto the shared 1 km grid.

Earth Engine back-transforms each 30 m layer to real units, then averages it into 1 km cells
(the most common class for texture and FCC), and we download one GeoTIFF per layer to
data/raw/isda/. Locally, every cell is tagged with its LGA and the table is written to
data/processed/isda_kaduna_1000m.parquet.

Run: python -m data.isda
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ee
import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import rioxarray
import yaml
from pydantic import BaseModel, PositiveInt

from config import CONFIG_DIR
from config.settings import Settings
from data.boundaries import load_boundaries_config, load_kaduna_lgas
from data.grid import (
    GridConfig,
    GridSpec,
    Tile,
    assign_lga,
    cell_table,
    load_grid_config,
    raster_to_columns,
    snap_grid,
    split_grid,
)

log = logging.getLogger(__name__)

# Earth Engine fills masked pixels with 0 in downloads unless told otherwise, and 0 is a real
# value for several properties, so masked pixels are written as this and read back as NaN.
NODATA = -9999.0
DOWNLOAD_TIMEOUT_S = 600
# Float rounding in the 1 km means can put a bound a hair past its value.
BOUND_TOLERANCE = 1e-3

Transform = Literal["none", "div10", "div100", "exp10", "exp100"]


class NumericProperty(BaseModel):
    column: str
    transform: Transform
    valid_min: float
    valid_max: float | None = None


class BedrockConfig(BaseModel):
    asset: str
    column: str
    band_suffix: str
    valid_min: float
    valid_max: float


class TextureConfig(BaseModel):
    asset: str
    column: str
    classes: dict[int, str]


class FccConfig(BaseModel):
    asset: str
    column: str
    modulo: PositiveInt


class WorldCoverConfig(BaseModel):
    asset: str
    cropland_class: int
    column: str
    source_scale_m: PositiveInt


class IsdaConfig(BaseModel):
    """Settings from config/isda.yaml."""

    asset_root: str
    source_scale_m: PositiveInt
    depths_cm: list[str]
    numeric: dict[str, NumericProperty]
    bedrock: BedrockConfig
    texture: TextureConfig
    fcc: FccConfig
    worldcover: WorldCoverConfig
    max_source_px_per_side: PositiveInt
    download_workers: PositiveInt
    max_source_pixels: PositiveInt


def load_isda_config(path: Path = CONFIG_DIR / "isda.yaml") -> IsdaConfig:
    """Read and validate the iSDA config."""
    return IsdaConfig.model_validate(yaml.safe_load(path.read_text()))


def numeric_columns(column: str, depths_cm: list[str]) -> list[str]:
    """Output columns for one numeric property: value, lower and upper at each depth."""
    names = []
    for depth in depths_cm:
        base = f"{column}_{depth}cm"
        names += [base, f"{base}_lower", f"{base}_upper"]
    return names


# --- Earth Engine side ---------------------------------------------------------------------

TRANSFORMS: dict[str, Callable[[ee.Image], ee.Image]] = {
    "none": lambda x: x,
    "div10": lambda x: x.divide(10),
    "div100": lambda x: x.divide(100),
    "exp10": lambda x: x.divide(10).exp().subtract(1),
    "exp100": lambda x: x.divide(100).exp().subtract(1),
}


def numeric_image(asset: str, prop: NumericProperty, depths_cm: list[str]) -> ee.Image:
    """Value, lower and upper bands in real units for one property, at 30 m.

    Bounds are transform(mean - sd) and transform(mean + sd), assuming the stdev band shares
    the mean band's stored scale. For log-stored properties this keeps the bounds right,
    where back-transforming the SD alone would not.
    """
    raw = ee.Image(asset).toFloat()
    to_units = TRANSFORMS[prop.transform]
    bands = []
    for depth in depths_cm:
        mean = raw.select(f"mean_{depth}")
        sd = raw.select(f"stdev_{depth}")
        bands += [
            to_units(mean),
            to_units(mean.subtract(sd)).max(0),
            to_units(mean.add(sd)),
        ]
    return ee.Image.cat(bands).rename(numeric_columns(prop.column, depths_cm))


def bedrock_image(asset: str, cfg: BedrockConfig) -> ee.Image:
    """Bedrock depth in cm with lower and upper bounds, at 30 m."""
    raw = ee.Image(asset).toFloat()
    mean = raw.select(f"mean_{cfg.band_suffix}")
    sd = raw.select(f"stdev_{cfg.band_suffix}")
    return ee.Image.cat([mean, mean.subtract(sd).max(0), mean.add(sd)]).rename(
        [cfg.column, f"{cfg.column}_lower", f"{cfg.column}_upper"]
    )


@dataclass(frozen=True)
class Layer:
    """One layer to download: its folder name, bands, source scale, and how to coarsen it."""

    name: str
    bands: list[str]
    image: ee.Image
    reducer: ee.Reducer
    source_scale_m: int


def layers(cfg: IsdaConfig) -> list[Layer]:
    """Every layer to pull: 18 numeric properties, bedrock, texture, FCC and cropland."""
    root = cfg.asset_root
    scale = cfg.source_scale_m
    result = [
        Layer(
            name,
            numeric_columns(p.column, cfg.depths_cm),
            numeric_image(f"{root}/{name}", p, cfg.depths_cm),
            ee.Reducer.mean(),
            scale,
        )
        for name, p in cfg.numeric.items()
    ]
    bedrock_bands = [
        cfg.bedrock.column,
        f"{cfg.bedrock.column}_lower",
        f"{cfg.bedrock.column}_upper",
    ]
    result.append(
        Layer(
            cfg.bedrock.asset,
            bedrock_bands,
            bedrock_image(f"{root}/{cfg.bedrock.asset}", cfg.bedrock),
            ee.Reducer.mean(),
            scale,
        )
    )
    texture_bands = [f"{cfg.texture.column}_{d}cm" for d in cfg.depths_cm]
    texture = ee.Image(f"{root}/{cfg.texture.asset}")
    result.append(
        Layer(
            cfg.texture.asset,
            texture_bands,
            texture.select([f"texture_{d}" for d in cfg.depths_cm], texture_bands),
            ee.Reducer.mode(),
            scale,
        )
    )
    fcc = ee.Image(f"{root}/{cfg.fcc.asset}").select("fcc").mod(cfg.fcc.modulo)
    result.append(
        Layer(cfg.fcc.asset, [cfg.fcc.column], fcc.rename(cfg.fcc.column), ee.Reducer.mode(), scale)
    )
    landcover = ee.ImageCollection(cfg.worldcover.asset).first().select("Map")
    cropland = landcover.eq(cfg.worldcover.cropland_class).toFloat()
    result.append(
        Layer(
            "cropland",
            [cfg.worldcover.column],
            cropland.rename(cfg.worldcover.column),
            ee.Reducer.mean(),
            cfg.worldcover.source_scale_m,
        )
    )
    return result


def layer_tiles(layer: Layer, spec: GridSpec, cfg: IsdaConfig) -> list[Tile]:
    """Tiles small enough that Earth Engine accepts each download for this layer."""
    tile_cells = cfg.max_source_px_per_side * layer.source_scale_m // spec.cell_size_m
    return split_grid(spec, tile_cells)


def tile_path(folder: Path, layer: Layer, tile: Tile) -> Path:
    """Where one tile of one layer is saved."""
    return folder / layer.name / f"r{tile.row_offset:03d}_c{tile.col_offset:03d}.tif"


def download_tile(layer: Layer, spec: GridSpec, max_pixels: int, path: Path) -> None:
    """Coarsen a layer to the grid in Earth Engine and save one tile as a GeoTIFF."""
    gridded = (
        layer.image.reduceResolution(reducer=layer.reducer, maxPixels=max_pixels)
        .reproject(crs=spec.crs, crsTransform=spec.crs_transform)
        .unmask(NODATA)
        .toFloat()
    )
    url = gridded.getDownloadURL(
        {
            "crs": spec.crs,
            "crs_transform": spec.crs_transform,
            "dimensions": f"{spec.width}x{spec.height}",
            "format": "GEO_TIFF",
        }
    )
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


# --- Local side ----------------------------------------------------------------------------


def read_tile(path: Path, spec: GridSpec, band_count: int) -> np.ndarray:
    """Read one downloaded tile, failing if it is not exactly where the grid expects."""
    raster = rioxarray.open_rasterio(path)
    transform = raster.rio.transform()
    on_grid = (
        raster.shape == (band_count, spec.height, spec.width)
        and np.allclose([transform.c, transform.f], [spec.west_m, spec.north_m])
        and np.isclose(transform.a, spec.cell_size_m)
    )
    if not on_grid:
        raise ValueError(
            f"{path} is not on the current grid (shape {raster.shape}, {transform}). "
            "Delete the layer folder and rerun to pull it again."
        )
    return np.asarray(raster.values)


def read_layer(folder: Path, layer: Layer, spec: GridSpec, cfg: IsdaConfig) -> pd.DataFrame:
    """Stitch a layer's tiles back into one grid and return it as columns."""
    values = np.full((len(layer.bands), spec.height, spec.width), np.nan, dtype=np.float32)
    for tile in layer_tiles(layer, spec, cfg):
        rows = slice(tile.row_offset, tile.row_offset + tile.spec.height)
        cols = slice(tile.col_offset, tile.col_offset + tile.spec.width)
        path = tile_path(folder, layer, tile)
        values[:, rows, cols] = read_tile(path, tile.spec, len(layer.bands))
    columns = raster_to_columns(values, layer.bands)
    return columns.mask(columns == NODATA)


def label_categories(table: pd.DataFrame, cfg: IsdaConfig) -> pd.DataFrame:
    """Store class codes as small integers, and add texture names."""
    for depth in cfg.depths_cm:
        code = f"{cfg.texture.column}_{depth}cm"
        table[code] = table[code].round().astype("Int8")
        names = table[code].map(cfg.texture.classes)
        table[f"texture_name_{depth}cm"] = names.astype("category")
    table[cfg.fcc.column] = table[cfg.fcc.column].round().astype("Int16")
    return table


def build_table(
    cfg: IsdaConfig, spec: GridSpec, raw_dir: Path, lgas: gpd.GeoDataFrame, grid: GridConfig
) -> pd.DataFrame:
    """Join every downloaded layer onto the grid cells and keep the cells inside Kaduna."""
    frames = [cell_table(spec)]
    frames += [read_layer(raw_dir, layer, spec, cfg) for layer in layers(cfg)]
    table = assign_lga(pd.concat(frames, axis=1), lgas, grid)
    table = table.drop(columns=["easting_m", "northing_m"])
    return label_categories(table, cfg)


def check_range(table: pd.DataFrame, column: str, low: float, high: float | None) -> None:
    """Raise if any non-null value in column falls outside [low, high]."""
    values = table[column]
    bad = values < low
    if high is not None:
        bad |= values > high
    if bad.any():
        raise ValueError(
            f"{column}: {int(bad.sum())} values outside [{low}, {high}], e.g. {values[bad].iloc[0]}"
        )


def check_bounds(table: pd.DataFrame, column: str) -> None:
    """Raise if a value falls outside its own lower and upper bounds."""
    value, lower, upper = (table[c] for c in (column, f"{column}_lower", f"{column}_upper"))
    bad = (lower > value + BOUND_TOLERANCE) | (upper < value - BOUND_TOLERANCE)
    if bad.any():
        raise ValueError(f"{column}: {int(bad.sum())} cells have the value outside its bounds")


def validate(table: pd.DataFrame, cfg: IsdaConfig) -> None:
    """Fail loudly on anything physically impossible or structurally wrong."""
    keys = ["cell_id", "lga", "lat_deg", "lon_deg"]
    if table[keys].isna().any().any():
        raise ValueError(f"Null keys: {table[keys].isna().sum().to_dict()}")
    if table["cell_id"].duplicated().any():
        raise ValueError("Duplicate cell_id values")
    for prop in cfg.numeric.values():
        for depth in cfg.depths_cm:
            column = f"{prop.column}_{depth}cm"
            check_range(table, column, prop.valid_min, prop.valid_max)
            check_bounds(table, column)
    check_range(table, cfg.bedrock.column, cfg.bedrock.valid_min, cfg.bedrock.valid_max)
    check_bounds(table, cfg.bedrock.column)
    check_range(table, cfg.worldcover.column, 0, 1)
    for depth in cfg.depths_cm:
        codes = table[f"{cfg.texture.column}_{depth}cm"]
        check_range(table, codes.name, min(cfg.texture.classes), max(cfg.texture.classes))


def pull_missing(all_layers: list[Layer], spec: GridSpec, cfg: IsdaConfig, folder: Path) -> None:
    """Download every tile not already on disk, several at a time."""
    jobs = [
        (layer, tile.spec, tile_path(folder, layer, tile))
        for layer in all_layers
        for tile in layer_tiles(layer, spec, cfg)
        if not tile_path(folder, layer, tile).exists()
    ]
    log.info("Pulling %d tiles", len(jobs))
    with ThreadPoolExecutor(max_workers=cfg.download_workers) as pool:
        futures = [
            pool.submit(download_tile, layer, tile_spec, cfg.max_source_pixels, path)
            for layer, tile_spec, path in jobs
        ]
        for future in futures:
            future.result()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings()
    grid = load_grid_config()
    cfg = load_isda_config()
    ee.Initialize(project=settings.ee_project)

    lgas = load_kaduna_lgas(load_boundaries_config(), settings.data_raw_dir, grid.storage_crs)
    bounds_m = tuple(lgas.to_crs(grid.projected_crs).total_bounds)
    spec = snap_grid(bounds_m, grid.cell_size_m, grid.projected_crs)
    log.info("Grid: %d x %d cells of %d m", spec.width, spec.height, spec.cell_size_m)

    raw_dir = settings.data_raw_dir / "isda" / f"grid_{grid.cell_size_m}m"
    pull_missing(layers(cfg), spec, cfg, raw_dir)

    table = build_table(cfg, spec, raw_dir, lgas, grid)
    validate(table, cfg)
    settings.data_processed_dir.mkdir(parents=True, exist_ok=True)
    out = settings.data_processed_dir / f"isda_kaduna_{grid.cell_size_m}m.parquet"
    table.to_parquet(out, index=False)
    lgas.to_file(settings.data_processed_dir / "kaduna_lgas.geojson", driver="GeoJSON")
    log.info("Wrote %d cells x %d columns to %s", len(table), table.shape[1], out)


if __name__ == "__main__":
    main()
