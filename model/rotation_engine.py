"""Rotation engine (PRD section 7): three cards from one advisor request.

Rule-based and explainable: every score keeps the reason that produced it, and a dimension
with no sourced data scores None ("not enough information") instead of a guess. Steps:
candidates from evidence, filter to the farm, score seven dimensions, weight by priorities,
mark opportunities, attach confidence and risk lines.
"""

import itertools
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator

from model.engine_data import EngineData

DIMENSIONS = ("nutrients", "organic_matter", "erosion", "pests", "water", "forage", "market")
PRIORITIES = {  # PRD I6 button -> card row
    "save_water": "water",
    "add_nitrogen": "nutrients",
    "build_organic_matter": "organic_matter",
    "stop_erosion": "erosion",
    "feed_animals": "forage",
    "extra_income": "market",
    "fewer_pests": "pests",
}
CEREALS = {"maize", "sorghum", "millet", "rice"}
LEVEL_ORDER = ("Low", "Medium", "High")


class Request(BaseModel):
    """PRD inputs I1 to I8. past_seasons lists the last one or two seasons, oldest first."""

    lga: str
    soil: Literal["Sandy", "Loamy", "Clay", "Not sure"] | None = None
    water: Literal["Rain only", "Irrigation", "Both"] = "Rain only"
    equipment: Literal["Hand tools", "Animal traction", "Tractor"] = "Hand tools"
    past_seasons: list[list[str]] = Field(min_length=1, max_length=2)
    priorities: list[str] = Field(default_factory=list, max_length=3)
    sell: Literal["Local market", "Buyer, processor or feed mill", "Mostly home use"] = (
        "Local market"
    )
    season_type: Literal["Typical", "Late start", "Dry spell", "Short and dry"] = "Typical"

    @field_validator("priorities")
    @classmethod
    def known_priorities(cls, value: list[str]) -> list[str]:
        unknown = set(value) - set(PRIORITIES)
        if unknown:
            raise ValueError(f"unknown priorities: {sorted(unknown)}")
        return value


@dataclass
class Score:
    value: float | None
    reason: str
    gaps: list[str] = field(default_factory=list)


@dataclass
class Rotation:
    key: str
    name: str
    seasons: list[list[str]]
    evidence: str
    status: str
    risks: list[str] = field(default_factory=list)
    relaxed: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
def crops_in(rotation: Rotation) -> list[str]:
    """Distinct crops in rotation order."""
    return list(dict.fromkeys(c for season in rotation.seasons for c in season))


def water_stats(data: EngineData, lga: str, crop: str, season_type: str) -> tuple[float, float]:
    """A9 share of seasons short and share that fit the rainy season; NaN if not modelled."""
    return data.water_lookup.get((lga, crop, season_type), (float("nan"), float("nan")))


def current_rotation(req: Request, data: EngineData) -> Rotation:
    """Card 1: the farmer's own last seasons, repeated."""
    names = ", then ".join(" with ".join(s) for s in req.past_seasons)
    return Rotation("current", names.capitalize(), req.past_seasons, "", "current")


# --------------------------------------------------------------------------- #
# Step 2: filters
# --------------------------------------------------------------------------- #
def water_filter(rotation: Rotation, req: Request, data: EngineData) -> str | None:
    """Hard reason to drop the rotation on water or season length, else None; adds risk lines."""
    w = data.cfg.engine.water
    for crop in crops_in(rotation):
        short, fit = water_stats(data, req.lga, crop, req.season_type)
        if np.isnan(short):
            continue
        if not data.crops.crops[crop].must_fit_season:
            rotation.risks.append(
                f"{crop.capitalize()} stays in the ground into the dry season and "
                f"ran short of water there in {short:.0%} of years"
            )
            continue
        if req.water == "Rain only" and short > w.max_short_share:
            return f"{crop} ran short of water in {short:.0%} of {req.season_type.lower()} seasons"
        if fit <= w.min_fit_share:
            return f"{crop} fits the rainy season in only {fit:.0%} of years"
        if fit < w.risk_fit_share:
            rotation.risks.append(
                f"{crop.capitalize()} fits the rainy season in about {round(fit * 10)} of 10 "
                f"{req.season_type.lower()} years: plant early or choose a short variety"
            )
    return None


