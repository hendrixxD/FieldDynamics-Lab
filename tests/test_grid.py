"""Tests for the shared analysis grid."""

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from data.grid import (
    GridConfig,
    GridSpec,
    assign_lga,
    cell_table,
    load_grid_config,
    raster_to_columns,
    snap_grid,
    split_grid,
)

GRID = GridConfig(projected_crs="EPSG:32632", storage_crs="EPSG:4326", cell_size_m=1000)


def test_grid_config_loads() -> None:
    config = load_grid_config()
    assert config.projected_crs == "EPSG:32632"
    assert config.cell_size_m == 1000


def test_snap_grid_rounds_outward_to_whole_cells() -> None:
    spec = snap_grid((300_400.0, 1_100_200.0, 302_100.0, 1_102_900.0), 1000, "EPSG:32632")
    assert (spec.west_m, spec.north_m) == (300_000, 1_103_000)
    assert (spec.width, spec.height) == (3, 3)
    assert spec.crs_transform == [1000, 0, 300_000, 0, -1000, 1_103_000]


def test_snap_grid_rejects_inverted_bounds() -> None:
    with pytest.raises(ValueError, match="west < east"):
        snap_grid((5.0, 0.0, 1.0, 1.0), 1000, "EPSG:32632")


def test_split_grid_covers_every_cell_once() -> None:
    spec = GridSpec("EPSG:32632", 1000, 0.0, 10_000.0, width=5, height=3)
    tiles = split_grid(spec, 2)
    covered = np.zeros((3, 5), dtype=int)
    for tile in tiles:
        rows = slice(tile.row_offset, tile.row_offset + tile.spec.height)
        cols = slice(tile.col_offset, tile.col_offset + tile.spec.width)
        covered[rows, cols] += 1
        assert tile.spec.west_m == tile.col_offset * 1000
        assert tile.spec.north_m == 10_000 - tile.row_offset * 1000
    assert (covered == 1).all()
    assert len(tiles) == 6


def test_cell_table_ids_and_centres_follow_raster_order() -> None:
    spec = GridSpec("EPSG:32632", 1000, 300_000.0, 1_102_000.0, width=2, height=2)
    cells = cell_table(spec)
    assert list(cells["cell_id"]) == [
        "E300000_N1101000",
        "E301000_N1101000",
        "E300000_N1100000",
        "E301000_N1100000",
    ]
    assert list(cells["easting_m"]) == [300_500, 301_500, 300_500, 301_500]
    assert list(cells["northing_m"]) == [1_101_500, 1_101_500, 1_100_500, 1_100_500]


def test_raster_to_columns_matches_cell_order() -> None:
    values = np.arange(8, dtype=np.float32).reshape(2, 2, 2)
    columns = raster_to_columns(values, ["a", "b"])
    assert list(columns["a"]) == [0, 1, 2, 3]
    assert list(columns["b"]) == [4, 5, 6, 7]
    assert columns.dtypes.eq(np.float32).all()


def test_raster_to_columns_rejects_band_mismatch() -> None:
    with pytest.raises(ValueError, match="Expected 3 bands"):
        raster_to_columns(np.zeros((2, 2, 2)), ["a", "b", "c"])


def test_assign_lga_tags_by_centre_and_drops_outside_cells() -> None:
    lgas = gpd.GeoDataFrame(
        {"lga": ["West", "East"]},
        geometry=[
            box(300_000, 1_100_000, 301_000, 1_101_000),
            box(301_000, 1_100_000, 302_000, 1_101_000),
        ],
        crs="EPSG:32632",
    ).to_crs("EPSG:4326")
    cells = pd.DataFrame(
        {
            "cell_id": ["a", "b", "c"],
            "easting_m": [300_500.0, 301_500.0, 305_500.0],
            "northing_m": [1_100_500.0] * 3,
        }
    )
    tagged = assign_lga(cells, lgas, GRID)
    assert dict(zip(tagged["cell_id"], tagged["lga"], strict=True)) == {"a": "West", "b": "East"}
    assert tagged["lat_deg"].between(9, 10.5).all()
    assert tagged["lon_deg"].between(6, 9).all()
