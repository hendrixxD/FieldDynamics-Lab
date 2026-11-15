"""Unit tests for the view-model builders backing the web screens."""

import pytest

from model.engine_data import load_bundle
from model.view_data import (
    crop_water_view,
    doy_to_label,
    lga_catalog,
    map_trends_summary,
    parse_advisor_query,
    radio_script_view,
    season_view,
)

DATA = load_bundle()


def test_doy_to_label_formats_calendar_dates() -> None:
    assert doy_to_label(1) == "Jan 1"
    assert doy_to_label(146.0) == "May 26"
    assert doy_to_label(None) == "Unknown"


def test_lga_catalog_covers_all_23_kaduna_lgas() -> None:
    catalog = lga_catalog(DATA)
    assert len(catalog) == 23
    names = {item["lga"] for item in catalog}
    assert {"Zaria", "Kaduna North", "Jema'a", "Birnin Gwari"} <= names
    urban = {item["lga"] for item in catalog if item["low_cropland"]}
    assert urban == {"Kaduna North", "Kaduna South"}


def test_season_view_builds_12_months_and_9_patterns() -> None:
    sv = season_view("Zaria", "Dry spell", DATA)
    assert sv["lga"] == "Zaria"
    assert sv["season_type"] == "Dry spell"
    assert len(sv["strip"]) == 12
    assert len(sv["patterns"]) == 9
    assert "soil_moisture_anomaly" in sv["terms"]
    frost = next(p for p in sv["patterns"] if p["term_key"] == "frost_risk")
    assert frost["headline"] == "0.0% Risk"


def test_season_view_rejects_unknown_lga() -> None:
    with pytest.raises(ValueError, match="Unknown LGA"):
        season_view("Kano", "Typical", DATA)


def test_crop_water_view_returns_stages_and_comparisons() -> None:
    cw = crop_water_view("Zaria", "maize", "Late start", DATA)
    assert cw["crop"] == "maize"
    assert len(cw["stages"]) == 4
    assert cw["water_need_mm"] > 0
    assert len(cw["comparisons"]) >= 5


def test_map_trends_summary_populates_every_lga() -> None:
    trends = map_trends_summary(DATA)
    assert len(trends) == 23
    assert trends["Zaria"]["annual_rain_mm"] > 500


def test_parse_advisor_query_falls_back_cleanly() -> None:
    req = parse_advisor_query(
        {"lga": "Unknown", "s1": "maize,cowpea", "s2": "sorghum", "priorities": "save_water,bad"},
        DATA,
    )
    assert req.lga == "Zaria"
    assert req.past_seasons == [["sorghum"], ["maize", "cowpea"]]
    assert req.priorities == ["save_water"]


def test_radio_script_view_contains_no_farmer_pii_and_four_cues() -> None:
    radio = radio_script_view("Zaria", DATA)
    assert len(radio["en_segments"]) == 4
    assert len(radio["ha_segments"]) == 4
    assert radio["en_words"] > 60
    assert radio["ha_words"] > 60