def suitability_filter(rotation: Rotation, req: Request, data: EngineData) -> str | None:
    """A15 (ECOCROP ranges against our climate and soil): drop a crop the LGA cannot suit,
    add risk lines for near misses, heat and short rain."""
    sc = data.cfg.suitability
    for crop in crops_in(rotation):
        row = data.suit_lookup.get((req.lga, crop))
        if row is None:
            continue
        name = crop.capitalize()
        if row["mean_score"] < sc.drop_mean_score:
            return f"{crop} scores {row['mean_score']:.2f} of 1 for this LGA's climate (ECOCROP)"
        if bool(row["ph_outside"]):
            return f"cropland pH here ({row['ph']:.1f}) is outside {crop}'s range (ECOCROP)"
        if row["mean_score"] < sc.risk_mean_score:
            rotation.risks.append(
                f"{name} suits this LGA's climate only partly (score {row['mean_score']:.2f} of 1)"
            )
        if row["texture_fit"] == "outside":
            rotation.risks.append(
                f"{name} prefers heavier soil than the {row['texture_group']} cropland mapped here"
            )
        if row["heat_share"] >= sc.risk_share:
            rotation.risks.append(
                f"The hottest month of the {crop} season is above its best "
                f"range in {round(row['heat_share'] * 10)} of 10 years"
            )
        if row["rain_short_share"] >= sc.risk_share:
            rotation.risks.append(
                f"Season rain was below {crop}'s best range in "
                f"{round(row['rain_short_share'] * 10)} of 10 years"
            )
    return None


def studies(seasons: list[list[str]], data: EngineData) -> list[dict]:
    """Published studies behind this exact sequence (config/rotations.yaml): checks, not gates."""
    return [
        {"key": k, "evidence": t.evidence, "status": t.status, "zones": t.best_fit}
        for k, t in data.rotations.templates.items()
        if t.seasons == seasons and t.status != "current_only"
    ]


def candidates(
    req: Request, data: EngineData, current: Rotation, relax_water: bool = False
) -> tuple[list[Rotation], dict]:
    """Every two-season sequence of two different crops our data can assess (A15 covers all 14;
    A9's water balance covers nine of them).

    The data decides what fits; a matching study is attached afterwards as a check. With
    relax_water, a rotation that fails the water rule stays, marked relaxed, with the failure
    as a risk line (PRD Step 2: fewer than two survivors).
    """
    crops, kept, dropped = list(data.crops.rules()), [], {}
    for first, second in itertools.permutations(crops, 2):
        seasons = [[first], [second]]
        key = f"{first}>{second}"
        rot = Rotation(key, f"{first.capitalize()} then {second}", seasons, "", "data")
        if seasons == current.seasons:
            dropped[key] = "same as Current"
            continue
        reason = suitability_filter(rot, req, data) or water_filter(rot, req, data)
        if reason and relax_water:
            rot.risks.append(f"Water rule relaxed: {reason}")
            rot.relaxed.append("water")
            reason = None
        if reason:
            dropped[key] = reason
            continue
        kept.append(rot)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Step 3: seven dimensions, 0 to 1 or None
# --------------------------------------------------------------------------- #
def nutrients(rot: Rotation, data: EngineData) -> Score:
    n, rules = data.cfg.engine.nitrogen, data.crops.rules()
    credits, gaps = [], []
    for season in rot.seasons:
        best = 0.0
        for crop in season:
            nitro = rules[crop].nitrogen if crop in rules else None
            if nitro is None:
                gaps.append(f"nitrogen for {crop}")
            elif nitro.fixes:
                r = nitro.net_n_after_grain_kg_ha
                net = 0.0 if r is None else (r.mean if r.mean is not None else (r.low + r.high) / 2)
                share = float(np.clip(net / n.full_credit_kg_ha, 0, 1))
                best = max(best, n.base_credit + (1 - n.base_credit) * share)
        credits.append(best)
    fixers = [c for c in crops_in(rot) if c in rules and rules[c].nitrogen.fixes]
    reason = (
        f"{' and '.join(fixers)} fix nitrogen, though most of it leaves with the grain"
        if fixers
        else "no nitrogen-fixing crop"
    )
    return Score(float(np.mean(credits)), reason, gaps)


