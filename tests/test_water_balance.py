"""Tests for the A9 FAO-56 water balance: Kc curve, roots, climate adjustment, TAW, Ks, bucket."""

import numpy as np
import pandas as pd
import pytest

from model.settings import load_crop_config, load_model_config
from model.water_balance import (
    adjust_kc_for_climate,
    depletion_fraction,
    kc_weights,
    rh_min_pct,
    root_depth_m,
    run_bucket,
    saturation_vapour_pressure_kpa,
    stage_totals,
    total_available_water_mm,
    water_stress_coefficient,
)
from model.water_pipeline import canonical_lga, frequency_table

LENGTHS = (10, 20, 30, 10)


def kc_curve(ini: float, mid: float, end: float) -> np.ndarray:
    w = kc_weights(LENGTHS)
    return ini * w[0] + mid * w[1] + end * w[2]


def test_every_crop_in_config_loads_with_stage_lengths() -> None:
    crops = load_crop_config().crops
    assert {"maize", "sorghum", "millet", "soybean", "cowpea", "groundnut", "sesame"} <= set(crops)
    assert crops["maize"].stages_days.total == 125


def test_smap_check_crop_is_a_configured_crop() -> None:
    assert load_model_config().water_balance.smap_check_crop in load_crop_config().crops


def test_kc_curve_follows_fao56_figure_25() -> None:
    kc = kc_curve(0.3, 1.2, 0.35)
    assert len(kc) == sum(LENGTHS)
    np.testing.assert_allclose(kc[:10], 0.3)  # initial stage is flat at Kc ini
    np.testing.assert_allclose(kc[30:60], 1.2)  # mid-season is flat at Kc mid
    assert kc[10] < kc[20] < kc[29]  # development climbs
    assert kc[60] > kc[69] > 0.35  # late season falls toward Kc end


def test_roots_grow_until_the_end_of_development_then_hold() -> None:
    roots = root_depth_m(LENGTHS, 0.2, 1.7)
    assert roots[0] == pytest.approx(0.2, abs=0.03)
    assert roots[29] == pytest.approx(1.7, abs=0.03)
    np.testing.assert_allclose(roots[30:], 1.7)


def test_saturation_vapour_pressure_matches_fao56_annex_2() -> None:
    # FAO-56 Annex 2, Table 2.3: 2.338 kPa at 20 deg C and 4.243 kPa at 30 deg C.
    values = saturation_vapour_pressure_kpa(np.array([20.0, 30.0]))
    np.testing.assert_allclose(values, [2.338, 4.243], atol=0.001)


def test_rh_min_is_100_when_dewpoint_equals_tmax() -> None:
    assert rh_min_pct(np.array([25.0]), np.array([25.0]))[0] == pytest.approx(100.0)


def test_kc_climate_adjustment_is_zero_at_the_reference_climate() -> None:
    assert adjust_kc_for_climate(1.2, np.array([2.0]), np.array([45.0]), 2.0)[0] == 1.2
    # Humid, calm air lowers Kc mid; dry, windy air raises it (FAO-56 Eq. 62).
    humid = adjust_kc_for_climate(1.2, np.array([1.0]), np.array([80.0]), 2.0)[0]
    dry = adjust_kc_for_climate(1.2, np.array([4.0]), np.array([20.0]), 2.0)[0]
    assert humid < 1.2 < dry
    expected = 1.2 + (0.04 * (1 - 2) - 0.004 * (80 - 45)) * (2 / 3) ** 0.3
    assert humid == pytest.approx(expected)


def test_taw_adds_topsoil_and_subsoil_layers() -> None:
    taw = total_available_water_mm(
        np.array([0.1, 0.2, 1.0]), np.array([100.0]), np.array([150.0]), top_m=0.2
    )
    np.testing.assert_allclose(taw[0], [10.0, 20.0, 20.0 + 0.8 * 150.0])


