"""Tests for A3 climate indicators, A4 bare fields, A7 trends, A10 land risk and A12 confidence."""

import numpy as np
import pandas as pd
import pytest

from model import bare_fields as bf
from model import confidence as cf
from model import indicators as ind
from model import land_risk as lr
from model import trends as tr

# ------------------------------------------------------------------ A3


def test_event_share_counts_years_with_at_least_one_event_day() -> None:
    month = pd.DataFrame({"lga": ["A"] * 4, "month": [3] * 4, "heat": [0, 2, 5, np.nan]})
    shares = ind.event_shares(month, {"heat": "heat"})
    # Three years with data, two with a heat day.
    assert shares.loc[("A", 3), "heat_share_years"] == pytest.approx(2 / 3)


def test_dry_soil_share_uses_the_threshold() -> None:
    month = pd.DataFrame({"lga": ["A"] * 3, "month": [7] * 3, "smap_anomaly_z": [-1.5, 0.2, -0.5]})
    assert ind.dry_soil_share(month, -1.0).loc[("A", 7)] == pytest.approx(1 / 3)


# ------------------------------------------------------------------ A7


def test_trend_detects_a_steady_rise_and_ignores_noise() -> None:
    years = np.arange(1981, 2026)
    rising = tr.trend_row(years, 2.0 * (years - 1981) + np.random.default_rng(0).normal(0, 1, 45))
    noise = tr.trend_row(years, np.random.default_rng(1).normal(0, 1, 45))
    assert rising["sen_slope_per_year"] == pytest.approx(2.0, abs=0.1)
    assert rising["trend_p"] < 0.001
    assert noise["trend_p"] > 0.05


def test_trend_p_is_never_looser_than_the_plain_test() -> None:
    years = np.arange(1981, 2026)
    alternating = np.where(years % 2 == 0, 1.0, -1.0) + 0.01 * (years - 1981)
    row = tr.trend_row(years, alternating)
    assert row["trend_p"] >= row["mk_p"]


def test_trend_needs_at_least_ten_years() -> None:
    with pytest.raises(ValueError, match="Too few years"):
        tr.trend_row(np.arange(5), np.arange(5.0))


def test_benjamini_hochberg_matches_hand_calculation_and_skips_missing() -> None:
    p = pd.Series([0.01, 0.04, np.nan, 0.03])
    adjusted = tr.benjamini_hochberg(p)
    # Three tests: 0.01 x 3/1 = 0.03; 0.03 x 3/2 = 0.045; 0.04 x 3/3 = 0.04, then monotone.
    np.testing.assert_allclose(adjusted.dropna().to_numpy(), [0.03, 0.04, 0.04])
    assert np.isnan(adjusted.iloc[2])


def test_card_line_wording() -> None:
    line = tr.card_line("Rain start", 4.2, "days", True, "later", "earlier")
    assert line == "Rain start: 4 days later per decade (clear trend)"
    assert "could be chance" in tr.card_line("Rain", -3.0, "mm", False)


# ------------------------------------------------------------------ A4


def test_flips_count_switches_within_each_year() -> None:
    bare = pd.Series([1, 1, 0, 0, 1, 0, 1, 0])
    lga = pd.Series(["A"] * 8)
    year = pd.Series([2001] * 4 + [2002] * 4)
    flips = bf.flips_per_year(bare, lga, year)
    assert flips.loc[("A", 2001)] == 1
    assert flips.loc[("A", 2002)] == 3


def test_dry_season_agreement() -> None:
    bare = pd.Series([True, True, False, True])
    month = pd.Series([1, 2, 7, 8])
    assert bf.dry_season_agreement(bare, month, [11, 12, 1, 2, 3, 4]) == 0.75


def test_relative_threshold_is_per_lga() -> None:
    ndvi = pd.Series(np.r_[np.linspace(0.2, 0.6, 100), np.linspace(0.3, 0.7, 100)])
    lga = pd.Series(["A"] * 100 + ["B"] * 100)
    thr = bf.relative_threshold(ndvi, lga, 0.25)
    assert thr.iloc[0] == pytest.approx(0.2 + 0.02 + 0.25 * 0.36, abs=0.01)
    assert thr.iloc[150] - thr.iloc[0] == pytest.approx(0.1, abs=0.01)


# ------------------------------------------------------------------ A10


def test_erosion_index_scales_from_zero_to_one_and_ranks_the_product() -> None:
    index = lr.erosion_index(
        pd.Series([4.0, 5.0, 4.0]), pd.Series([25.0, 30.0, 30.0]), pd.Series([1.0, 1.0, 1.0])
    )
    assert index.tolist() == [0.0, 1.0, pytest.approx(20 / 50)]
    assert lr.band(index, (0.333, 0.667)).tolist() == ["Low", "High", "Medium"]


def test_min_max_of_a_constant_is_one_half() -> None:
    assert lr.min_max(pd.Series([2.0, 2.0])).tolist() == [0.5, 0.5]


# ------------------------------------------------------------------ A12


def test_confidence_scores_levels_and_reasons() -> None:
    share = cf.score_higher_better(pd.Series([0.05, 0.2, 0.5]), (0.10, 0.30))
    filled = cf.score_lower_better(pd.Series([0.2, 0.07, 0.01]), (0.10, 0.05))
    assert share.tolist() == [0, 1, 2]
    assert filled.tolist() == [0, 1, 2]
    scores = pd.DataFrame(
        {
            "cropland": share,
            "vegetation_data": filled,
            "soil": [2, 2, 2],
            "vegetation_stress": [0, 0, 0],
        }
    )
    total = scores.sum(axis=1)
    assert cf.level(total, {"High": 6, "Medium": 4}).tolist() == ["Low", "Medium", "High"]
    reasons = cf.weakest_reason(scores)
    assert reasons.iloc[0].startswith("little cropland is mapped here; many vegetation")
    assert reasons.iloc[2] == "satellite greenness could not predict crop stress"


def test_soil_relative_width() -> None:
    isda = pd.DataFrame(
        {
            "sand_content__0_20__value_mean": [50.0],
            "sand_content__0_20__lower_mean": [45.0],
            "sand_content__0_20__upper_mean": [55.0],
        }
    )
    assert cf.soil_relative_width(isda, ["sand_content"], "0_20").iloc[0] == pytest.approx(0.2)
