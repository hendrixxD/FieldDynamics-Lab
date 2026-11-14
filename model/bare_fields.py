"""A4. Bare-field detection (PRD A4): which months cropland is usually bare, smoothed against raw.

Cropland NDVI composites (16-day MODIS, data/ndvi_gee.py) are cloud-noisy. The PRD method smooths
them with Savitzky-Golay (window 5, order 2) before thresholding; the baseline thresholds the raw
series. Better detection means fewer false flips (bare to green and back inside a year) and
closer agreement with the known dry season.
"""

import numpy as np
import pandas as pd


def relative_threshold(ndvi: pd.Series, lga: pd.Series, fraction: float) -> pd.Series:
    """Per-LGA bare threshold: 5th percentile + fraction x (95th - 5th) of that LGA's series."""
    q = ndvi.groupby(lga).quantile([0.05, 0.95]).unstack()
    per_lga = q[0.05] + fraction * (q[0.95] - q[0.05])
    return lga.map(per_lga)


def flips_per_year(bare: pd.Series, lga: pd.Series, year: pd.Series) -> pd.Series:
    """Number of bare/green switches between consecutive composites, per LGA-year.

    A real year switches about twice (green-up and harvest); extra switches are noise.
    """
    frame = pd.DataFrame({"bare": bare.astype(int), "lga": lga, "year": year})
    switched = frame.groupby(["lga", "year"])["bare"].diff().abs()
    return switched.groupby([frame["lga"], frame["year"]]).sum()


def dry_season_agreement(bare: pd.Series, month: pd.Series, dry_months: list[int]) -> float:
    """Share of composites whose bare flag matches the known season (bare in dry months only)."""
    expected = month.isin(dry_months)
    return float((bare.astype(bool) == expected).mean())


def bare_probability(
    bare: pd.Series, lga: pd.Series, year: pd.Series, month: pd.Series
) -> pd.DataFrame:
    """Per LGA, the share of years each calendar month was bare (a month is bare when most of
    its composites are bare). Columns m01 to m12."""
    frame = pd.DataFrame({"bare": bare.astype(float), "lga": lga, "year": year, "month": month})
    monthly = frame.groupby(["lga", "year", "month"])["bare"].mean() > 0.5
    prob = monthly.groupby(["lga", "month"]).mean().unstack("month")
    prob.columns = [f"m{int(m):02d}" for m in prob.columns]
    return prob


def method_scores(
    table: pd.DataFrame, flags: dict[str, str], dry_months: list[int]
) -> pd.DataFrame:
    """Flips per year and dry-season agreement for each bare-flag column in table."""
    rows = []
    for name, column in flags.items():
        flips = flips_per_year(table[column], table["lga"], table["year"])
        rows.append(
            {
                "method": name,
                "flips_per_year_mean": float(flips.mean()),
                "flips_per_year_median": float(np.median(flips)),
                "dry_season_agreement": dry_season_agreement(
                    table[column], table["month"], dry_months
                ),
            }
        )
    return pd.DataFrame(rows)
