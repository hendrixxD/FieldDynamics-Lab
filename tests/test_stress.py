"""Tests for A6 crop stress: features, metrics, intervals, the honesty rule and scenarios."""

import numpy as np
import pandas as pd
import pytest

from model import stress
from model.stress import (
    SMAP,
    bootstrap_interval,
    climatology_rmse_skill,
    feature_climatology,
    interval_coverage,
    observed_record,
    permutation_shares,
    ridge_matrix,
    scenario_features,
    score_predictions,
    to_anomalies,
    window_sums,
)
from model.stress_pipeline import ship_decision


def test_window_sum_needs_every_month() -> None:
    month = pd.DataFrame(
        {
            "lga": ["A"] * 3,
            "year": [2001, 2001, 2002],
            "month": [7, 8, 7],
            "rain_mm": [200.0, 300.0, 250.0],
        }
    )
    sums = window_sums(month, "rain_mm", [7, 8])
    assert sums.loc[("A", 2001)] == 500.0
    # 2002 has July only, so its July-August total is unknown, not 250.
    assert np.isnan(sums.loc[("A", 2002)])


def test_anomalies_use_lga_climatology_and_pass_smap_and_static_through() -> None:
    df = pd.DataFrame(
        {
            "lga": ["A", "A", "A"],
            "rain_mm": [100.0, 200.0, 300.0],
            SMAP: [np.nan, 0.5, -0.5],
            "soil_mm": [80.0, 80.0, 80.0],
        }
    )
    clim = feature_climatology(df, ["rain_mm"])
    out = to_anomalies(df, clim, ["rain_mm", SMAP], ["soil_mm"])
    np.testing.assert_allclose(out["rain_mm"], [-1, 0, 1])
    assert np.isnan(out[SMAP].iloc[0])
    assert out["soil_mm"].tolist() == [80.0, 80.0, 80.0]


def test_ridge_matrix_fills_missing_smap_with_zero_and_no_era_indicator() -> None:
    x = pd.DataFrame({SMAP: [np.nan, 1.2], "rain_mm": [0.5, -0.5]})
    out = ridge_matrix(x)
    assert out[SMAP].tolist() == [0.0, 1.2]
    assert list(out.columns) == [SMAP, "rain_mm"]


def test_skill_is_one_for_a_perfect_model_and_zero_for_climatology() -> None:
    y = np.array([1.0, -1.0, 0.5])
    assert climatology_rmse_skill(y, y) == pytest.approx(1.0)
    assert climatology_rmse_skill(y, np.zeros(3)) == pytest.approx(0.0)
    assert climatology_rmse_skill(y, -y) < 0


def test_scores_and_coverage_per_scheme() -> None:
    cv = pd.DataFrame(
        {
            "scheme": ["s"] * 4,
            "target": [1.0, -1.0, 0.0, 2.0],
            "m": [1.0, -1.0, 0.0, 2.0],
            "low": [0.0, 0.0, -1.0, 0.0],
            "high": [2.0, 2.0, 1.0, 1.0],
        }
    )
    scores = score_predictions(cv, ["m"])
    assert scores.loc[0, "rmse"] == 0.0
    cov = interval_coverage(cv, {"band": ("low", "high")})
    # -1 is below its band and 2 above its band: two of four inside.
    assert cov.loc[0, "coverage"] == 0.5


def metrics_frame(rmse: dict[tuple[str, str], float]) -> pd.DataFrame:
    rows = [
        {"scheme": s, "model": m, "rmse": r, "skill_vs_climatology": 1 - r}
        for (s, m), r in rmse.items()
    ]
    return pd.DataFrame(rows)


@pytest.mark.parametrize(
    ("ridge", "lgbm", "shipped", "skilful"),
    [
        (0.90, 0.80, "lgbm", True),  # LightGBM beats Ridge, both beat climatology
        (0.90, 0.95, "ridge", True),  # LightGBM loses to Ridge: Ridge ships
        (1.02, 1.04, "ridge", False),  # nothing beats climatology: no skill
    ],
)
def test_honesty_rule(ridge: float, lgbm: float, shipped: str, skilful: bool) -> None:
    rmse = {
        (s, m): v
        for s in ("leave_one_year_out", "senatorial_zone")
        for m, v in (("ridge", ridge), ("lgbm", lgbm))
    }
    decision = ship_decision(metrics_frame(rmse), min_skill=0.02)
    assert decision["shipped"] == shipped
    assert decision["has_skill"] is skilful
    assert decision["card_source"] == ("a6_model" if skilful else "weather_risk_a2_a9")


