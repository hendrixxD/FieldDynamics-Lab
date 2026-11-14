"""Weather risk lines for the cards (docs/pest-risk.md, feature 2): computed, never scored.

Two lines, each the share of past seasons in which the condition happened, per LGA and crop:

- humid spell: a run of days during the crop's season with relative humidity at or above the
  LGA's own 90th percentile for that month and a warm mean temperature. Leaf blights are favoured
  by warm, humid weather; a fixed humidity threshold would fire every rainy season in Kaduna, so
  the threshold is relative to each LGA's normal.
- late dry spell: a long run of dry days in the weeks before harvest, when drought raises
  aflatoxin in groundnut.

Season windows (plant and harvest dates per LGA, year and crop) come from A9.
"""

import pandas as pd


def season_days(daily: pd.DataFrame, windows: pd.DataFrame) -> pd.DataFrame:
    """Daily rows inside each crop season window (lga, year, crop, plant_date, harvest_date)."""
    daily = daily.assign(year=daily["date"].dt.year)
    days = windows.merge(daily, on=["lga", "year"])
    inside = days["date"].between(days["plant_date"], days["harvest_date"])
    return days[inside].sort_values(["lga", "crop", "year", "date"]).reset_index(drop=True)


def longest_run(flag: pd.Series, keys: list[pd.Series]) -> pd.Series:
    """Longest run of True per group, without looping over rows (flag must be in date order)."""
    breaks = (~flag).groupby(keys).cumsum()
    runs = flag.groupby([*keys, breaks]).cumsum()
    return runs.groupby(keys).max()


def add_humid_flag(
    daily: pd.DataFrame, rh_quantile: float, t_low: float, t_high: float
) -> pd.DataFrame:
    """Flag days at or above the LGA-month RH quantile (all years) with mean T in the band."""
    month = daily["date"].dt.month
    limit = daily.groupby([daily["lga"], month])["RH2M"].transform("quantile", rh_quantile)
    return daily.assign(humid=(daily["RH2M"] >= limit) & daily["T2M"].between(t_low, t_high))


def add_late_window(windows: pd.DataFrame, weeks: int) -> pd.DataFrame:
    """Narrow each season window to its last `weeks` weeks before harvest."""
    start = windows["harvest_date"] - pd.Timedelta(weeks=weeks)
    return windows.assign(
        plant_date=start.where(start > windows["plant_date"], windows["plant_date"])
    )


def share_of_seasons(longest: pd.Series, min_days: int, windows: pd.DataFrame) -> pd.DataFrame:
    """Per LGA and crop (and season type): share of seasons with a run of at least min_days."""
    hit = (longest >= min_days).rename("hit").reset_index()
    hit = hit.merge(windows[["lga", "year", "crop", "season_type"]], on=["lga", "year", "crop"])
    overall = hit.groupby(["lga", "crop"])["hit"].mean().rename("share_all_years")
    by_type = hit.dropna(subset=["season_type"]).pivot_table(
        index=["lga", "crop"], columns="season_type", values="hit", aggfunc="mean"
    )
    return pd.concat([overall, by_type.add_prefix("share_")], axis=1).reset_index()
