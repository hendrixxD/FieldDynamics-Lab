"""Whole-system backtest (PRD R33; docs/model-evaluation.md, "Whole system").

For every LGA-year the card's warning is rebuilt as the engine would have shown it for that
year's season type, but from the other years only (leave one year out), and compared with what
happened. Two outcomes:

- the crop did not finish inside that year's rainy season (A9 on that year's CHIRPS rain).
  The season type is built from the same year's onset and season length, so this outcome is
  partly circular: it tests that the card carries the type's risk, not that the type is forecast;
- observed cropland greenness was stressed (MODIS, independent of the weather inputs).

The baseline is the same warning with no season type: the share over all other years.
"""

import numpy as np
import pandas as pd


def loyo_share(rows: pd.DataFrame, value: str, by: list[str]) -> pd.Series:
    """Mean of a 0/1 column within each group, leaving out the row's own year.

    rows has one row per LGA-year (and crop). Returns NaN where a group has no other year.
    """
    group = rows.groupby(by)[value]
    total, count = group.transform("sum"), group.transform("count")
    own = rows[value].astype(float)
    return (total - own) / (count - 1).replace(0, np.nan)


def warned(fit_share: pd.Series, risk_fit_share: float) -> pd.Series:
    """The card warns (risk line, or the crop is dropped) when the fit share is below the
    engine's risk threshold. NaN fit shares give no warning."""
    return fit_share < risk_fit_share


def hit_rates(warning: pd.Series, bad: pd.Series) -> dict[str, float]:
    """Share of bad years warned of (hit rate), share of good years warned of (false alarm),
    and how much likelier a bad year is when warned than when not (lift)."""
    warning, bad = warning.astype(bool), bad.astype(bool)
    hit = float(warning[bad].mean()) if bad.any() else float("nan")
    false_alarm = float(warning[~bad].mean()) if (~bad).any() else float("nan")
    bad_if_warned = float(bad[warning].mean()) if warning.any() else float("nan")
    bad_if_not = float(bad[~warning].mean()) if (~warning).any() else float("nan")
    return {
        "lga_years": int(len(bad)),
        "bad_years": int(bad.sum()),
        "hit_rate": hit,
        "false_alarm_rate": false_alarm,
        "bad_share_if_warned": bad_if_warned,
        "bad_share_if_not_warned": bad_if_not,
        "lift": bad_if_warned / bad_if_not if bad_if_not else float("nan"),
    }


def backtest_table(rows: pd.DataFrame, risk_fit_share: float) -> pd.DataFrame:
    """Per LGA-year and crop: the leave-one-year-out fit share with and without the season type,
    and whether each would have warned. rows needs lga, year, crop, season_type, fits_season."""
    out = rows.dropna(subset=["season_type"]).copy()
    out["fits"] = out["fits_season"].astype(float)
    out["fit_share_type"] = loyo_share(out, "fits", ["lga", "crop", "season_type"])
    out["fit_share_all"] = loyo_share(out, "fits", ["lga", "crop"])
    out["warned_type"] = warned(out["fit_share_type"], risk_fit_share)
    out["warned_all"] = warned(out["fit_share_all"], risk_fit_share)
    out["failed"] = ~out["fits_season"].astype(bool)
    return out
