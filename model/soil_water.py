"""A8. Soil water holding: field capacity, wilting point and available water from iSDA soil data.

Three pedotransfer functions (PTFs) turn the same iSDA inputs into water retention; the A8
notebook scores them against OpenLandMap water content (Hengl and Gupta 2019), which is mapped
from measured soil profiles with no PTF, and A9 uses the best one.

- Saxton and Rawls (2006), Soil Sci. Soc. Am. J. 70:1569-1578: regressions on US soils
  (implemented in data/soil_hydro.py, with the bulk density adjustment).
- Hodnett and Tomasella (2002), Geoderma 108:155-180: van Genuchten parameters fitted on tropical
  soils (IGBP-DIS). Coefficients as implemented in ISRIC's GSIF package (AWCPTF), which this
  module reproduces exactly on GSIF's documented example.
- Rosetta3 (Zhang and Schaap 2017, J. Hydrol. 547:39-53): USDA neural network, through the
  rosetta-soil package.
Field capacity is water held at 33 kPa and wilting point at 1500 kPa, the suctions Saxton and
Rawls use, so all three are compared on the same definition.
"""

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from rosetta import rosetta

from data.soil_hydro import saxton_rawls

FIELD_CAPACITY_KPA = 33.0
WILTING_POINT_KPA = 1500.0
# 1 kPa of suction = 10.197 cm of water, to turn Rosetta's alpha (1/cm) into 1/kPa.
CM_WATER_PER_KPA = 10.197

# Hodnett and Tomasella (2002) via GSIF AWCPTF: rows are the intercept and predictors
# [1, sand, silt, clay, OC %, bulk density g/cm3, CEC cmol/kg, pH, silt^2, clay^2, sand*silt,
# sand*clay]; each parameter = row . predictors / 100; alpha and n are exp() of theirs.
# Alpha is in 1/kPa.
HODNETT_TOMASELLA = {
    "ln_alpha": [-2.294, 0, -3.526, 0, 2.44, 0, -0.076, -11.331, 0.019, 0, 0, 0],
    "ln_n": [62.986, 0, 0, -0.833, -0.529, 0, 0, 0.593, 0, 0.007, -0.014, 0],
    "theta_s": [81.799, 0, 0, 0.099, 0, -31.42, 0.018, 0.451, 0, 0, 0, -5e-4],
    "theta_r": [22.733, -0.164, 0, 0, 0, 0, 0.235, -0.831, 0, 0.0018, 0, 0.0026],
}


def van_genuchten_theta(
    h_kpa: float, theta_r: ArrayLike, theta_s: ArrayLike, alpha_per_kpa: ArrayLike, n: ArrayLike
) -> np.ndarray:
    """Volumetric water content (m3/m3) at suction h_kpa. van Genuchten (1980) with m = 1 - 1/n."""
    tr, ts = np.asarray(theta_r, float), np.asarray(theta_s, float)
    a, nn = np.asarray(alpha_per_kpa, float), np.asarray(n, float)
    return tr + (ts - tr) / (1 + (a * abs(h_kpa)) ** nn) ** (1 - 1 / nn)


def hodnett_tomasella_params(
    sand_pct: ArrayLike,
    silt_pct: ArrayLike,
    clay_pct: ArrayLike,
    oc_g_kg: ArrayLike,
    bd_g_cm3: ArrayLike,
    cec_cmol_kg: ArrayLike,
    ph: ArrayLike,
) -> dict[str, np.ndarray]:
    """van Genuchten parameters from Hodnett and Tomasella (2002). Texture is rescaled to sum to
    100 % and a negative theta_r is set to 0, as GSIF does."""
    s, si, c = (np.asarray(v, float) for v in (sand_pct, silt_pct, clay_pct))
    total = s + si + c
    s, si, c = s / total * 100, si / total * 100, c / total * 100
    x = np.stack(
        [
            np.ones_like(s),
            s,
            si,
            c,
            np.asarray(oc_g_kg, float) / 10,
            np.asarray(bd_g_cm3, float),
            np.asarray(cec_cmol_kg, float),
            np.asarray(ph, float),
            si**2,
            c**2,
            s * si,
            s * c,
        ]
    )
    coef = {k: np.asarray(v) @ x / 100 for k, v in HODNETT_TOMASELLA.items()}
    return {
        "theta_r": np.maximum(coef["theta_r"], 0),
        "theta_s": coef["theta_s"],
        "alpha_per_kpa": np.exp(coef["ln_alpha"]),
        "n": np.exp(coef["ln_n"]),
    }


def rosetta3_params(
    sand_pct: ArrayLike, silt_pct: ArrayLike, clay_pct: ArrayLike, bd_g_cm3: ArrayLike
) -> dict[str, np.ndarray]:
    """van Genuchten parameters from Rosetta3 (texture plus bulk density model)."""
    rows = np.column_stack([np.asarray(v, float) for v in (sand_pct, silt_pct, clay_pct, bd_g_cm3)])
    mean, _, _ = rosetta(3, rows.tolist())
    return {
        "theta_r": mean[:, 0],
        "theta_s": mean[:, 1],
        "alpha_per_kpa": mean[:, 2] * CM_WATER_PER_KPA,
        "n": mean[:, 3],
    }