def test_bootstrap_interval_is_ordered_and_brackets_a_noisy_line() -> None:
    rng = np.random.default_rng(0)
    x = pd.DataFrame({"rain_mm": rng.normal(size=300)})
    y = 2 * x["rain_mm"].to_numpy() + rng.normal(scale=0.5, size=300)
    x_test = pd.DataFrame({"rain_mm": [0.0, 1.0]})
    band = bootstrap_interval(x, y, x_test, alpha=1.0, quantiles=[0.1, 0.9], runs=200, seed=0)
    assert band.shape == (2, 2)
    assert (band[:, 0] < band[:, 1]).all()
    assert band[1, 0] < 2.0 < band[1, 1]


def test_permutation_share_is_zero_for_an_unused_column() -> None:
    rng = np.random.default_rng(0)
    x = pd.DataFrame({"used": rng.normal(size=200), "unused": rng.normal(size=200)})
    y = x["used"].to_numpy()
    shares = permutation_shares(lambda d: d["used"].to_numpy(), x, y, repeats=5, seed=0)
    assert shares["used"] == pytest.approx(1.0)
    assert shares["unused"] == 0.0


def test_scenario_uses_own_years_only_when_there_are_enough() -> None:
    anomalies = pd.DataFrame({"rain_mm": [1.0, 3.0, 2.0, -2.0]})
    types = pd.Series(["Wet", "Wet", "Wet", "Dry"])
    lga = pd.Series(["A", "A", "B", "B"])
    static = pd.DataFrame({"soil_mm": [80.0, 90.0]}, index=pd.Index(["A", "B"], name="lga"))
    out = scenario_features(anomalies, types, lga, ["rain_mm"], static, min_years=2)
    out = out.set_index(["lga", "season_type"])
    assert out.loc[("A", "Wet"), "rain_mm"] == 2.0  # A's own two Wet years
    assert out.loc[("B", "Wet"), "rain_mm"] == 2.0  # statewide Wet mean of 1, 3, 2
    assert out.loc[("A", "Dry"), "scenario_source"] == "statewide_type_mean"
    assert out.loc[("B", "Dry"), "soil_mm"] == 90.0


def test_observed_record_counts_stressed_years() -> None:
    record = observed_record(
        pd.Series([-1.0, 0.2, -0.6, np.nan]),
        pd.Series(["Dry", "Dry", "Dry", "Dry"]),
        pd.Series(["A", "A", "A", "A"]),
        stressed_z=-0.5,
    )
    row = record.iloc[0]
    assert row["observed_years"] == 3
    assert row["observed_share_stressed"] == pytest.approx(2 / 3)


def test_diagnostics_separate_between_year_from_within_year_signal() -> None:
    from model.stress import signal_diagnostics

    rng = np.random.default_rng(0)
    years = np.repeat(np.arange(2000, 2020), 5)
    year_effect = rng.normal(size=20).repeat(5)
    x = pd.DataFrame({"year_driven": year_effect, "lga_noise": rng.normal(size=100)})
    y = year_effect + rng.normal(scale=0.1, size=100)
    diag = signal_diagnostics(x, y, years).set_index("feature")
    assert diag.loc["year_driven", "r_between_years"] > 0.9
    # The feature is identical across LGAs within a year, so it explains nothing inside one.
    assert (
        np.isnan(diag.loc["year_driven", "r_within_year"])
        or abs(diag.loc["year_driven", "r_within_year"]) < 0.3
    )
    assert diag.loc["year_driven", "loyo_skill"] > 0.5


def test_skill_vs_climatology_bounds() -> None:
    y = np.array([1.0, -1.0, 2.0, -2.0])
    assert stress.skill_vs_climatology(y, y) == 1.0
    assert stress.skill_vs_climatology(y, np.zeros(4)) == 0.0
    assert stress.skill_vs_climatology(y, -y) < 0


def test_shuffled_target_has_no_skill() -> None:
    rng = np.random.default_rng(0)
    x = pd.DataFrame({"a": rng.normal(size=200), "b": rng.normal(size=200)})
    y = 0.8 * x["a"].to_numpy() + rng.normal(scale=0.5, size=200)
    years = np.repeat(np.arange(10), 20)
    skills = stress.shuffled_target_skill(x, y, years, np.logspace(-2, 3, 20), runs=5, seed=1)
    assert (skills < 0.05).all()
