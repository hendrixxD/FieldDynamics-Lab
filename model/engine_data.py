"""Everything the rotation engine reads, loaded once from the precomputed outputs.

No live calls: the engine scores a request from these tables only (PRD section 11,
Reliability). LGA names are mapped to the feature tables' spelling on load.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel

from config import CONFIG_DIR
from model.settings import CropConfig, ModelConfig, load_crop_config, load_model_config
from model.water_pipeline import canonical_lga

SEASON_TYPES = ("Typical", "Late start", "Dry spell", "Short and dry")
STRIGA_SOIL = {  # iSDA 0-20 cm column -> True when a higher value means more Striga risk
    "sand_content__0_20__value_mean": True,
    "nitrogen_total__0_20__value_mean": False,
    "carbon_organic__0_20__value_mean": False,
}


class Template(BaseModel):
    name: str
    seasons: list[list[str]]
    evidence: str
    status: str
    best_fit: list[str]


class RotationConfig(BaseModel):
    templates: dict[str, Template]
    zone_crops: dict[str, list[str]]


@dataclass(frozen=True)
class EngineData:
    cfg: ModelConfig
    crops: CropConfig
    rotations: RotationConfig
    water: pd.DataFrame  # A9 per LGA, crop and season type: short_share, fits_season_share
    land: pd.DataFrame  # A10 per LGA
    confidence: pd.DataFrame  # A12 per LGA
    striga_soil: pd.Series  # 0-2 soil base per LGA
    dry_spell_share: pd.Series  # share of years with a 10+ day dry spell, per LGA
    zone_of: dict[str, str]
    type_share: pd.DataFrame  # A5: share of years in each season type, per LGA
    heat_share: pd.DataFrame  # A3: share of years with a heat day, LGA x month
    onset_doy: pd.Series  # A2: median rain start day, per LGA
    need: pd.DataFrame  # 0-2 per LGA and row: how much this LGA's data says the row matters
    water_lookup: dict[tuple[str, str, str], tuple[float, float]]  # fast A9 access
    prices: pd.DataFrame  # KDBS retail price per mudu, LGA x crop
    risk_lines: pd.DataFrame  # A14: share of seasons with each weather risk, per LGA and crop
    suitability: pd.DataFrame  # A15: EcoCrop climate and soil fit, indexed by LGA and crop
    suit_lookup: dict[tuple[str, str], dict]  # fast A15 access
    season_days: dict[str, float]  # days in the ground per crop (FAO-56, else ECOCROP)
    type_share_lookup: dict[str, dict[str, float]]  # A5 shares as plain dicts


def load_rotations(path: Path = CONFIG_DIR / "rotations.yaml") -> RotationConfig:
    """Read and validate config/rotations.yaml."""
    return RotationConfig.model_validate(yaml.safe_load(path.read_text()))


def tercile_score(values: pd.Series, higher_is_worse: bool) -> pd.Series:
    """0, 1 or 2 by Kaduna tercile; 2 is the worst third for Striga."""
    ranks = values.rank(pct=True, ascending=higher_is_worse)
    return pd.Series(np.select([ranks > 2 / 3, ranks > 1 / 3], [2, 1], 0), index=values.index)


def striga_soil_base(isda: pd.DataFrame) -> pd.Series:
    """Rounded mean of the sand, total N and organic C tercile scores (docs/pest-risk.md)."""
    scores = pd.concat(
        [tercile_score(isda[col], worse) for col, worse in STRIGA_SOIL.items()], axis=1
    )
    return scores.mean(axis=1).round().astype(int)


def lga_need(
    isda: pd.DataFrame, land: pd.DataFrame, spells: pd.Series, striga: pd.Series
) -> pd.DataFrame:
    """0-2 per LGA for the rows the data can rank: the worse third of Kaduna scores 2.

    Nutrients from iSDA total N (low is worse), organic matter from organic C, erosion from the
    A10 index, water from how often long dry spells come (A2), pests from the Striga soil base.
    """
    return pd.DataFrame(
        {
            "nutrients": tercile_score(isda["nitrogen_total__0_20__value_mean"], False),
            "organic_matter": tercile_score(isda["carbon_organic__0_20__value_mean"], False),
            "erosion": tercile_score(land["erosion_index"], True),
            "water": tercile_score(spells, True),
            "pests": striga,
        }
    )


def _indexed(path: Path, lgas: list[str]) -> pd.DataFrame:
    table = pd.read_csv(path)
    return table.assign(lga=canonical_lga(table["lga"], lgas))


def lga_table(isda: pd.DataFrame, land: pd.DataFrame, year: pd.DataFrame) -> pd.DataFrame:
    """Per-LGA inputs the engine reads: Striga soil base, dry-spell share, rain start, need."""
    spells = (year["a2_dry_spells_10plus"] > 0).groupby(year["lga"]).mean()
    striga = striga_soil_base(isda)
    need = lga_need(isda, land, spells, striga).add_prefix("need_")
    return need.assign(
        striga_soil=striga,
        dry_spell_share=spells,
        onset_doy=year.groupby("lga")["a2_onset_doy"].median(),
    ).rename_axis("lga")


def pipeline_tables(cfg: ModelConfig) -> dict[str, pd.DataFrame]:
    """Read the engine's tables from the pipeline outputs (A3 to A15, iSDA, KDBS)."""
    out, lgas = cfg.paths.output_dir, sorted(cfg.lga_zone)
    isda = _indexed(cfg.paths.isda_stats, lgas).set_index("lga")
    year = pd.read_parquet(cfg.paths.annual_features)
    year = year.assign(lga=canonical_lga(year["lga"], lgas))
    land = _indexed(out / "a10" / "a10_lga_land_risk.csv", lgas).set_index("lga")
    a3 = _indexed(out / "a3" / "a3_lga_month_indicators.csv", lgas)
    kdbs = _indexed(cfg.paths.kdbs_prices, lgas).dropna(subset=["crop"])
    return {
        "water": _indexed(out / "a9" / "a9_lga_crop_season_type.csv", lgas),
        "land": land,
        "confidence": _indexed(out / "a12" / "a12_lga_confidence.csv", lgas).set_index("lga"),
        "lga": lga_table(isda, land, year),
        "type_share": _indexed(out / "a5" / "a5_lga_type_share.csv", lgas).set_index("lga"),
        "heat_share": a3.pivot(index="lga", columns="month", values="heat_share_years"),
        "prices": kdbs.pivot(index="lga", columns="crop", values="price_ngn"),
        "risk_lines": pd.read_csv(cfg.paths.risk_lines),
        "suitability": pd.read_csv(cfg.paths.suitability),
    }


