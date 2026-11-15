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


class LgaItem(BaseModel):
    """Summary of one Kaduna LGA for dropdowns and the trend map."""

    lga: str
    zone: str
    cropland_share: float
    cropland_pct: float
    low_cropland: bool
    soil_default: str
    soil_ph: float | None
    onset_label: str
    cessation_label: str
    length_days: int
    erosion_band: str
    confidence_level: str
    confidence_reason: str


class LgaListResponse(BaseModel):
    lgas: list[LgaItem]


class MapTrendItem(LgaItem):
    annual_rain_mm: int
    heat_days_yr: float
    onset_slope_days_dec: float
    onset_significant: bool
    heat_slope_days_dec: float
    heat_significant: bool
    erosion_index: float
    slope_pct: float
    imerg_p95_mm: float
    july_dry_soil_pct: int


class MapTrendsResponse(BaseModel):
    trends: dict[str, MapTrendItem]


class LegendStop(BaseModel):
    """Single color stop along a layer's legend scale."""

    value: float
    label: str
    colour: str


class LayerLegend(BaseModel):
    """Specification of a map analysis layer's color scale, limits, and units."""

    layer: str
    label: str
    unit: str
    value_min: float
    value_max: float
    native_resolution: str
    colours: list[str]
    stops: list[LegendStop]


class LegendListResponse(BaseModel):
    """All 4 analysis layer legends defined in config/layers.yaml."""

    layers: dict[str, LayerLegend]


class PointLayerValues(BaseModel):
    """Agroclimatic indicators for one geographic coordinate."""

    soil_moisture_z: float
    heat_days_yr: float
    erosion_index: float
    water_stress_pct: float
    annual_rain_mm: int
    imerg_p95_mm: float


class PointQueryResponse(BaseModel):
    """Response returned when a farmer taps the map or queries a specific GPS coordinate."""

    lat: float
    lon: float
    lga: str
    zone: str
    cropland_share: float
    cropland_pct: float
    soil_default: str
    soil_ph: float | None
    onset_label: str
    cessation_label: str
    length_days: int
    erosion_band: str
    confidence_level: str
    confidence_reason: str
    layers: PointLayerValues
