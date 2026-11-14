"""Response bodies for the JSON routes. The request body for /api/score is model/'s Request."""

from typing import Literal

from pydantic import BaseModel

Level = Literal["Low", "Medium", "High"]


class Row(BaseModel):
    """One scored line on a card (PRD section 9): None when the data cannot score it."""

    score: float | None
    level: str
    arrow: Literal["better", "worse", "same"]
    reason: str


class Study(BaseModel):
    key: str
    evidence: str
    status: str
    zones: list[str]


class Confidence(BaseModel):
    level: Level
    reason: str


class Card(BaseModel):
    """Current, Option A or Option B."""

    key: str
    badge: Literal["Current", "Option A", "Option B"]
    title: str
    seasons: list[list[str]]
    rows: dict[str, Row]
    total: float | None
    rows_scored: int
    studies: list[Study]
    source: Literal["current", "data"]
    confidence: Confidence
    risks: list[str]
    labour: str
    gaps: list[str]
    check_first: bool
    opportunities: list[str] = []


class StrigaRisk(BaseModel):
    level: Level
    reason: str


class ScoreResponse(BaseModel):
    """What /api/score returns: the cards plus why other rotations were left out."""

    lga: str
    zone: str
    season_type: str
    striga_risk: StrigaRisk
    weights: dict[str, float]
    cards: list[Card]
    close_call: bool
    dropped: dict[str, str]
    low_cropland: bool
