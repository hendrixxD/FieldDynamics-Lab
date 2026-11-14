"""Crop suitability per LGA from our data and the FAO ECOCROP crop requirements (EcoCrop model).

ECOCROP gives each crop's absolute and optimal ranges for temperature, season rainfall, soil
pH and texture. Our data gives each LGA's actual values: POWER monthly temperature and CHIRPS
monthly rain for every year since 1981, and iSDA cropland pH, sand and clay. The EcoCrop model
(Hijmans et al. 2001) scores each range with a trapezoid: 0 outside the absolute range, 1 inside
the optimal range, linear in between. A season scores the lower of its temperature and rain
scores; temperature is the worst month of the growing period.

Source of the crop ranges: FAO ECOCROP database, as shipped in the Recocrop R package
(Hijmans, GPL-3), extracted to data/raw/ecocrop/ecocrop_kaduna_crops.csv.
"""

import numpy as np
import pandas as pd

TEXTURE_CODES = {"L": "light", "M": "medium", "H": "heavy", "O": "organic"}


def trapezoid(
    x: pd.Series | np.ndarray, amin: float, opmin: float, opmax: float, amax: float
) -> np.ndarray:
    """0 outside [amin, amax], 1 inside [opmin, opmax], linear between (EcoCrop)."""
    x = np.asarray(x, dtype=float)
    rise = np.clip((x - amin) / max(opmin - amin, 1e-9), 0, 1)
    fall = np.clip((amax - x) / max(amax - opmax, 1e-9), 0, 1)
    return np.minimum(rise, fall)


def usda_texture_group(sand: pd.Series, clay: pd.Series) -> pd.Series:
    """ECOCROP texture group from sand and clay (%), via the USDA texture triangle.

    light: sand, loamy sand, sandy loam; heavy: any class with 27% clay or more except sandy
    clay loam (clay loam, silty clay loam, sandy clay, silty clay, clay); medium: the rest.
    Sandy loam bound: clay < 20% and sand >= 52% (or clay < 7%, silt < 50%, sand >= 43%).
    """
    silt = 100 - sand - clay
    light = ((clay < 20) & (sand >= 52)) | ((clay < 7) & (silt < 50) & (sand >= 43))
    sandy_clay_loam = (clay >= 20) & (clay < 35) & (sand > 45) & (silt < 28)
    heavy = (clay >= 27) & ~sandy_clay_loam
    return pd.Series(np.select([light, heavy], ["light", "heavy"], "medium"), index=sand.index)


def texture_fit(group: pd.Series, optimal: str, absolute: str) -> pd.Series:
    """'optimal', 'tolerated' or 'outside' for each LGA's texture group. 'W' means any."""

    def allowed(codes: str) -> set[str]:
        letters = codes.split()
        return (
            set(TEXTURE_CODES.values())
            if "W" in letters
            else {TEXTURE_CODES[c] for c in letters if c in TEXTURE_CODES}
        )

    opt, ok = allowed(optimal), allowed(absolute) | allowed(optimal)
    return pd.Series(
        np.select([group.isin(opt), group.isin(ok)], ["optimal", "tolerated"], "outside"),
        index=group.index,
    )


def season_windows(monthly: pd.DataFrame, months: int) -> pd.DataFrame:
    """Every start month and year: mean of the coldest/hottest month and season rain total.

    monthly needs lga, year, month, tmean_c, rain_mm, sorted. A season starting late in the
    year runs into the next year.
    """
    m = monthly.sort_values(["lga", "year", "month"]).reset_index(drop=True)
    g = m.groupby("lga")
    roll = lambda col, fn: getattr(g[col].rolling(months, min_periods=months), fn)()  # noqa: E731
    out = m[["lga", "year", "month"]].copy()
    out["t_min_month"] = roll("tmean_c", "min").reset_index(level=0, drop=True)
    out["t_max_month"] = roll("tmean_c", "max").reset_index(level=0, drop=True)
    out["rain_mm"] = roll("rain_mm", "sum").reset_index(level=0, drop=True)
    # rolling windows end at `month`; shift labels to the start month
    end = pd.to_datetime(dict(year=out["year"], month=out["month"], day=1))
    start = end - pd.DateOffset(months=months - 1)
    return out.assign(year=start.dt.year, month=start.dt.month).dropna()