def cover(rot: Rotation, data: EngineData) -> tuple[float | None, float | None]:
    """Mean season cover days and max root depth, each relative to the longest/deepest crop."""
    crops = data.crops.crops
    days = [max(season_length(c, data) for c in s) for s in rot.seasons]
    longest = max(c.stages_days.total for c in crops.values())
    deepest = max(c.root_depth_max_m for c in crops.values())
    if np.isnan(days).any():
        return None, None
    cover_share = min(1.0, float(np.mean(days)) / longest)
    if any(c not in crops for c in crops_in(rot)):
        return cover_share, None  # no FAO-56 rooting depth: erosion row cannot be scored
    return cover_share, max(crops[c].root_depth_max_m for c in crops_in(rot)) / deepest


def season_length(crop: str, data: EngineData) -> float:
    """Days in the ground: FAO-56 stages where known, else A15's ECOCROP growing period."""
    return data.season_days.get(crop, float("nan"))


def organic_matter(rot: Rotation, data: EngineData) -> Score:
    w, rules = data.cfg.engine.organic_matter, data.crops.rules()
    cover_share, _ = cover(rot, data)
    if cover_share is None:
        return Score(None, "season length unknown for a crop", ["crop season length"])
    legume = np.mean([any(rules[c].nitrogen.fixes for c in s if c in rules) for s in rot.seasons])
    value = w["cover_weight"] * cover_share + w["legume_weight"] * legume
    return Score(float(value), "ground cover days and legume seasons", ["residue amounts"])


def erosion(rot: Rotation, data: EngineData, lga: str) -> Score:
    w = data.cfg.engine.erosion
    cover_share, root_share = cover(rot, data)
    if cover_share is None or root_share is None:
        return Score(None, "rooting depth unknown for a crop", ["rooting depth"])
    value = w["cover_weight"] * cover_share + w["root_weight"] * root_share
    band = data.land.loc[lga, "erosion_band"]
    return Score(float(value), f"ground cover and rooting; erosion risk here is {band.lower()}")


def pests(rot: Rotation, data: EngineData) -> Score:
    p, rules = data.cfg.engine.pests, data.crops.rules()
    seasons = rot.seasons
    pairs = list(zip(seasons, seasons[1:] + seasons[:1], strict=True))
    fam = [{rules[c].family for c in s if c in rules} for s in seasons]
    changes = np.mean([not (fam[i] & fam[(i + 1) % len(seasons)]) for i in range(len(seasons))])
    hosts = {c for c in rules if "striga_hermonthica_host" in rules[c].pest_groups}
    striga = [set(a) & set(b) & hosts for a, b in pairs]
    trap = any("striga_trap_crop" in rules[c].pest_groups for c in crops_in(rot) if c in rules)
    value = changes - p["striga_repeat_penalty"] * any(striga) + p["trap_crop_credit"] * trap
    reason = "crop family changes every season" if changes == 1 else "the same crop family repeats"
    if trap:
        reason += "; a legume before maize cuts Striga"
    return Score(float(np.clip(value, 0, 1)), reason)


def expected_short(data: EngineData, lga: str, crop: str, season_type: str) -> float:
    """Half the chosen season type, half all four types weighted by how often each comes (A5)."""
    shares = data.type_share_lookup[lga]
    known = {t: v for t in shares if not np.isnan(v := water_stats(data, lga, crop, t)[0])}
    if season_type not in known:
        return float("nan")
    weight = sum(shares[t] for t in known)
    usual = sum(known[t] * shares[t] for t in known) / weight
    return 0.5 * known[season_type] + 0.5 * usual


