"""A7. Long-term trends per LGA (PRD A7): Mann-Kendall with Sen's slope, against an OLS baseline.

Mann-Kendall (pymannkendall) tests for a monotonic trend without assuming normal errors; Sen's
slope is the median of pairwise slopes, robust to outlier years. Hamed and Rao (1998) widen the
test's variance when the series is autocorrelated. Testing 23 LGAs x 4 series at once would give
about 5 false "significant" trends by chance at 5 %, so Benjamini and Hochberg (1995) false
discovery control decides the significance flag shown on cards.
"""

import numpy as np
import pandas as pd
import pymannkendall as mk
from scipy import stats


def trend_row(years: np.ndarray, values: np.ndarray) -> dict[str, float | bool]:
    """Sen's slope and Mann-Kendall p (plain and Hamed-Rao), and the OLS slope and p, per year.

    Hamed-Rao's variance can come out negative on short or flat series, which leaves its p
    undefined; the plain test is used there and autocorrelation_corrected says so. The p used
    for the verdict (trend_p) is the larger of the plain and corrected p: the correction exists
    to stop positive autocorrelation from overstating a trend, so it may only make the test
    stricter, never looser.
    """
    keep = np.isfinite(values)
    x, y = years[keep], values[keep]
    if len(y) < 10:
        raise ValueError(f"Too few years for a trend test: {len(y)}")
    plain = mk.original_test(y)
    with np.errstate(invalid="ignore"):
        corrected = mk.hamed_rao_modification_test(y)
    corrected_ok = bool(np.isfinite(corrected.p))
    ols = stats.linregress(x, y)
    return {
        "n_years": len(y),
        "first_year": int(x.min()),
        "last_year": int(x.max()),
        "sen_slope_per_year": float(mk.sens_slope(y).slope),
        "mk_p": float(plain.p),
        "mk_hamed_rao_p": float(corrected.p) if corrected_ok else np.nan,
        "autocorrelation_corrected": corrected_ok,
        "trend_p": max(float(plain.p), float(corrected.p)) if corrected_ok else float(plain.p),
        "ols_slope_per_year": float(ols.slope),
        "ols_p": float(ols.pvalue),
        "mean": float(y.mean()),
    }


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    """False-discovery-adjusted p-values (Benjamini and Hochberg 1995), same order as given.

    Missing p-values stay missing and do not count toward the number of tests.
    """
    valid = p_values.dropna()
    p = valid.to_numpy(dtype=float)
    order = np.argsort(p)
    ranked = p[order] * len(p) / np.arange(1, len(p) + 1)
    adjusted = np.minimum.accumulate(ranked[::-1])[::-1].clip(max=1)
    out = np.empty_like(adjusted)
    out[order] = adjusted
    return pd.Series(out, index=valid.index).reindex(p_values.index)


def trend_table(annual: pd.DataFrame, series: list[str], alpha: float) -> pd.DataFrame:
    """One row per LGA and series: slopes per decade, p-values and the significance flag.

    annual has lga, year and one column per series. A trend counts as significant when its
    trend_p, adjusted for false discoveries across the whole table, is below alpha.
    """
    rows = []
    for (lga, name), group in annual.melt(["lga", "year"], series, "series").groupby(
        ["lga", "series"]
    ):
        group = group.sort_values("year")
        rows.append(
            {
                "lga": lga,
                "series": name,
                **trend_row(group["year"].to_numpy(), group["value"].to_numpy(float)),
            }
        )
    table = pd.DataFrame(rows)
    table["sen_slope_per_decade"] = table["sen_slope_per_year"] * 10
    table["ols_slope_per_decade"] = table["ols_slope_per_year"] * 10
    table["p_adjusted"] = benjamini_hochberg(table["trend_p"])
    table["significant"] = table["p_adjusted"] < alpha
    table["direction"] = np.sign(table["sen_slope_per_year"]).map(
        {1.0: "up", -1.0: "down", 0.0: "flat"}
    )
    return table


def card_line(
    label: str,
    slope_per_decade: float,
    unit: str,
    significant: bool,
    up_word: str = "more",
    down_word: str = "less",
) -> str:
    """Plain trend sentence for a card, e.g. 'Rain start: 4 days later per decade (clear trend)'."""
    word = up_word if slope_per_decade > 0 else down_word
    verdict = "clear trend" if significant else "no clear trend, could be chance"
    return f"{label}: {abs(slope_per_decade):.0f} {unit} {word} per decade ({verdict})"