def test_p_is_adjusted_for_et_and_kept_between_bounds() -> None:
    np.testing.assert_allclose(depletion_fraction(0.55, np.array([5.0, 3.0])), [0.55, 0.63])
    assert depletion_fraction(0.75, np.array([0.0]))[0] == 0.8
    assert depletion_fraction(0.2, np.array([10.0]))[0] == 0.1


def test_ks_is_one_within_raw_and_falls_to_zero_at_taw() -> None:
    taw, raw = np.array([100.0] * 3), np.array([50.0] * 3)
    ks = water_stress_coefficient(np.array([40.0, 75.0, 100.0]), taw, raw)
    np.testing.assert_allclose(ks, [1.0, 0.5, 0.0])


def test_bucket_without_rain_dries_down_and_conserves_water() -> None:
    days = 60
    et0 = np.full((1, days), 5.0)
    result = run_bucket(
        np.zeros((1, days)), et0, np.ones((1, days)), np.full((1, days), 100.0), p_table=0.5
    )
    # Everything used came out of the soil store.
    assert result.depletion_mm[0, -1] == pytest.approx(result.eta_mm.sum())
    assert result.eta_mm[0, 0] == 5.0  # starts unstressed at field capacity
    assert result.ks[0, -1] < 1.0  # stressed once depletion passes RAW
    assert result.depletion_mm.max() <= 100.0


def test_bucket_refills_to_field_capacity_and_drains_the_excess() -> None:
    rain = np.zeros((1, 10))
    rain[0, 5] = 500.0
    result = run_bucket(
        rain, np.full((1, 10), 4.0), np.ones((1, 10)), np.full((1, 10), 80.0), p_table=0.5
    )
    assert result.depletion_mm[0, 5] == 0.0
    assert result.eta_mm.sum() == pytest.approx(result.etc_mm.sum())


def test_stage_totals_split_the_season_by_stage() -> None:
    daily = np.ones((2, sum(LENGTHS)))
    np.testing.assert_allclose(stage_totals(daily, LENGTHS), [list(LENGTHS)] * 2)


def test_lga_names_map_to_the_feature_spelling_and_unknown_names_fail() -> None:
    names = pd.Series(["Jema'A", "zaria"])
    assert canonical_lga(names, ["Jema'a", "Zaria"]).tolist() == ["Jema'a", "Zaria"]
    with pytest.raises(ValueError, match="Atlantis"):
        canonical_lga(pd.Series(["Atlantis"]), ["Zaria"])


def test_riskiest_stage_is_none_when_no_stage_falls_short() -> None:
    zeros = {f"shortfall_{s}_share": [0.0, 0.0] for s in ("init", "dev", "mid", "late")}
    rows = pd.DataFrame(
        {
            "lga": ["A", "A"],
            "crop": ["maize"] * 2,
            "year": [2001, 2002],
            "water_need_mm": [400.0, 420.0],
            "water_used_mm": [400.0, 420.0],
            "season_rain_mm": [900.0, 950.0],
            "shortfall_mm": [0.0, 0.0],
            "stress_days": [0, 0],
            "ran_short": [False, False],
            "fits_season": [True, False],
            "et0_filled_share": [0.0, 0.0],
            **zeros,
        }
    )
    table = frequency_table(rows, ["lga", "crop"])
    assert table.loc[0, "riskiest_stage"] == "none"
    assert table.loc[0, "fits_season_share"] == 0.5


def test_experiment_log_replaces_same_day_runs_and_keeps_others(tmp_path) -> None:  # noqa: ANN001
    from model.experiments import log_experiments

    log = tmp_path / "experiments.csv"
    row = {
        "date": "2026-11-14",
        "model": "A9",
        "features": "f",
        "split": "s",
        "seed": 0,
        "metrics": "{}",
        "notes": "first",
    }
    log_experiments(log, [row, {**row, "date": "2026-11-13"}])
    log_experiments(log, [{**row, "notes": "rerun"}])
    table = pd.read_csv(log)
    assert len(table) == 2
    assert table.loc[table["date"] == "2026-11-14", "notes"].item() == "rerun"
