"""Tests for the local side of the iSDA pull: config, tile stitching, labels and validation."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from data.grid import GridSpec
from data.isda import (
    NODATA,
    Layer,
    check_bounds,
    check_range,
    label_categories,
    layer_tiles,
    load_isda_config,
    numeric_columns,
    read_layer,
    tile_path,
)

CONFIG = load_isda_config()

# The 21 assets in ISDASOIL/Africa/v1, listed from Earth Engine on 2026-11-09.
ISDA_ASSETS = {
    "aluminium_extractable",
    "bedrock_depth",
    "bulk_density",
    "calcium_extractable",
    "carbon_organic",
    "carbon_total",
    "cation_exchange_capacity",
    "clay_content",
    "fcc",
    "iron_extractable",
    "magnesium_extractable",
    "nitrogen_total",
    "ph",
    "phosphorus_extractable",
    "potassium_extractable",
    "sand_content",
    "silt_content",
    "stone_content",
    "sulphur_extractable",
    "texture_class",
    "zinc_extractable",
}


def test_config_covers_all_21_isda_assets() -> None:
    configured = set(CONFIG.numeric) | {
        CONFIG.bedrock.asset,
        CONFIG.texture.asset,
        CONFIG.fcc.asset,
    }
    assert configured == ISDA_ASSETS


def test_numeric_columns_carry_depth_and_bounds() -> None:
    assert numeric_columns("soil_ph", ["0_20", "20_50"]) == [
        "soil_ph_0_20cm",
        "soil_ph_0_20cm_lower",
        "soil_ph_0_20cm_upper",
        "soil_ph_20_50cm",
        "soil_ph_20_50cm_lower",
        "soil_ph_20_50cm_upper",
    ]


def write_tile(path: Path, values: np.ndarray, spec: GridSpec) -> None:
    """Save a small GeoTIFF the way Earth Engine lays out a download."""
    half = spec.cell_size_m / 2
    x = spec.west_m + half + spec.cell_size_m * np.arange(spec.width)
    y = spec.north_m - half - spec.cell_size_m * np.arange(spec.height)
    raster = xr.DataArray(values, dims=("band", "y", "x"), coords={"x": x, "y": y})
    path.parent.mkdir(parents=True, exist_ok=True)
    raster.rio.write_crs(spec.crs).rio.to_raster(path)


def test_read_layer_stitches_tiles_and_masks_nodata(tmp_path: Path) -> None:
    # 10 m source with 40 cells per tile, so a 50 x 2 grid needs two tiles.
    layer = Layer("cropland", ["cropland_fraction"], image=None, reducer=None, source_scale_m=10)
    spec = GridSpec("EPSG:32632", 1000, 300_000.0, 1_100_000.0, width=50, height=2)
    expected = np.linspace(0, 1, 100, dtype=np.float32).reshape(1, 2, 50)
    expected[0, 0, 3] = NODATA
    for tile in layer_tiles(layer, spec, CONFIG):
        cols = slice(tile.col_offset, tile.col_offset + tile.spec.width)
        write_tile(tile_path(tmp_path, layer, tile), expected[:, :, cols], tile.spec)

    column = read_layer(tmp_path, layer, spec, CONFIG)["cropland_fraction"]
    assert np.isnan(column[3])
    assert np.allclose(column.drop(3), np.delete(expected.ravel(), 3))


def test_read_layer_rejects_tile_off_grid(tmp_path: Path) -> None:
    layer = Layer("ph", ["soil_ph_0_20cm"], image=None, reducer=None, source_scale_m=30)
    spec = GridSpec("EPSG:32632", 1000, 300_000.0, 1_100_000.0, width=2, height=2)
    shifted = GridSpec("EPSG:32632", 1000, 301_000.0, 1_100_000.0, width=2, height=2)
    (tile,) = layer_tiles(layer, spec, CONFIG)
    write_tile(tile_path(tmp_path, layer, tile), np.ones((1, 2, 2), np.float32), shifted)
    with pytest.raises(ValueError, match="not on the current grid"):
        read_layer(tmp_path, layer, spec, CONFIG)


def test_label_categories_names_texture_classes() -> None:
    table = pd.DataFrame(
        {
            "texture_class_0_20cm": [9.0, np.nan],
            "texture_class_20_50cm": [6.0, 4.0],
            "fcc_code": [12.0, np.nan],
        }
    )
    labelled = label_categories(table, CONFIG)
    assert list(labelled["texture_name_0_20cm"].astype(object)) == ["Sandy Loam", np.nan]
    assert list(labelled["texture_name_20_50cm"]) == ["Sandy Clay Loam", "Clay Loam"]
    assert str(labelled["fcc_code"].dtype) == "Int16"


def test_check_range_ignores_nulls_and_catches_out_of_range() -> None:
    table = pd.DataFrame({"soil_ph_0_20cm": [5.9, np.nan, 6.1]})
    check_range(table, "soil_ph_0_20cm", 3, 11)
    table.loc[1, "soil_ph_0_20cm"] = 14.0
    with pytest.raises(ValueError, match="1 values outside"):
        check_range(table, "soil_ph_0_20cm", 3, 11)


def test_check_bounds_catches_value_outside_its_bounds() -> None:
    table = pd.DataFrame({"x": [5.0, 5.0], "x_lower": [4.0, 5.5], "x_upper": [6.0, 6.0]})
    with pytest.raises(ValueError, match="1 cells"):
        check_bounds(table, "x")