def short_share(data: EngineData, lga: str, crop: str, season_type: str) -> float:
    """A9 bucket where the crop has one, else A15's share of years with rain below the crop's
    ECOCROP optimum (a coarser, rain-only check)."""
    value = expected_short(data, lga, crop, season_type)
    row = data.suit_lookup.get((lga, crop))
    if np.isnan(value) and row is not None:
        value = float(row["rain_short_share"])
    return value


def water(rot: Rotation, req: Request, data: EngineData) -> Score:
    shorts = [short_share(data, req.lga, c, req.season_type) for c in crops_in(rot)]
    known = [s for s in shorts if not np.isnan(s)]
    gaps = [
        f"water balance for {c}" for c, s in zip(crops_in(rot), shorts, strict=True) if np.isnan(s)
    ]
    if not known:
        return Score(None, "no water balance for these crops", gaps)
    relief = data.cfg.engine.water.irrigation_relief if req.water != "Rain only" else 1.0
    short = float(np.mean(known)) * relief
    access = "rain only" if req.water == "Rain only" else "irrigation halves the shortfall"
    reason = (
        f"ran short in about {round(short * 10)} of 10 seasons, weighing {req.season_type.lower()} "
        "years and how often each season type comes here"
    )
    return Score(1 - short, f"{reason} ({access})", gaps)


def forage(rot: Rotation, data: EngineData) -> Score:
    rules = data.crops.rules()
    eaten = {
        c: rules[c].forage.share_eaten
        for c in crops_in(rot)
        if c in rules and rules[c].forage and rules[c].forage.share_eaten is not None
    }
    gaps = [f"share of {c} forage animals eat" for c in crops_in(rot) if c not in eaten]
    if gaps:  # a row covers every crop or none: partial data would let a partner carry it
        return Score(None, "forage value not known for every crop", gaps)
    best = max(eaten, key=eaten.get)
    return Score(
        float(np.mean(list(eaten.values()))),
        f"animals eat {eaten[best]:.0%} of {best} residues",
        gaps,
    )


def market(rot: Rotation, req: Request, data: EngineData) -> Score:
    """Price per mudu in this LGA (KDBS), relative to the dearest mapped crop there.

    Measures are only compared like with like (1 mudu, loose). Selling mostly for home use
    halves the value of cash crops (PRD I7).
    """
    m, rules = data.cfg.engine.market, data.crops.rules()
    prices = data.prices.loc[req.lga]
    gaps = [f"Kaduna price for {c} by the mudu" for c in crops_in(rot) if c not in prices.index]
    if gaps:
        return Score(None, "no comparable KDBS price for every crop", gaps)
    values = [
        prices[c]
        / prices.max()
        * (
            m["home_use_cash_factor"]
            if (req.sell == "Mostly home use" and rules[c].market.role == "cash")
            else 1.0
        )
        for c in crops_in(rot)
    ]
    best = max(crops_in(rot), key=lambda c: prices[c])
    return Score(
        float(np.mean(values)),
        f"{best} sells for about N{prices[best]:,.0f} a mudu in {req.lga} (KDBS); "
        f"you sell: {req.sell.lower()}",
    )


def score_rotation(rot: Rotation, req: Request, data: EngineData) -> dict[str, Score]:
    """All seven dimensions for one rotation."""
    return {
        "nutrients": nutrients(rot, data),
        "organic_matter": organic_matter(rot, data),
        "erosion": erosion(rot, data, req.lga),
        "pests": pests(rot, data),
        "water": water(rot, req, data),
        "forage": forage(rot, data),
        "market": market(rot, req, data),
    }


# --------------------------------------------------------------------------- #
# Striga risk (docs/pest-risk.md, feature 1)
# --------------------------------------------------------------------------- #
def cereal_run(req: Request, data: EngineData) -> float:
    """Cereal seasons in a row at the end of the farmer's history; sorghum counts 1.5."""
    run = 0.0
    for season in reversed(req.past_seasons):
        if not set(season) & CEREALS:
            break
        run += data.cfg.engine.striga.sorghum_season_weight if "sorghum" in season else 1.0
    return run


