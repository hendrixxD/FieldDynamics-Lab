"""Tests for picking Kaduna's LGAs out of geoBoundaries."""

import geopandas as gpd
import pytest
from shapely.geometry import box

from data.boundaries import load_boundaries_config, select_state_lgas

STATES = gpd.GeoDataFrame(
    {"shapeName": ["Kaduna", "Kano"]}, geometry=[box(0, 0, 2, 2), box(2, 0, 4, 2)]
)
LGAS = gpd.GeoDataFrame(
    {"shapeName": ["Zaria", "Chikun", "Kano Municipal"], "shapeID": ["z", "c", "k"]},
    geometry=[box(0, 1, 2, 2), box(0, 0, 2, 1), box(2, 0, 4, 2)],
)


def test_boundaries_config_pins_a_release() -> None:
    config = load_boundaries_config()
    assert "/9469f09/" in str(config.lga_url)
    assert config.expected_lga_count == 23


def test_select_state_lgas_keeps_only_that_state() -> None:
    selected = select_state_lgas(STATES, LGAS, "Kaduna", expected_count=2)
    assert list(selected["lga"]) == ["Chikun", "Zaria"]
    assert list(selected["lga_id"]) == ["c", "z"]


def test_select_state_lgas_fails_on_wrong_count() -> None:
    with pytest.raises(ValueError, match="Expected 3 LGAs"):
        select_state_lgas(STATES, LGAS, "Kaduna", expected_count=3)


def test_select_state_lgas_fails_on_unknown_state() -> None:
    with pytest.raises(ValueError, match="one state named 'Lagos'"):
        select_state_lgas(STATES, LGAS, "Lagos", expected_count=1)