def climate_scores(windows: pd.DataFrame, crop: pd.Series) -> pd.DataFrame:
    """EcoCrop temperature, rain and overall score for each LGA, year and start month."""
    t_low = trapezoid(windows["t_min_month"], crop.TMIN, crop.TOPMN, crop.TOPMX, crop.TMAX)
    t_high = trapezoid(windows["t_max_month"], crop.TMIN, crop.TOPMN, crop.TOPMX, crop.TMAX)
    rain = trapezoid(windows["rain_mm"], crop.RMIN, crop.ROPMN, crop.ROPMX, crop.RMAX)
    temp = np.minimum(t_low, t_high)
    return windows.assign(
        temp_score=temp,
        rain_score=rain,
        score=np.minimum(temp, rain),
        heat_beyond_optimum=windows["t_max_month"] > crop.TOPMX,
        rain_below_optimum=windows["rain_mm"] < crop.ROPMN,
    )


def best_start(scores: pd.DataFrame) -> pd.DataFrame:
    """Per LGA: the start month with the highest mean score over years, and its record."""
    by_month = scores.groupby(["lga", "month"])[
        ["score", "temp_score", "rain_score", "heat_beyond_optimum", "rain_below_optimum"]
    ].mean()
    best = by_month["score"].groupby(level="lga").idxmax()
    picked = by_month.loc[best.to_list()].reset_index()
    return picked.rename(
        columns={
            "month": "best_start_month",
            "score": "mean_score",
            "heat_beyond_optimum": "heat_share",
            "rain_below_optimum": "rain_short_share",
        }
    )


def share_suitable(scores: pd.DataFrame, best: pd.DataFrame, cut: float) -> pd.Series:
    """Per LGA: share of years whose season, started in the best month, scores at least cut."""
    at_best = scores.merge(
        best[["lga", "best_start_month"]],
        left_on=["lga", "month"],
        right_on=["lga", "best_start_month"],
    )
    return (at_best["score"] >= cut).groupby(at_best["lga"]).mean().rename("share_years_suitable")


def soil_fit(isda: pd.DataFrame, crop: pd.Series) -> pd.DataFrame:
    """pH score (EcoCrop trapezoid on the LGA's cropland mean pH, 0-20 cm) and texture fit.

    ph_outside is True only when even the LGA's 10th or 90th percentile cropland pH lies beyond
    the crop's absolute limit, so a mean just past a limit does not rule a crop out where part
    of the cropland suits it.
    """
    ph = isda["ph__0_20__value_mean"]
    p10, p90 = isda["ph__0_20__value_p10"], isda["ph__0_20__value_p90"]
    group = usda_texture_group(
        isda["sand_content__0_20__value_mean"], isda["clay_content__0_20__value_mean"]
    )
    return pd.DataFrame(
        {
            "ph": ph,
            "ph_p10": p10,
            "ph_p90": p90,
            "ph_score": trapezoid(ph, crop.PHMIN, crop.PHOPMN, crop.PHOPMX, crop.PHMAX),
            "ph_outside": (p90 < crop.PHMIN) | (p10 > crop.PHMAX),
            "texture_group": group,
            "texture_fit": texture_fit(group, str(crop.TEXT), str(crop.TEXTR)),
        },
        index=isda.index,
    )


def crop_suitability(
    monthly: pd.DataFrame,
    isda: pd.DataFrame,
    ecocrop: pd.DataFrame,
    season_days: dict[str, float],
    cut: float,
) -> pd.DataFrame:
    """One row per LGA and crop: climate scores at the best start month, and soil fit.

    season_days gives each crop's growing period; crops missing from it use the midpoint of
    ECOCROP's range. ecocrop is indexed by crop key.
    """
    parts = []
    for crop in ecocrop.index:
        req = ecocrop.loc[crop]
        days = season_days.get(crop, (req.GMIN + req.GMAX) / 2)
        months = max(1, int(round(days / 30.4)))
        scores = climate_scores(season_windows(monthly, months), req)
        best = best_start(scores)
        best = best.join(share_suitable(scores, best, cut), on="lga")
        soil = soil_fit(isda, req).reset_index(names="lga")
        parts.append(best.merge(soil, on="lga").assign(crop=crop, season_months=months))
    return pd.concat(parts, ignore_index=True)
