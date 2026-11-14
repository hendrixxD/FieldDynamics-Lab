"""Tests for A8 soil water: van Genuchten, the three pedotransfer functions, gravel, depths."""

import numpy as np
import pandas as pd
import pytest

from model import soil_water as sw
from model.soil_water import (
    agreement,
    bulk_water,
    hodnett_tomasella_params,
    layer_mean,
    retention,
    rosetta3_params,
    van_genuchten_theta,
)

SANDY_LOAM = pd.DataFrame(
    {
        "sand_pct": [58.0],
        "silt_pct": [20.0],
        "clay_pct": [22.0],
        "oc_g_kg": [8.0],
        "bd_g_cm3": [1.45],
        "cec_cmol_kg": [8.0],
        "ph": [5.9],
    }
)


def test_van_genuchten_is_saturated_at_zero_suction_and_dries_toward_residual() -> None:
    assert van_genuchten_theta(0.0, 0.05, 0.45, 0.1, 1.5) == pytest.approx(0.45)
    assert van_genuchten_theta(1e7, 0.05, 0.45, 0.1, 1.5) == pytest.approx(0.05, abs=1e-3)


def test_hodnett_tomasella_reproduces_gsif_worked_example() -> None:
    # GSIF AWCPTF documented example: sand 30, silt 25, clay 48, OC 23 g/kg, BD 1200 kg/m3,
    # CEC 12, pH 6.4 -> water at 1585 kPa 0.259, available water at 31.6 kPa 0.0999.
    p = hodnett_tomasella_params(30, 25, 48, 23, 1.2, 12, 6.4)
    wilting = van_genuchten_theta(1585, **p)
    assert float(wilting) == pytest.approx(0.259, abs=5e-4)
    assert float(van_genuchten_theta(31.6, **p) - wilting) == pytest.approx(0.0999, abs=5e-4)


def test_rosetta3_returns_plausible_parameters_for_a_sandy_loam() -> None:
    p = rosetta3_params([58], [20], [22], [1.45])
    assert 0.3 < p["theta_s"][0] < 0.5
    assert 1.1 < p["n"][0] < 2.0


@pytest.mark.parametrize("method", ["saxton_rawls", "hodnett_tomasella", "rosetta3"])
def test_every_method_holds_more_water_at_field_capacity_than_wilting_point(method: str) -> None:
    out = retention(method, SANDY_LOAM)
    assert 0.05 < out.loc[0, "wp"] < out.loc[0, "fc"] < 0.45


def test_unknown_method_fails_loudly() -> None:
    with pytest.raises(ValueError, match="Unknown soil water method"):
        retention("guesswork", SANDY_LOAM)


def test_stones_hold_no_water() -> None:
    fine = pd.DataFrame({"fc": [0.30], "wp": [0.10]})
    out = bulk_water(fine, [20.0])
    assert out.loc[0, "fc"] == pytest.approx(0.24)
    assert out.loc[0, "awc_mm_per_m"] == pytest.approx(160.0)


def test_layer_mean_interpolates_between_point_depths() -> None:
    depths = [0, 10, 30]
    values = np.array([[10.0, 20.0, 40.0]])
    # 0-20 cm: linear 10 -> 20 over 0-10, then 20 -> 30 over 10-20, so the mean is 20.
    assert layer_mean(depths, values, 0, 20)[0] == pytest.approx(20.0, abs=0.1)


def test_agreement_scores() -> None:
    scores = agreement(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 2.0, 4.0]))
    assert scores["bias"] == pytest.approx(-1 / 3)
    assert scores["mae"] == pytest.approx(1 / 3)


def test_texture_closed_so_rosetta_never_drops_an_lga() -> None:
    # Sums of 97 % and 102 %: Rosetta3 alone would return NaN for both.
    soil = pd.DataFrame(
        {
            "sand_pct": [56.0, 60.0],
            "silt_pct": [21.0, 21.0],
            "clay_pct": [20.0, 21.0],
            "oc_g_kg": [6.0, 6.0],
            "bd_g_cm3": [1.33, 1.33],
            "cec_cmol_kg": [8.6, 8.6],
            "ph": [5.9, 5.9],
        }
    )
    for method in ("saxton_rawls", "hodnett_tomasella", "rosetta3"):
        result = sw.retention(method, soil)
        assert result.notna().all().all()
        assert (result["fc"] > result["wp"]).all()


def test_agreement_refuses_missing_values() -> None:
    with pytest.raises(ValueError):
        sw.agreement(pd.Series([0.2, np.nan]), pd.Series([0.2, 0.3]))


def test_cropland_reference_scales_wilting_point_with_field_capacity() -> None:
    ref = sw.cropland_reference([0.30], [0.12], [0.24])
    assert ref.loc[0, "fc"] == pytest.approx(0.24)
    assert ref.loc[0, "wp"] == pytest.approx(0.096)
    assert ref.loc[0, "awc"] == pytest.approx(0.144)
