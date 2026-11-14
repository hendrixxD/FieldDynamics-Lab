"""Tests for A5 season types: anomalies, naming, the stress check and monthly profiles."""

import numpy as np
import pandas as pd
import pytest

from model.season_types import (
    monthly_profiles,
    name_clusters,
    stressed_share_gap,
    within_lga_anomalies,
    year_block_p,
)
from model.settings import load_model_config

CONFIG = load_model_config()
CENTROID_COLUMNS = [
    "a2_onset_doy",
    "a2_cessation_doy",
    "a2_length_days",
    "season_rain_mm",
    "a2_longest_dry_spell_days",
    "a2_dry_spells_10plus",
    "season_power_heat_days",
]


def test_config_zones_cover_23_distinct_lgas() -> None:
    lgas = [lga for zone in CONFIG.zones.values() for lga in zone]
    assert len(lgas) == 23
    assert len(set(lgas)) == 23


def test_config_k_is_a_scored_candidate() -> None:
    assert CONFIG.season_types.k in CONFIG.season_types.k_candidates


def test_anomalies_are_z_scores_within_each_lga() -> None:
    df = pd.DataFrame(
        {"lga": ["A", "A", "A", "B", "B", "B"], "rain_mm": [100.0, 200.0, 300.0, 10.0, 20.0, 30.0]}
    )
    z = within_lga_anomalies(df, ["rain_mm"])
    # Same pattern in both LGAs despite a tenfold difference in level.
    np.testing.assert_allclose(z["rain_mm"], [-1, 0, 1, -1, 0, 1])


def test_anomaly_is_zero_when_an_lga_never_varies_and_nan_stays_nan() -> None:
    df = pd.DataFrame({"lga": ["A", "A", "B", "B"], "spells": [0.0, 0.0, 1.0, np.nan]})
    z = within_lga_anomalies(df, ["spells"])
    assert z["spells"].iloc[:2].tolist() == [0.0, 0.0]
    assert np.isnan(z["spells"].iloc[3])


def centroids(rows: dict[str, dict[str, float]]) -> pd.DataFrame:
    frame = pd.DataFrame(0.0, index=range(len(rows)), columns=CENTROID_COLUMNS)
    for i, values in enumerate(rows.values()):
        for col, value in values.items():
            frame.loc[i, col] = value
    return frame


def test_clusters_are_named_from_their_centroids() -> None:
    c = centroids(
        {
            "typical": {"season_rain_mm": 0.1},
            "late": {"a2_onset_doy": 1.0, "a2_length_days": -1.0},
            "dry spell": {"a2_longest_dry_spell_days": 1.0, "a2_dry_spells_10plus": 0.8},
            "short and dry": {"a2_length_days": -0.6, "season_rain_mm": -0.9, "a2_onset_doy": 0.2},
        }
    )
    assert name_clusters(c, 0.3) == {
        0: "Typical",
        1: "Late start",
        2: "Dry spell",
        3: "Short and dry",
    }


def test_a_cluster_too_close_to_normal_is_not_given_a_trait_name() -> None:
    c = centroids({"typical": {}, "faint": {"season_rain_mm": 0.2}})
    assert name_clusters(c, 0.3) == {0: "Typical", 1: "Type 1"}


def test_stressed_share_gap_is_max_minus_min_share() -> None:
    labels = np.array(["a", "a", "b", "b", "b", "b"])
    stressed = np.array([True, True, True, False, False, False])
    assert stressed_share_gap(labels, stressed) == pytest.approx(1.0 - 0.25)


def test_year_block_p_is_high_when_types_carry_no_stress_signal() -> None:
    years, lgas = range(2000, 2010), ["A", "B", "C"]
    rows = [
        (y, lga, "x" if (y + i) % 2 else "y", False) for y in years for i, lga in enumerate(lgas)
    ]
    types = pd.DataFrame(rows, columns=["year", "lga", "season_type", "stressed"])
    # No year is stressed, so every permutation gives the same zero gap.
    assert year_block_p(types, runs=50, seed=0) == 1.0


def test_year_block_p_is_low_when_whole_years_line_up_with_stress() -> None:
    rows = [
        (y, lga, "dry" if y < 2010 else "wet", y < 2010)
        for y in range(2000, 2020)
        for lga in ["A", "B", "C"]
    ]
    types = pd.DataFrame(rows, columns=["year", "lga", "season_type", "stressed"])
    assert year_block_p(types, runs=500, seed=0) < 0.01


def test_profile_falls_back_to_lga_level_plus_statewide_shift() -> None:
    month = pd.DataFrame(
        {
            "lga": ["A", "A", "B", "B"],
            "year": [2001, 2002, 2001, 2002],
            "month": [7, 7, 7, 7],
            "rain_mm": [100.0, 140.0, 300.0, 260.0],
        }
    )
    types = pd.DataFrame(
        {
            "lga": ["A", "A", "B", "B"],
            "year": [2001, 2002, 2001, 2002],
            "season_type": ["Wet", "Dry", "Wet", "Wet"],
        }
    )
    out = monthly_profiles(month, types, ["rain_mm"], min_years=2)
    july = out[out["month"] == 7].set_index(["lga", "season_type"])
    # B has two Wet years of its own.
    assert july.loc[("B", "Wet"), "rain_mm"] == pytest.approx(280.0)
    assert july.loc[("B", "Wet"), "source"] == "lga"
    # A has one Wet year: A's mean (120) + statewide Wet (220) - statewide mean (200) = 140.
    assert july.loc[("A", "Wet"), "rain_mm"] == pytest.approx(140.0)
    assert july.loc[("A", "Wet"), "source"] == "statewide_shift"
    assert len(out) == 2 * 2 * 12
