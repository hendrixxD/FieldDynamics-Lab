import json
from pathlib import Path

import pandas as pd

from model import contract as ct
from model.settings import load_model_config

CFG = load_model_config()
TABLES = ct.load_inputs(CFG)


def test_doy_range_gives_median_and_the_8_in_10_range() -> None:
    a2 = pd.DataFrame({"lga": ["A"] * 11, "onset_doy": list(range(100, 210, 10))})
    row = ct.doy_range(a2, "onset_doy").loc["A"]
    assert row["median"] == 150
    assert row["p10"] == 110 and row["p90"] == 190
    assert row["years"] == 11


def test_dry_spell_months_counts_each_year_once_per_month() -> None:
    spells = pd.DataFrame(
        {
            "lga": ["A", "A", "A"],
            "year": [2001, 2001, 2002],
            "start": ["2001-07-02", "2001-07-20", "2002-10-05"],
        }
    )
    shares = ct.dry_spell_months(spells, pd.Series({"A": 4, "B": 2}))
    assert shares.loc["A", 7] == 0.25  # two July spells in 2001 are one year
    assert shares.loc["A", 10] == 0.25
    assert shares.loc["B"].sum() == 0  # an LGA with no spells still gets a row


def test_dry_spell_window_needs_the_minimum_share() -> None:
    shares = pd.Series([0.0] * 9 + [0.05, 0.0, 0.0], index=ct.MONTHS)
    assert ct.dry_spell_window(shares, 0.1)["usual_month"] is None
    assert ct.dry_spell_window(shares, 0.04)["usual_month"] == 10


def test_clean_makes_json_safe_values() -> None:
    import numpy as np

    assert ct.clean({"a": np.float32(1.23456), "b": float("nan"), "c": [np.int64(2)]}) == {
        "a": 1.235,
        "b": None,
        "c": [2],
    }


def test_season_file_has_the_contract_fields_for_every_lga() -> None:
    for lga in sorted(CFG.lga_zone):
        body = ct.season_file(lga, TABLES, CFG)
        assert body["contract_version"] == CFG.contract.version and body["source"] == "model"
        assert all(len(v) == 12 for v in body["months"].values())
        assert all(0 <= p <= 1 for p in body["months"]["bare_probability"])
        assert {t["name"] for t in body["season_types"]} == set(TABLES["a5_share"].columns)
        assert all(t["series"] in CFG.trends.card_series for t in body["trends"])
        assert body["onset_doy"]["p10"] <= body["onset_doy"]["median"] <= body["onset_doy"]["p90"]


def test_frost_is_reported_not_hidden() -> None:
    body = ct.season_file("Zaria", TABLES, CFG)
    assert body["months"]["frost_share_years"] == [0.0] * 12  # PRD: shown as very low


def test_committed_contract_is_current(tmp_path: Path) -> None:
    """api/contract must be rewritten whenever a model output changes."""
    fresh = ct.write_contract(CFG, tmp_path)
    committed = ct.contract_dir(CFG)
    for path in sorted(fresh.rglob("*.json")):
        old = json.loads((committed / path.relative_to(fresh)).read_text())
        assert json.loads(path.read_text()) == old, f"run: python -m model.contract ({path.name})"


def test_pixel_dry_spell_shares_cover_june_to_september_only() -> None:
    shares = ct.season_file("Zaria", TABLES, CFG)["months"]["dry_spell_cropland_share_7plus"]
    assert [v is not None for v in shares] == [m in (6, 7, 8, 9) for m in ct.MONTHS]
    assert all(0 <= v <= 1 for v in shares if v is not None)
