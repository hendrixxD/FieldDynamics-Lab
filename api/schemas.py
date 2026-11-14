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


class CropFact(BaseModel):
    """One crop on a card: its water need under the chosen season type and N fixing (R4)."""

    crop: str
    fixes_nitrogen: bool
    water_need_mm: float | None
    short_share: float | None


class Risk(BaseModel):
    """A risk line and who to check it with (R12)."""

    text: str
    check_with: str


class Opportunity(BaseModel):
    """One of the four opportunities (R19) and the row reason behind it."""

    name: str
    reason: str


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
    crops: list[CropFact]
    water_line: str
    why: str
    benefits: list[str]
    studies: list[Study]
    source: Literal["current", "data"]
    confidence: Confidence
    risks: list[Risk]
    labour: str
    gaps: list[str]
    trend_line: str
    check_first: bool
    opportunities: list[Opportunity] = []


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
    notice: str | None = None
