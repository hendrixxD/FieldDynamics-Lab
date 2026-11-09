"""The shared analysis grid: cell geometry, cell ids, and turning rasters into cell tables."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, PositiveInt

from config import CONFIG_DIR


class GridConfig(BaseModel):
    """Settings from config/grid.yaml."""

    projected_crs: str
    storage_crs: str
    cell_size_m: PositiveInt


def load_grid_config(path: Path = CONFIG_DIR / "grid.yaml") -> GridConfig:
    """Read and validate the grid config."""
    return GridConfig.model_validate(yaml.safe_load(path.read_text()))


@dataclass(frozen=True)
class GridSpec:
    """A north-up grid in a projected CRS, with its top-left corner on a whole cell."""

    crs: str
    cell_size_m: int
    west_m: float
    north_m: float
    width: int
    height: int

    @property
    def crs_transform(self) -> list[float]:
        """Affine transform in Earth Engine order: [x scale, 0, x origin, 0, y scale, y origin]."""
        return [self.cell_size_m, 0, self.west_m, 0, -self.cell_size_m, self.north_m]


@dataclass(frozen=True)
class Tile:
    """A piece of a grid, and where its first cell sits in the full grid."""

    row_offset: int
    col_offset: int
    spec: GridSpec


def split_grid(spec: GridSpec, tile_cells: int) -> list[Tile]:
    """Cut a grid into tiles of at most tile_cells a side, covering every cell exactly once."""
    if tile_cells < 1:
        raise ValueError(f"tile_cells must be at least 1, got {tile_cells}")
    tiles = []
    for row in range(0, spec.height, tile_cells):
        for col in range(0, spec.width, tile_cells):
            sub = GridSpec(
                crs=spec.crs,
                cell_size_m=spec.cell_size_m,
                west_m=spec.west_m + col * spec.cell_size_m,
                north_m=spec.north_m - row * spec.cell_size_m,
                width=min(tile_cells, spec.width - col),
                height=min(tile_cells, spec.height - row),
            )
            tiles.append(Tile(row, col, sub))
    return tiles


def snap_grid(bounds_m: tuple[float, float, float, float], cell_size_m: int, crs: str) -> GridSpec:
    """Smallest grid covering (west, south, east, north) in metres, with edges on whole cells.

    Snapping every dataset to the same cell edges is what lets them join on cell_id.
    """
    west_m, south_m, east_m, north_m = bounds_m
    if west_m >= east_m or south_m >= north_m:
        raise ValueError(f"Bounds must run west < east and south < north, got {bounds_m}")
    west_snapped = math.floor(west_m / cell_size_m) * cell_size_m
    south_snapped = math.floor(south_m / cell_size_m) * cell_size_m
    east_snapped = math.ceil(east_m / cell_size_m) * cell_size_m
    north_snapped = math.ceil(north_m / cell_size_m) * cell_size_m
    return GridSpec(
        crs=crs,
        cell_size_m=cell_size_m,
        west_m=west_snapped,
        north_m=north_snapped,
        width=(east_snapped - west_snapped) // cell_size_m,
        height=(north_snapped - south_snapped) // cell_size_m,
    )


def cell_table(spec: GridSpec) -> pd.DataFrame:
    """One row per cell in raster order (row by row from the north-west corner).

    cell_id is the cell's south-west corner in metres, e.g. E302000_N1150000, so ids stay
    the same whatever area a later pull covers.
    """
    rows, cols = np.indices((spec.height, spec.width)).reshape(2, -1)
    west_edge_m = spec.west_m + cols * spec.cell_size_m
    south_edge_m = spec.north_m - (rows + 1) * spec.cell_size_m
    half_m = spec.cell_size_m / 2
    return pd.DataFrame(
        {
            "cell_id": "E"
            + pd.Series(west_edge_m.round().astype(np.int64)).astype(str)
            + "_N"
            + pd.Series(south_edge_m.round().astype(np.int64)).astype(str),
            "easting_m": west_edge_m + half_m,
            "northing_m": south_edge_m + half_m,
        }
    )


def raster_to_columns(values: np.ndarray, names: list[str]) -> pd.DataFrame:
    """Flatten a (band, row, col) array into one float32 column per band, in raster order."""
    if values.ndim != 3 or values.shape[0] != len(names):
        raise ValueError(f"Expected {len(names)} bands as (band, row, col), got {values.shape}")
    flat = values.reshape(len(names), -1).T.astype(np.float32)
    return pd.DataFrame(flat, columns=names)


def assign_lga(cells: pd.DataFrame, lgas: gpd.GeoDataFrame, grid: GridConfig) -> pd.DataFrame:
    """Tag each cell with the LGA holding its centre, add lat/lon, and drop cells outside.

    A cell straddling two LGAs goes to the one holding its centre.
    """
    points = gpd.GeoDataFrame(
        cells,
        geometry=gpd.points_from_xy(cells["easting_m"], cells["northing_m"]),
        crs=grid.projected_crs,
    )
    joined = points.sjoin(lgas[["lga", "geometry"]].to_crs(grid.projected_crs), predicate="within")
    lonlat = joined.geometry.to_crs(grid.storage_crs)
    tagged = pd.DataFrame(joined.drop(columns=["geometry", "index_right"]))
    tagged["lat_deg"] = lonlat.y.astype(np.float32)
    tagged["lon_deg"] = lonlat.x.astype(np.float32)
    tagged["lga"] = tagged["lga"].astype("category")
    return tagged.reset_index(drop=True)
