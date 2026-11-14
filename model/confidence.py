"""A12. Confidence per LGA (PRD A12): how much the data behind an LGA's cards can be trusted.

Each input scores 0 (weak) to 2 (strong). This module scores the LGA-level inputs: cropland
share, gap-filled vegetation data, soil map uncertainty, and the vegetation stress evidence
(scored 0 while A6 has no skill). The rotation engine adds the evidence strength of each template
and applies the levels. Cut-offs: config/models.yaml.
"""

import numpy as np
import pandas as pd


def score_higher_better(values: pd.Series, cuts: tuple[float, float]) -> pd.Series:
    """0 below cuts[0], 1 between, 2 at or above cuts[1]."""
    return pd.Series(np.select([values < cuts[0], values < cuts[1]], [0, 1], 2), index=values.index)


def score_lower_better(values: pd.Series, cuts: tuple[float, float]) -> pd.Series:
    """0 above cuts[0], 1 between, 2 at or below cuts[1] (cuts[0] > cuts[1])."""
    return pd.Series(np.select([values > cuts[0], values > cuts[1]], [0, 1], 2), index=values.index)


def soil_relative_width(isda: pd.DataFrame, properties: list[str], layer: str) -> pd.Series:
    """Mean over properties of iSDA's (upper - lower) / value at one layer: the soil map's own
    uncertainty, per LGA."""
    widths = [
        (isda[f"{p}__{layer}__upper_mean"] - isda[f"{p}__{layer}__lower_mean"])
        / isda[f"{p}__{layer}__value_mean"]
        for p in properties
    ]
    return pd.concat(widths, axis=1).mean(axis=1)


def level(total: pd.Series, levels: dict[str, int]) -> pd.Series:
    """High, Medium or Low from a total score."""
    return pd.Series(
        np.select([total >= levels["High"], total >= levels["Medium"]], ["High", "Medium"], "Low"),
        index=total.index,
    )


REASONS = {
    "cropland": "little cropland is mapped here",
    "vegetation_data": "many vegetation readings were cloud-filled",
    "soil": "the satellite soil map is uncertain here",
    "vegetation_stress": "satellite greenness could not predict crop stress",
}


def weakest_reason(scores: pd.DataFrame) -> pd.Series:
    """Plain reason naming the weakest inputs (score 0), for the card's confidence line.

    Built column by column (four columns), never row by row.
    """
    text = pd.Series("", index=scores.index)
    for key, reason in REASONS.items():
        weak = scores[key].eq(0)
        text = text.where(~weak, text + np.where(text.eq(""), "", "; ") + reason)
    return text.replace("", "none")
