"""A10. Erosion risk and infiltration potential per LGA (PRD A10).

Erosion risk multiplies the three things that move soil: how steep the cropland is (SRTM), how
hard it rains (IMERG 95th percentile wet-day rain) and how erodible the soil is (Williams K,
relative to the Kaduna mean). The product is scaled 0 to 1 across LGAs, so it ranks LGAs against
each other; it is not a soil loss in tonnes. Infiltration comes from saturated conductivity and
the hydrologic soil group (A8, NEH 630 Ch. 7).
"""

import numpy as np
import pandas as pd


def min_max(values: pd.Series) -> pd.Series:
    """Scale to 0-1 across LGAs; a constant series becomes 0.5."""
    span = values.max() - values.min()
    if span == 0:
        return pd.Series(0.5, index=values.index)
    return (values - values.min()) / span


def erosion_index(
    slope_pct: pd.Series, rain_intensity_mm: pd.Series, erodibility_rel: pd.Series
) -> pd.Series:
    """Relative erosion risk, 0 (lowest in Kaduna) to 1 (highest): slope x intensity x K."""
    return min_max(slope_pct * rain_intensity_mm * erodibility_rel)


def band(index: pd.Series, edges: tuple[float, float]) -> pd.Series:
    """Low, Medium or High from a 0-1 index."""
    return pd.Series(
        np.select([index < edges[0], index < edges[1]], ["Low", "Medium"], "High"),
        index=index.index,
    )


def term_shares(
    slope_pct: pd.Series, rain_intensity_mm: pd.Series, erodibility_rel: pd.Series
) -> pd.DataFrame:
    """Which term drives each LGA's erosion risk: each term's log-departure from the Kaduna
    median, as shares of the total departure (a product's terms add in logs)."""
    logs = pd.DataFrame(
        {
            "slope": np.log(slope_pct),
            "rain_intensity": np.log(rain_intensity_mm),
            "erodibility": np.log(erodibility_rel),
        }
    )
    departure = (logs - logs.median()).abs()
    return departure.div(departure.sum(axis=1).replace(0, np.nan), axis=0)