def striga_risk(req: Request, data: EngineData) -> tuple[str, str]:
    """Risk level from field history and the LGA's soil base, with a plain reason."""
    s, run = data.cfg.engine.striga, cereal_run(req, data)
    soil = int(data.striga_soil.loc[req.lga])
    level = (
        "High"
        if run >= s.high_seasons or (run >= s.medium_seasons and soil >= s.high_soil_base)
        else "Medium"
        if run >= s.medium_seasons
        else "Low"
    )
    soil_word = (
        "among the sandiest, lowest-nitrogen"
        if soil == 2
        else "mid-range"
        if soil == 1
        else "among the less sandy, higher-nitrogen"
    )
    return level, (
        f"{level} Striga risk: {run:g} cereal season(s) in a row on this field, "
        f"and soils here are {soil_word} in Kaduna"
    )


# --------------------------------------------------------------------------- #
# Steps 4 to 6: weights, ranking, confidence
# --------------------------------------------------------------------------- #
def weights(req: Request, data: EngineData, striga_level: str) -> dict[str, float]:
    """Data first: each row's weight starts from how much this LGA's data says it matters
    (0.5, 1 or 1.5 for the best, middle and worst third of Kaduna), then a chosen priority
    multiplies it (PRD Step 4). High Striga risk on the farmer's field adds to Pests."""
    e, need = data.cfg.engine, data.need.loc[req.lga]
    chosen = {PRIORITIES[p] for p in req.priorities}
    w = {
        d: (0.5 + 0.5 * float(need[d]) if d in need.index else 1.0)
        * (e.priority_weight if d in chosen else 1.0)
        for d in DIMENSIONS
    }
    if striga_level == "High":
        w["pests"] += e.striga.pests_weight_bonus
    return w


def coverage(scores: dict[str, Score], w: dict[str, float]) -> float:
    """Share of the weight that sits on rows with data."""
    return sum(w[d] for d in DIMENSIONS if scores[d].value is not None) / sum(w.values())


def total(scores: dict[str, Score], w: dict[str, float]) -> float:
    """Weighted mean over the rows with data, times the share of weight they carry.

    A row without data adds nothing: the tool will not rank up what it cannot evaluate, so
    filling a data gap can only raise a rotation, never a guess (PRD Step 4).
    """
    known = [d for d in DIMENSIONS if scores[d].value is not None]
    mean = sum(w[d] * scores[d].value for d in known) / sum(w[d] for d in known)
    return mean * coverage(scores, w)


def level(value: float | None, data: EngineData) -> str:
    cut = data.cfg.engine.levels
    if value is None:
        return "Not enough information"
    return "High" if value >= cut["high"] else "Medium" if value >= cut["medium"] else "Low"


def close_call(
    a: dict[str, Score], b: dict[str, Score], w: dict[str, float], data: EngineData
) -> bool:
    """PRD Step 6: nudge weights by up to 20% many times; a frequent A/B swap is a close call."""
    s = data.cfg.engine.sensitivity
    rng = np.random.default_rng(data.cfg.random_seed)
    swaps = 0
    for _ in range(s.runs):
        nudged = {d: v * (1 + rng.uniform(-s.max_nudge, s.max_nudge)) for d, v in w.items()}
        swaps += total(b, nudged) > total(a, nudged)
    return swaps / s.runs > s.swap_share


def confidence(req: Request, data: EngineData, rot: Rotation) -> tuple[str, str]:
    """The LGA's A12 level; one step lower for a relaxed rule or unknown soil. A matching study
    is reported as support, and data alone does not lower confidence."""
    row = data.confidence.loc[req.lga]
    steps, reasons = 0, [str(row["reason"])]
    if rot.relaxed or req.soil == "Not sure":
        steps, reasons = steps + 1, [*reasons, "a rule was relaxed or the soil type is unknown"]
    idx = max(0, LEVEL_ORDER.index(row["level"]) - steps)
    return LEVEL_ORDER[idx], "; ".join(reasons)


