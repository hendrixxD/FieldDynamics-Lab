"""Validated settings for the models (config/models.yaml) and crops (config/crops.yaml)."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, PositiveInt

from config import CONFIG_DIR


class Years(BaseModel):
    start: int
    end: int


class SeasonTypeConfig(BaseModel):
    features: dict[str, str]
    k_candidates: list[PositiveInt]
    k: PositiveInt
    n_init: PositiveInt
    stability_seeds: PositiveInt
    permutation_runs: PositiveInt
    name_min_z: float
    profile_min_years: PositiveInt


class Window(BaseModel):
    column: str
    months: list[int] = Field(min_length=1)


class AlphaGrid(BaseModel):
    log10_min: float
    log10_max: float
    count: PositiveInt


class StressConfig(BaseModel):
    target: str
    weather_features: dict[str, str]
    static_features: dict[str, str]
    windows: dict[str, Window]
    tmax_mid_months: list[int]
    lightgbm: dict[str, float | int]
    early_stopping_rounds: PositiveInt
    early_stopping_min_trees: PositiveInt
    early_stopping_year_share: float = Field(gt=0, lt=1)
    ridge_alphas: AlphaGrid
    quantiles: list[float]
    optuna_trials: PositiveInt
    nested_optuna_trials: PositiveInt
    shuffle_runs: PositiveInt
    bootstrap_runs: PositiveInt
    permutation_repeats: PositiveInt
    min_skill: float
    scenario_min_years: PositiveInt

    @property
    def labels(self) -> dict[str, str]:
        """Plain labels for every feature, weather and static."""
        return {**self.weather_features, **self.static_features}


class PAdjust(BaseModel):
    slope: float
    reference_etc_mm_day: float
    min: float
    max: float


class KcClimate(BaseModel):
    wind_ref_m_s: float
    rhmin_ref_pct: float
    wind_coef: float
    rh_coef: float
    height_exponent: float
    kc_end_min_to_adjust: float
    wind_range_m_s: tuple[float, float]
    rhmin_range_pct: tuple[float, float]


class WaterBalanceConfig(BaseModel):
    years: Years
    root_depth_initial_m: float = Field(gt=0)
    topsoil_depth_m: float = Field(gt=0)
    p_adjust: PAdjust
    kc_climate: KcClimate
    shortfall_share: float = Field(gt=0, lt=1)
    stress_ks: float
    smap_years: Years
    smap_check_crop: str


class SoilWaterConfig(BaseModel):
    methods: list[str]
    layers_cm: dict[str, tuple[float, float]]
    reference_depths_cm: list[float]


class ClimateIndicatorConfig(BaseModel):
    years: Years
    events: dict[str, str]
    means: list[str]
    smap_dry_z: float


class BareFieldConfig(BaseModel):
    dry_season_months: list[int]
    absolute_threshold: float
    relative_fraction: float = Field(gt=0, lt=1)
    years: Years


class TrendConfig(BaseModel):
    years: Years
    series: dict[str, str]
    alpha: float = Field(gt=0, lt=1)


class LandRiskConfig(BaseModel):
    slope_column: str
    intensity_column: str
    erodibility_column: str
    ksat_column: str
    group_column: str
    band_edges: tuple[float, float]


class ConfidenceConfig(BaseModel):
    cropland_share: tuple[float, float]
    ndvi_interpolated_share: tuple[float, float]
    soil_relative_width: tuple[float, float]
    vegetation_stress: int = Field(ge=0, le=2)
    levels: dict[str, int]


class BuildConfig(BaseModel):
    notebooks: list[str]
    timeout_s: PositiveInt


class Paths(BaseModel):
    features_dir: Path
    output_dir: Path
    experiment_log: Path
    rain_daily: Path
    power_daily: Path
    onset: Path
    soil: Path
    smap_daily: Path
    isda_stats: Path
    engine_soil: Path
    openlandmap: Path
    openlandmap_cropland_fc: Path
    kdbs_prices: Path
    risk_lines: Path
    suitability: Path
    notebooks: Path
    annual_features: Path
    monthly_features: Path
    static_features: Path
    ndvi_composites: Path


class EngineWater(BaseModel):
    max_short_share: float
    min_fit_share: float
    risk_fit_share: float
    irrigation_relief: float


class EngineNitrogen(BaseModel):
    base_credit: float
    full_credit_kg_ha: float


class EngineStriga(BaseModel):
    sorghum_season_weight: float
    high_seasons: float
    medium_seasons: float
    high_soil_base: int
    pests_weight_bonus: float


class EngineSensitivity(BaseModel):
    runs: PositiveInt
    max_nudge: float
    swap_share: float


class HumidLine(BaseModel):
    crops: list[str]
    rh_quantile: float = Field(gt=0, lt=1)
    t_mean_c: tuple[float, float]
    min_days: PositiveInt


class LateDryLine(BaseModel):
    crops: list[str]
    weeks_before_harvest: PositiveInt
    dry_day_mm: float
    min_days: PositiveInt


class SuitabilityConfig(BaseModel):
    ecocrop: Path
    ecocrop_source: str
    ecocrop_sha256: str
    ecocrop_names: dict[str, str]
    years: Years
    suitable_score: float
    drop_mean_score: float
    risk_mean_score: float
    risk_share: float


class RiskLineConfig(BaseModel):
    humid: HumidLine
    late_dry: LateDryLine
    min_share: float


class EngineConfig(BaseModel):
    priority_weight: float
    max_priorities: PositiveInt
    water: EngineWater
    nitrogen: EngineNitrogen
    organic_matter: dict[str, float]
    erosion: dict[str, float]
    pests: dict[str, float]
    market: dict[str, float | dict[str, float]]
    striga: EngineStriga
    levels: dict[str, float]
    sensitivity: EngineSensitivity
    scouting: dict[str, str]


class ModelConfig(BaseModel):
    years: Years
    stressed_ndvi_z: float
    random_seed: int
    zones: dict[str, list[str]]
    season_types: SeasonTypeConfig
    stress_model: StressConfig
    water_balance: WaterBalanceConfig
    soil_water: SoilWaterConfig
    climate_indicators: ClimateIndicatorConfig
    bare_fields: BareFieldConfig
    trends: TrendConfig
    land_risk: LandRiskConfig
    confidence: ConfidenceConfig
    build: BuildConfig
    paths: Paths
    suitability: SuitabilityConfig
    risk_lines: RiskLineConfig
    engine: EngineConfig

    @property
    def lga_zone(self) -> dict[str, str]:
        """LGA name to senatorial zone."""
        return {lga: zone for zone, lgas in self.zones.items() for lga in lgas}


class Stages(BaseModel):
    init: PositiveInt
    dev: PositiveInt
    mid: PositiveInt
    late: PositiveInt

    @property
    def total(self) -> int:
        """Season length in days."""
        return self.init + self.dev + self.mid + self.late


class Kc(BaseModel):
    ini: float = Field(gt=0, le=1.5)
    mid: float = Field(gt=0, le=1.5)
    end: float = Field(gt=0, le=1.5)


class Range(BaseModel):
    low: float | None = None
    high: float | None = None
    mean: float | None = None


class Nitrogen(BaseModel):
    """N fixing and the net N left in the soil after the grain is taken away (kg N/ha)."""

    fixes: bool
    net_n_after_grain_kg_ha: Range | None = None
    source: str


class Forage(BaseModel):
    residue: str
    value_class: Literal["high", "low"] | None = None
    share_eaten: float | None = Field(default=None, ge=0, le=1)
    source: str | None = None


class Market(BaseModel):
    role: Literal["food", "cash", "both"] | None = None
    role_source: str | None = None


class Labour(BaseModel):
    days_per_ha: float = Field(gt=0)
    source: str


class CropRules(BaseModel):
    """PRD A11 fields. A value is None until it comes from a named source (AGENTS.md)."""

    family: str
    pest_groups: list[str] = []
    nitrogen: Nitrogen
    forage: Forage | None = None
    market: Market = Market()
    labour: Labour | None = None
    gaps: list[str] = []


class Crop(BaseModel):
    label: str
    stages_days: Stages
    kc: Kc
    height_m: float = Field(gt=0)
    root_depth_max_m: float = Field(gt=0)
    depletion_fraction_p: float = Field(gt=0, lt=1)
    must_fit_season: bool = True
    rules: CropRules


class OtherCrop(BaseModel):
    """A crop with rotation rules but no FAO-56 water fields, so A9 cannot run it."""

    label: str
    why_no_water_balance: str
    rules: CropRules


class CropConfig(BaseModel):
    crops: dict[str, Crop]
    other_crops: dict[str, OtherCrop] = {}
    pest_groups: dict[str, str] = {}
    kdbs_commodities: dict[str, str] = {}
    sources: dict[str, str] = {}

    def rules(self) -> dict[str, CropRules]:
        """A11 rules for every crop in scope, water-balance crops and the rest."""
        return {
            **{k: c.rules for k, c in self.crops.items()},
            **{k: c.rules for k, c in self.other_crops.items()},
        }


def load_crop_config(path: Path = CONFIG_DIR / "crops.yaml") -> CropConfig:
    """Read and validate config/crops.yaml."""
    return CropConfig.model_validate(yaml.safe_load(path.read_text()))


def load_model_config(path: Path = CONFIG_DIR / "models.yaml") -> ModelConfig:
    """Read and validate config/models.yaml."""
    return ModelConfig.model_validate(yaml.safe_load(path.read_text()))
