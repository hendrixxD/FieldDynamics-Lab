"""A9. FAO-56 crop water balance: crop water need, actual use, and shortfall per growth stage.

Pure science functions. Equation numbers are from FAO-56 (Allen et al., 1998), chapters 6 to 8:
https://www.fao.org/4/x0490e/x0490e0b.htm and https://www.fao.org/4/x0490e/x0490e0e.htm.
Arrays shaped (n, days) hold one row per LGA-year, one column per day after planting.
"""

from dataclasses import dataclass

import numpy as np

STAGES = ("init", "dev", "mid", "late")


def stage_bounds(lengths_days: tuple[int, int, int, int]) -> np.ndarray:
    """Start day of each stage plus the season end: init, dev, mid, late, end."""
    return np.concatenate([[0], np.cumsum(lengths_days)])


def kc_weights(lengths_days: tuple[int, int, int, int]) -> np.ndarray:
    """Weights (3, days) so that Kc on each day = w_ini Kc_ini + w_mid Kc_mid + w_end Kc_end.

    FAO-56 Figure 25 and Eq. 66: Kc_ini through the initial stage, a straight line to Kc_mid
    over development, Kc_mid through mid-season, a straight line to Kc_end over late season.
    """
    start = stage_bounds(lengths_days)
    day = np.arange(start[-1]) + 0.5
    dev = np.clip((day - start[1]) / lengths_days[1], 0, 1)
    late = np.clip((day - start[3]) / lengths_days[3], 0, 1)
    return np.vstack([1 - dev, dev - late, late])


def root_depth_m(
    lengths_days: tuple[int, int, int, int], zr_start_m: float, zr_max_m: float
) -> np.ndarray:
    """Root depth per day (m): linear from zr_start_m at planting to zr_max_m at the end of the
    development stage, then constant. FAO-56 gives the maximum (Table 22), not the growth path;
    linear growth to full cover is our assumption."""
    start = stage_bounds(lengths_days)
    day = np.arange(start[-1]) + 0.5
    grown = np.clip(day / start[2], 0, 1)
    return zr_start_m + (zr_max_m - zr_start_m) * grown


def saturation_vapour_pressure_kpa(temp_c: np.ndarray) -> np.ndarray:
    """Saturation vapour pressure (kPa) at temp_c (deg C). FAO-56 Eq. 11."""
    return 0.6108 * np.exp(17.27 * temp_c / (temp_c + 237.3))


def rh_min_pct(dewpoint_c: np.ndarray, tmax_c: np.ndarray) -> np.ndarray:
    """Daily minimum relative humidity (%) from dewpoint and Tmax: 100 e(Tdew) / e(Tmax).

    FAO-56 Eq. 11 applied as the chapter 6 note on estimating RHmin when it is not measured.
    """
    ratio = saturation_vapour_pressure_kpa(dewpoint_c) / saturation_vapour_pressure_kpa(tmax_c)
    return np.clip(100 * ratio, 0, 100)


def adjust_kc_for_climate(
    kc_table: float,
    wind_m_s: np.ndarray,
    rhmin_pct: np.ndarray,
    height_m: float,
    wind_ref: float = 2.0,
    rhmin_ref: float = 45.0,
    wind_coef: float = 0.04,
    rh_coef: float = -0.004,
    exponent: float = 0.3,
) -> np.ndarray:
    """Kc mid (or Kc end) adjusted for climate other than u2 = 2 m/s, RHmin = 45 %.

    FAO-56 Eq. 62 (Eq. 65 for Kc end): Kc + [0.04 (u2 - 2) - 0.004 (RHmin - 45)] (h / 3)^0.3.
    Inputs are stage means; callers limit them to the valid range (1-6 m/s, 20-80 %).
    """
    shift = wind_coef * (wind_m_s - wind_ref) + rh_coef * (rhmin_pct - rhmin_ref)
    return kc_table + shift * (height_m / 3) ** exponent


def total_available_water_mm(
    root_depth: np.ndarray, awc_top_mm_m: np.ndarray, awc_sub_mm_m: np.ndarray, top_m: float
) -> np.ndarray:
    """Total available water in the root zone (mm). FAO-56 Eq. 82, TAW = 1000 (FC - WP) Zr,
    summed over a topsoil layer and the subsoil below it, each with its own AWC (mm per m).

    root_depth is (days,); awc arrays are (n,); returns (n, days).
    """
    top = np.minimum(root_depth, top_m)
    sub = np.maximum(root_depth - top_m, 0)
    return awc_top_mm_m[:, None] * top[None, :] + awc_sub_mm_m[:, None] * sub[None, :]


def depletion_fraction(
    p_table: float,
    etc_mm_day: np.ndarray,
    slope: float = 0.04,
    reference_mm_day: float = 5.0,
    low: float = 0.1,
    high: float = 0.8,
) -> np.ndarray:
    """Depletion fraction p adjusted for crop ET: p_table + 0.04 (5 - ETc), within 0.1 to 0.8.

    FAO-56 Table 22 note.
    """
    return np.clip(p_table + slope * (reference_mm_day - etc_mm_day), low, high)


def water_stress_coefficient(
    depletion_mm: np.ndarray, taw_mm: np.ndarray, raw_mm: np.ndarray
) -> np.ndarray:
    """Ks: 1 while depletion is within RAW, then (TAW - Dr) / (TAW - RAW). FAO-56 Eq. 84."""
    with np.errstate(divide="ignore", invalid="ignore"):
        stressed = (taw_mm - depletion_mm) / (taw_mm - raw_mm)
    return np.where(depletion_mm > raw_mm, np.clip(stressed, 0, 1), 1.0)


@dataclass
class BucketResult:
    """Daily (n, days) crop need, actual ET, stress coefficient and end-of-day depletion."""

    etc_mm: np.ndarray
    eta_mm: np.ndarray
    ks: np.ndarray
    depletion_mm: np.ndarray


def run_bucket(
    rain_mm: np.ndarray,
    et0_mm: np.ndarray,
    kc: np.ndarray,
    taw_mm: np.ndarray,
    p_table: float,
    p_slope: float = 0.04,
    p_reference: float = 5.0,
    p_low: float = 0.1,
    p_high: float = 0.8,
) -> BucketResult:
    """Daily root-zone depletion balance, FAO-56 Eq. 85, starting at field capacity.

    Dr_i = Dr_(i-1) - P_i + ETc_adj_i + DP_i, with ETc_adj = Ks Kc ET0 (Eq. 80) and Ks from the
    previous day's depletion. Rain beyond field capacity drains (DP); runoff, irrigation and
    capillary rise are 0. Sequential by day, vectorised across rows (engineering standards 3).
    """
    etc = kc * et0_mm
    eta, ks_out, dep_out = (np.empty_like(etc) for _ in range(3))
    depletion = np.zeros(etc.shape[0])
    for day in range(etc.shape[1]):
        p = depletion_fraction(p_table, etc[:, day], p_slope, p_reference, p_low, p_high)
        taw = taw_mm[:, day]
        ks = water_stress_coefficient(depletion, taw, p * taw)
        eta[:, day] = ks * etc[:, day]
        depletion = np.clip(depletion - rain_mm[:, day] + eta[:, day], 0, taw)
        ks_out[:, day], dep_out[:, day] = ks, depletion
    return BucketResult(etc, eta, ks_out, dep_out)


def stage_totals(daily: np.ndarray, lengths_days: tuple[int, int, int, int]) -> np.ndarray:
    """Sum a daily (n, days) array within each growth stage: returns (n, 4)."""
    start = stage_bounds(lengths_days)
    return np.add.reduceat(daily, start[:-1], axis=1)