def gravel_fraction(stone_pct: ArrayLike) -> np.ndarray:
    """Volume share of stones (0-1). iSDA stone content is read as volume percent."""
    return np.clip(np.asarray(stone_pct, float) / 100, 0, 0.9)


def close_texture(soil: pd.DataFrame) -> pd.DataFrame:
    """Rescale sand, silt and clay so they sum to exactly 100 %.

    iSDA maps each fraction separately, so LGA means sum to 97-102 %. Rosetta3 returns NaN for
    any sample outside 99-101 % (rosetta-soil 0.3.2, rosetta.py), so every method gets the same
    closed texture.
    """
    texture = soil[["sand_pct", "silt_pct", "clay_pct"]]
    closed = texture.div(texture.sum(axis=1), axis=0) * 100
    return soil.assign(**{c: closed[c] for c in closed.columns})


def retention(method: str, soil: pd.DataFrame) -> pd.DataFrame:
    """Field capacity and wilting point (m3/m3 of fine soil) for one method.

    soil needs sand_pct, silt_pct, clay_pct, oc_g_kg, bd_g_cm3, cec_cmol_kg and ph. Texture is
    closed to 100 % first; a method that still returns a missing value raises.
    """
    result = _retention(method, close_texture(soil))
    if result.isna().any().any():
        missing = result.index[result.isna().any(axis=1)].tolist()
        raise ValueError(f"{method} returned no value for {missing}")
    return result


def _retention(method: str, soil: pd.DataFrame) -> pd.DataFrame:
    """retention() without the checks."""
    if method == "saxton_rawls":
        sr = saxton_rawls(soil["sand_pct"], soil["clay_pct"], soil["oc_g_kg"], soil["bd_g_cm3"])
        return pd.DataFrame({"fc": sr["fc"], "wp": sr["wp"]}, index=soil.index)
    if method == "hodnett_tomasella":
        p = hodnett_tomasella_params(
            soil["sand_pct"],
            soil["silt_pct"],
            soil["clay_pct"],
            soil["oc_g_kg"],
            soil["bd_g_cm3"],
            soil["cec_cmol_kg"],
            soil["ph"],
        )
    elif method == "rosetta3":
        p = rosetta3_params(soil["sand_pct"], soil["silt_pct"], soil["clay_pct"], soil["bd_g_cm3"])
    else:
        raise ValueError(f"Unknown soil water method: {method!r}")
    return pd.DataFrame(
        {
            "fc": van_genuchten_theta(FIELD_CAPACITY_KPA, **p),
            "wp": van_genuchten_theta(WILTING_POINT_KPA, **p),
        },
        index=soil.index,
    )


def bulk_water(fine: pd.DataFrame, stone_pct: ArrayLike) -> pd.DataFrame:
    """Water per volume of whole soil: stones hold none (Saxton and Rawls 2006, gravel section).

    Adds awc (fc - wp) and awc_mm_per_m (mm of water per m of soil depth).
    """
    keep = 1 - gravel_fraction(stone_pct)
    out = fine.mul(keep, axis=0)
    out["awc"] = out["fc"] - out["wp"]
    out["awc_mm_per_m"] = out["awc"] * 1000
    return out


def layer_mean(
    depths_cm: ArrayLike, values: np.ndarray, top_cm: float, bottom_cm: float
) -> np.ndarray:
    """Mean of a profile sampled at point depths over a layer, by linear interpolation.

    values is (n, depths); returns (n,). Used for OpenLandMap, which gives point depths
    (0, 10, 30, 60, 100, 200 cm) while iSDA gives the 0-20 and 20-50 cm layers.
    """
    grid = np.linspace(top_cm, bottom_cm, 101)
    depths = np.asarray(depths_cm, float)
    return np.array([np.interp(grid, depths, row).mean() for row in np.atleast_2d(values)])


def agreement(predicted: pd.Series, reference: pd.Series) -> dict[str, float]:
    """Bias, mean absolute error and correlation of predicted against reference values.

    Raises on missing values: pandas would skip them and score a method on fewer LGAs.
    """
    if predicted.isna().any() or reference.isna().any():
        raise ValueError("agreement() got missing values; every LGA must be scored")
    diff = predicted - reference
    return {
        "bias": float(diff.mean()),
        "mae": float(diff.abs().mean()),
        "r": float(np.corrcoef(predicted, reference)[0, 1]),
    }


def cropland_reference(fc_all: ArrayLike, wp_all: ArrayLike, fc_crop: ArrayLike) -> pd.DataFrame:
    """Cropland field capacity, wilting point and available water from OpenLandMap (m3/m3).

    iSDA's inputs are cropland means, so the reference must describe cropland too. Field
    capacity is masked to cropland directly (Earth Engine). Wilting point exists only unmasked
    (Zenodo), so it is scaled by the same cropland-to-all-land ratio as field capacity: an
    assumption that the texture shift between cropland and other land moves both suctions in
    proportion.
    """
    fc_all, wp_all, fc_crop = (np.asarray(v, float) for v in (fc_all, wp_all, fc_crop))
    wp_crop = wp_all * fc_crop / fc_all
    return pd.DataFrame({"fc": fc_crop, "wp": wp_crop, "awc": fc_crop - wp_crop})