def heat_line(rot: Rotation, req: Request, data: EngineData) -> list[str]:
    """Heat at flowering: A2's usual rain start plus the crop's early stages gives the
    flowering month; A3 gives how often that month has a 35 C day."""
    lines, start = [], float(data.onset_doy.loc[req.lga])
    for season in rot.seasons[:1]:
        for crop in season:
            if crop not in data.crops.crops:
                continue
            st = data.crops.crops[crop].stages_days
            month = int(np.clip((start + st.init + st.dev) // 30.5 + 1, 1, 12))
            share = float(data.heat_share.loc[req.lga, month])
            if share >= 0.3:
                lines.append(
                    f"{crop.capitalize()} usually flowers in month {month}, when a 35 C "
                    f"day came in {round(share * 10)} of 10 years here"
                )
    return lines


# --------------------------------------------------------------------------- #
# Cards
# --------------------------------------------------------------------------- #
def risk_lines(rot: Rotation, req: Request, data: EngineData, striga: tuple[str, str]) -> list[str]:
    """Rotation risks, Striga, dry spells and scouting lines (never part of the score)."""
    rules, e = data.crops.rules(), data.cfg.engine
    groups = {g for c in crops_in(rot) if c in rules for g in rules[c].pest_groups}
    lines = list(rot.risks) + heat_line(rot, req, data) + weather_lines(rot, req, data)
    if striga[0] != "Low" and groups & {"striga_hermonthica_host", "striga_main_seed_producer"}:
        lines.append(striga[1])
        share = float(data.dry_spell_share.loc[req.lga])
        lines.append(
            f"Long dry spells (10+ days) came in {round(share * 10)} of 10 seasons here; "
            "they make Striga damage worse"
        )
    if "striga_gesnerioides_host" in groups and striga[0] == "High":
        lines.append("Cowpea has its own Striga: ask for a resistant variety")
    lines += [e.scouting[g] for g in e.scouting if g in groups]
    if req.soil and req.soil != "Not sure":
        lines.append(f"You chose {req.soil.lower()} soil; water figures use the satellite soil map")
    return lines


RISK_TEXT = {
    "humid_spell": "{crop} seasons here had a run of unusually humid, warm days in {n} of 10 "
    "years: watch for leaf blight",
    "late_dry_spell": "{crop} had 10+ dry days in the month before harvest in {n} of 10 years "
    "here: harvest on time and dry pods well (aflatoxin)",
}


def weather_lines(rot: Rotation, req: Request, data: EngineData) -> list[str]:
    """A14 lines whose share of seasons reaches the card threshold, by the chosen season type
    where that type has years, else over all years. Column-wise, no row loop."""
    table = data.risk_lines.xs(req.lga, level="lga").reset_index()
    table = table[table["crop"].isin(crops_in(rot))]
    by_type = table.get(f"share_{req.season_type}", pd.Series(np.nan, index=table.index))
    share = by_type.fillna(table["share_all_years"])
    table = table.assign(n=(share * 10).round())  # before filtering: assign realigns on index
    shown = table[share >= data.cfg.risk_lines.min_share]
    templates = shown["line"].map(RISK_TEXT)
    return [
        t.format(crop=c.capitalize(), n=int(n))
        for t, c, n in zip(templates, shown["crop"], shown["n"], strict=True)
    ]


def labour_line(rot: Rotation, req: Request, data: EngineData) -> str:
    rules = data.crops.rules()
    days = [rules[c].labour.days_per_ha for c in crops_in(rot) if c in rules and rules[c].labour]
    if len(days) < len(crops_in(rot)):
        return "Labour: not enough information for every crop"
    return (
        f"Labour with {req.equipment.lower()}: about {round(np.mean(days))} working days per "
        "hectare each season (Guinea savanna trials)"
    )


def card(
    rot: Rotation,
    scores: dict[str, Score],
    w: dict[str, float],
    req: Request,
    data: EngineData,
    striga: tuple[str, str],
    base: dict[str, Score] | None,
) -> dict:
    """One card in the PRD section 9 field order."""
    rows = {}
    for d in DIMENSIONS:
        s, ref = scores[d], (base or scores)[d]
        arrow = (
            "same"
            if base is None or s.value is None or ref.value is None
            else "better"
            if s.value > ref.value + 0.05
            else "worse"
            if s.value < ref.value - 0.05
            else "same"
        )
        rows[d] = {
            "score": s.value,
            "level": level(s.value, data),
            "arrow": arrow,
            "reason": s.reason,
        }
    conf = confidence(req, data, rot)
    return {
        "key": rot.key,
        "title": rot.name,
        "seasons": rot.seasons,
        "rows": rows,
        "total": total(scores, w),
        "rows_scored": sum(s.value is not None for s in scores.values()),
        "studies": studies(rot.seasons, data),
        "source": "current" if rot.status == "current" else "data",
        "confidence": {"level": conf[0], "reason": conf[1]},
        "risks": risk_lines(rot, req, data, striga),
        "labour": labour_line(rot, req, data),
        "gaps": sorted({g for s in scores.values() for g in s.gaps}),
        "check_first": conf[0] == "Low",
    }


OPPORTUNITIES = {
    "Interrupts pest cycles": ["pests"],
    "Reduces water demand": ["water"],
    "Improves infiltration": ["erosion"],
    "Adds supplemental income": ["market", "forage"],
}


def opportunities(rows: dict) -> list[str]:
    """PRD Step 5: an opportunity applies when its row beats Current."""
    return [
        name
        for name, dims in OPPORTUNITIES.items()
        if any(rows[d]["arrow"] == "better" for d in dims)
    ]


def rank_candidates(
    req: Request, data: EngineData, current: Rotation, w: dict[str, float]
) -> tuple[list[tuple[Rotation, dict[str, Score]]], dict]:
    """Every surviving candidate, best first; ties go to a sequence a study backs."""
    pool, dropped = candidates(req, data, current)
    if len(pool) < 2:
        pool, _ = candidates(req, data, current, relax_water=True)
    ranked = sorted(
        ((r, score_rotation(r, req, data)) for r in pool),
        key=lambda rs: (-round(total(rs[1], w), 3), not studies(rs[0].seasons, data)),
    )
    return ranked, dropped


def score_request(req: Request, data: EngineData) -> dict:
    """The PRD's /api/score result: Current plus Options A and B, with what was dropped."""
    if req.lga not in data.zone_of:
        raise ValueError(f"unknown LGA {req.lga!r}")
    striga = striga_risk(req, data)
    w = weights(req, data, striga[0])
    current = current_rotation(req, data)
    problem = water_filter(current, req, data)  # Current is never removed, only warned
    if problem:
        current.risks.insert(0, f"In {req.season_type.lower()} seasons here, {problem}")
    base = score_rotation(current, req, data)
    ranked, dropped = rank_candidates(req, data, current, w)
    scored = ranked[:1]
    for rot, sc in ranked[1:]:
        if set(crops_in(rot)) != set(crops_in(scored[0][0])):
            scored.append((rot, sc))
            break
    cards = [card(current, base, w, req, data, striga, None)]
    for (rot, scores), badge in zip(scored, ("Option A", "Option B"), strict=False):
        c = card(rot, scores, w, req, data, striga, base)
        cards.append({**c, "badge": badge, "opportunities": opportunities(c["rows"])})
    cards[0]["badge"] = "Current"
    flag = len(scored) == 2 and close_call(scored[0][1], scored[1][1], w, data)
    return {
        "lga": req.lga,
        "zone": data.zone_of[req.lga],
        "season_type": req.season_type,
        "striga_risk": {"level": striga[0], "reason": striga[1]},
        "weights": w,
        "cards": cards,
        "close_call": flag,
        "dropped": dropped,
        "low_cropland": bool(data.land.loc[req.lga, "low_cropland"]),
    }
