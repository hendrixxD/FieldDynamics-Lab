"""A3. Climate indicators per LGA and calendar month (PRD A3): long-term means and event shares.

The pattern panel (F3e) reads these: how hot, how stormy and how dry each month usually is in
each LGA, and in how many years each kind of event happened. Thresholds: config/models.yaml.
"""

import pandas as pd


def monthly_means(month: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Long-term mean of each column per LGA and calendar month, ignoring missing months."""
    return month.groupby(["lga", "month"])[columns].mean().add_suffix("_mean")


def event_shares(month: pd.DataFrame, events: dict[str, str]) -> pd.DataFrame:
    """Share of years in which the month held at least one event day, per LGA and month.

    events maps an indicator name to the column counting its days. Years where that column is
    missing do not count toward the share.
    """
    shares = {}
    for name, column in events.items():
        has_event = (month[column] >= 1).where(month[column].notna())
        shares[f"{name}_share_years"] = has_event.groupby([month["lga"], month["month"]]).mean()
    return pd.DataFrame(shares)


def dry_soil_share(month: pd.DataFrame, threshold_z: float) -> pd.Series:
    """Share of years with the SMAP soil moisture anomaly below threshold_z, per LGA and month."""
    dry = (month["smap_anomaly_z"] < threshold_z).where(month["smap_anomaly_z"].notna())
    return dry.groupby([month["lga"], month["month"]]).mean().rename("soil_dry_share_years")


def record_years(month: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """How many years of data sit behind each LGA-month, per source column."""
    return month.groupby(["lga", "month"])[columns].count().add_suffix("_years")


def indicator_table(
    month: pd.DataFrame, means: list[str], events: dict[str, str], smap_dry_z: float
) -> pd.DataFrame:
    """The A3 table: one row per LGA and calendar month."""
    parts = [
        monthly_means(month, means),
        event_shares(month, events),
        dry_soil_share(month, smap_dry_z),
        record_years(month, sorted(set(events.values()) | {"smap_anomaly_z"})),
    ]
    return pd.concat(parts, axis=1).reset_index()