def assemble(cfg: ModelConfig, t: dict[str, pd.DataFrame]) -> EngineData:
    """Build the engine's data object, with its fast lookups, from the tables."""
    crops, suit, water, lga = load_crop_config(), t["suitability"], t["water"], t["lga"]
    need = lga.filter(like="need_").rename(columns=lambda c: c.removeprefix("need_"))
    return EngineData(
        cfg=cfg,
        crops=crops,
        rotations=load_rotations(),
        water=water.set_index(["lga", "crop", "season_type"]).sort_index(),
        land=t["land"],
        confidence=t["confidence"],
        striga_soil=lga["striga_soil"].astype(int),
        dry_spell_share=lga["dry_spell_share"],
        zone_of=cfg.lga_zone,
        type_share=t["type_share"],
        type_share_lookup=t["type_share"].to_dict("index"),
        heat_share=t["heat_share"].rename(columns=int),
        onset_doy=lga["onset_doy"],
        need=need,
        water_lookup={
            (r.lga, r.crop, r.season_type): (float(r.short_share), float(r.fits_season_share))
            for r in water.itertuples()
        },
        prices=t["prices"],
        risk_lines=t["risk_lines"].set_index(["lga", "crop", "line"]),
        suitability=suit.set_index(["lga", "crop"]).sort_index(),
        suit_lookup={(r["lga"], r["crop"]): r for r in suit.to_dict("records")},
        season_days={
            **{c: m * 30.4 for c, m in suit.groupby("crop")["season_months"].first().items()},
            **{k: float(c.stages_days.total) for k, c in crops.crops.items()},
        },
    )


def load_engine_data(cfg: ModelConfig | None = None) -> EngineData:
    """Read the engine's data from the pipeline outputs (notebooks and tests)."""
    cfg = cfg or load_model_config()
    return assemble(cfg, pipeline_tables(cfg))
