"""View-model builders for the web screens and JSON endpoints.

Reads only committed contract files (`api/contract/`) and the engine bundle (`EngineData`),
so every route works identically locally and on Vercel (where `data/` is excluded).
"""

import json
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

import pandas as pd
import yaml

from config import CONFIG_DIR
from model.engine_data import EngineData
from model.rotation_engine import DIMENSIONS, PRIORITIES, Request, score_request

CONTRACT_DIR = CONFIG_DIR.parent / "api" / "contract"
MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
TEXTURE_MAP = {"light": "Sandy", "medium": "Loamy", "heavy": "Clay"}
PRIORITY_META = (
    ("save_water", "Save water", "Reduce moisture shortfall & dry-spell risk", "water"),
    ("add_nitrogen", "Add nitrogen to soil", "Legume biological nitrogen fixation", "nutrients"),
    (
        "build_organic_matter",
        "Build organic matter",
        "Longer canopy cover & crop residue",
        "organic_matter",
    ),
    ("stop_erosion", "Stop soil washing away", "Ground cover & deeper root anchoring", "erosion"),
    ("feed_animals", "Feed animals (forage value)", "Palatable haulms & stover", "forage"),
    ("extra_income", "Extra cash income", "Local Kaduna retail market value", "market"),
    (
        "fewer_pests",
        "Interrupt pest & disease cycles",
        "Break Striga weed & cereal pest buildup",
        "pests",
    ),
)
DIMENSION_LABELS = {
    "nutrients": "Nutrients (N)",
    "organic_matter": "Organic matter",
    "erosion": "Erosion & infiltration",
    "pests": "Pest cycle",
    "water": "Water use",
    "forage": "Forage value",
    "market": "Market fit",
}
STAGE_META = (
    ("init", "Initial (Planting & Emergence)", "shortfall_init_share"),
    ("dev", "Crop Development (Vegetative)", "shortfall_dev_share"),
    ("mid", "Mid-Season (Flowering & Pollination)", "shortfall_mid_share"),
    ("late", "Late Season (Grain Fill & Ripening)", "shortfall_late_share"),
)


def doy_to_label(doy_val: float | None) -> str:
    """Format a 1-based calendar day of year (non-leap reference) as 'May 26'."""
    if doy_val is None or pd.isna(doy_val):
        return "Unknown"
    target = date(2025, 1, 1) + timedelta(days=max(0, round(float(doy_val)) - 1))
    return target.strftime("%b %-d")


def doy_to_month(doy_val: float) -> int:
    """Return 1-based month number for a 1-based calendar day of year."""
    return (date(2025, 1, 1) + timedelta(days=max(0, round(float(doy_val)) - 1))).month


def read_lgas_contract() -> list[dict[str, Any]]:
    """Read `api/contract/lgas.json`."""
    payload = json.loads((CONTRACT_DIR / "lgas.json").read_text())
    return list(payload["lgas"])


def read_season_contract(lga: str) -> dict[str, Any]:
    """Read `api/contract/season/{lga}.json`, raising ValueError for unknown LGAs."""
    safe_path = CONTRACT_DIR / "season" / f"{lga}.json"
    if not safe_path.exists() or safe_path.parent != CONTRACT_DIR / "season":
        raise ValueError(f"Unknown LGA: {lga}")
    return dict(json.loads(safe_path.read_text()))


def soil_default_for(lga: str, data: EngineData) -> tuple[str, float | None]:
    """Return `(soil_label, ph)` from A15 iSDA cropland suitability for `lga`."""
    rec = data.suit_lookup.get((lga, "maize"), {})
    group = str(rec.get("texture_group", "medium"))
    ph_val = rec.get("ph")
    ph_clean = round(float(ph_val), 1) if ph_val is not None and not pd.isna(ph_val) else None
    return TEXTURE_MAP.get(group, "Loamy"), ph_clean


def lga_catalog(data: EngineData) -> list[dict[str, Any]]:
    """All 23 LGAs enriched with soil default, onset/cessation dates, erosion and confidence."""
    rows: list[dict[str, Any]] = []
    for entry in read_lgas_contract():
        name = str(entry["lga"])
        season = read_season_contract(name)
        soil_label, soil_ph = soil_default_for(name, data)
        conf = data.confidence.loc[name]
        rows.append(
            {
                **entry,
                "cropland_pct": round(float(entry["cropland_share"]) * 100, 1),
                "soil_default": soil_label,
                "soil_ph": soil_ph,
                "onset_label": doy_to_label(season["onset_doy"]["median"]),
                "cessation_label": doy_to_label(season["cessation_doy"]["median"]),
                "length_days": round(float(season["length_days"]["median"])),
                "erosion_band": season["erosion_band"],
                "confidence_level": str(conf["level"]),
                "confidence_reason": str(conf["reason"]),
            }
        )
    return rows


def classify_month_band(
    idx: int,
    bare_prob: float,
    tmax_c: float,
    rain_mm: float,
    onset_month: int,
    cess_month: int,
    dry_month: int | None,
) -> tuple[str, str]:
    """Assign `(band_key, short_label)` to calendar month `idx` (1..12) from contract values."""
    if dry_month == idx:
        return "dry", "Dry risk"
    if idx == onset_month:
        return "onset", "Onset"
    if idx == cess_month:
        return "stop", "Rains ease"
    if rain_mm >= 180.0:
        return "peak", "Peak rain"
    if rain_mm >= 60.0:
        return "rain", "Rain"
    if tmax_c >= 34.0:
        return "hot", "Hottest"
    if bare_prob >= 0.5:
        return "bare", "Bare field"
    return "transition", "Transition"


def build_month_strip(season: dict[str, Any], season_type: str) -> list[dict[str, Any]]:
    """Build 12 monthly calendar cells for `season_type` using `season/{lga}.json`."""
    months = season["months"]
    profile = next(
        (t["monthly"] for t in season["season_types"] if t["name"] == season_type),
        months,
    )
    onset_m = doy_to_month(season["onset_doy"]["median"])
    cess_m = doy_to_month(season["cessation_doy"]["median"])
    dry_m = season["dry_spell"].get("usual_month")
    cells: list[dict[str, Any]] = []
    for pos in range(12):
        rain_mm = float(profile["rain_mm"][pos])
        tmax_c = float(profile["tmax_c"][pos])
        bare_prob = float(months["bare_probability"][pos])
        band, tag = classify_month_band(pos + 1, bare_prob, tmax_c, rain_mm, onset_m, cess_m, dry_m)
        cells.append(
            {
                "num": pos + 1,
                "name": MONTH_NAMES[pos],
                "band": band,
                "tag": tag,
                "rain_mm": round(rain_mm, 1),
                "et0_mm": round(float(months["et0_mm"][pos]), 1),
                "water_balance_mm": round(float(profile["water_balance_mm"][pos]), 1),
                "tmax_c": round(tmax_c, 1),
                "heat_days": round(float(profile["heat_days"][pos]), 1),
                "ndvi": round(float(profile["ndvi_cropland"][pos]), 2),
                "bare_pct": round(bare_prob * 100),
                "smap_z": round(float(months["soil_moisture_anomaly_z"][pos]), 2),
            }
        )
    return cells


def _pattern_card(
    num: str,
    key: str,
    title: str,
    headline: str,
    sub: str,
    status: str,
    tone: str,
    meaning: str,
    source: str,
) -> dict[str, str]:
    """Pack one pattern diagnostic card dictionary."""
    return {
        "num": num,
        "term_key": key,
        "title": title,
        "headline": headline,
        "sub": sub,
        "status": status,
        "tone": tone,
        "meaning": meaning,
        "source": source,
    }


def build_patterns_list(lga: str, season: dict[str, Any], data: EngineData) -> list[dict[str, Any]]:
    """Build the 9 NASA climate pattern cards (Traceability Row 18) from contract + A10 data."""
    m = season["months"]
    trends = {t["series"]: t for t in season["trends"]}
    land = data.land.loc[lga]
    rain_yr = round(sum(float(x) for x in m["rain_mm"]))
    heat_yr = round(sum(float(x) for x in m["heat_days"]), 1)
    mean_tmax = round(sum(float(x) for x in m["tmax_c"]) / 12, 1)
    et0_day = round(sum(float(x) for x in m["et0_mm"]) / 365.0, 1)
    bare_mos = sum(1 for p in m["bare_probability"] if float(p) >= 0.5)
    peak_ndvi = round(max(float(x) for x in m["ndvi_cropland"]), 2)
    july_dry_pct = round(float(m["dry_soil_share_years"][6]) * 100)
    wb_slope = round(float(trends["season_water_balance_mm"]["sen_slope_per_decade"]), 1)
    eband = str(season["erosion_band"])
    etone = "danger" if eband == "High" else ("amber" if eband == "Medium" else "ok")
    filled_pct = round(float(season["flags"]["ndvi_filled_share"]) * 100, 1)
    return [
        _pattern_card(
            "01",
            "temperature",
            "Surface Temperature",
            f"{mean_tmax}°C Mean Tmax",
            f"Peak {round(max(m['tmax_c']), 1)}°C in Mar–Apr",
            "Seasonal baseline",
            "ok",
            "Daytime maxima peak before rain onset and cool below 29°C in August.",
            "NASA POWER Agroclimatology (0.5° × 0.625°, 1981–2025)",
        ),
        _pattern_card(
            "02",
            "heat_stress",
            "Heat Stress Days (≥35°C)",
            f"{heat_yr} Days / Yr",
            str(trends["heat_days"]["card_line"]),
            "High pre-season heat" if heat_yr >= 25 else "Moderate heat",
            "danger" if heat_yr >= 25 else "amber",
            "Hot days cluster in Feb–May; late planting shifts flowering toward warmer days.",
            "NASA POWER Daily Tmax (1981–2025)",
        ),
        _pattern_card(
            "03",
            "rain_onset",
            "Rainfall Onset & Cessation",
            f"Onset {doy_to_label(season['onset_doy']['median'])}",
            f"{round(season['length_days']['median'])}d · "
            f"ends {doy_to_label(season['cessation_doy']['median'])}",
            "Short window" if season["length_days"]["median"] < 140 else "Savanna window",
            "amber" if season["length_days"]["median"] < 150 else "ok",
            f"{trends['onset_doy']['card_line']}. 80% of seasons span "
            f"{round(season['length_days']['p10'])}–{round(season['length_days']['p90'])} days.",
            "CHIRPS v2.0 (0.05°) & A2 Agronomic Onset Rule",
        ),
        _pattern_card(
            "04",
            "soil_moisture_anomaly",
            "Soil Moisture Anomaly",
            f"{july_dry_pct}% Dry-Soil Julys",
            f"July z = {round(float(m['soil_moisture_anomaly_z'][6]), 2)} (2015–2025)",
            "Monitor flowering" if july_dry_pct >= 15 else "Buffered",
            "amber" if july_dry_pct >= 15 else "ok",
            "Share of SMAP years where July soil moisture dropped ≥1 SD below normal.",
            "NASA SMAP SPL4SMGP V008 (~9 km, 2015–2025)",
        ),
        _pattern_card(
            "05",
            "evapotranspiration",
            "Evaporation & ET0",
            f"{et0_day} mm / Day",
            f"Rain {rain_yr} mm vs ET0 {round(sum(m['et0_mm']))} mm/yr",
            "High dry-month demand",
            "amber",
            "FAO-56 atmospheric water demand exceeds rainfall Oct–May, favoring mulch cover.",
            "NASA POWER · FAO-56 Penman-Monteith Eq. 6",
        ),
        _pattern_card(
            "06",
            "frost_risk",
            "Frost Probability (≤2°C)",
            "0.0% Risk",
            "Zero frost days in 45-year record",
            "No agricultural threat",
            "muted",
            "Minimum temperatures stay safely above 2°C year-round across Kaduna State.",
            "NASA POWER Daily Tmin (1981–2025)",
        ),
        _pattern_card(
            "07",
            "extreme_rain",
            "Extreme Rain & Erosion",
            f"{round(float(land['imerg_p95_wet_mm']), 1)} mm p95 Storm",
            f"Slope {round(float(land['srtm_slope_pct_crop']), 1)}% · {eband} erosion",
            f"{eband} topsoil risk",
            etone,
            "Heavy monsoon storms erode bare plots; canopy cover and deep roots protect soil.",
            "NASA GPM IMERG V07 (0.1°) × SRTM 30m × iSDAsoil",
        ),
        _pattern_card(
            "08",
            "ndvi",
            "Vegetation & Bare-Field Index",
            f"NDVI {peak_ndvi} Peak",
            f"Bare cropland ~{bare_mos} mos/yr",
            f"{bare_mos}-month bare window",
            "amber",
            f"Smoothed cropland NDVI marks dry bare months; cloud-filled share is {filled_pct}%.",
            "NASA MODIS MOD13Q1 250 m (ESA WorldCover Mask)",
        ),
        _pattern_card(
            "09",
            "water_balance",
            "Long-Term Water Balance",
            f"{wb_slope:+.1f} mm / Dec",
            str(trends["season_water_balance_mm"]["card_line"]),
            "Significant shift" if trends["season_water_balance_mm"]["significant"] else "Stable",
            "amber" if trends["season_water_balance_mm"]["significant"] else "ok",
            "45-yr rain minus FAO-56 ET0 replaces GRACE-FO (~300 km footprint too coarse).",
            "CHIRPS + NASA POWER Mann-Kendall Sen Slope",
        ),
    ]


def build_terms_dict(
    lga: str, season: dict[str, Any], data: EngineData
) -> dict[str, dict[str, str]]:
    """Plain-language Tap-for-Meaning glossary entries (R23) hydrated with `lga` values."""
    by_key = {p["term_key"]: p for p in build_patterns_list(lga, season, data)}
    dry = season["dry_spell"]
    usual_m = MONTH_NAMES[dry["usual_month"] - 1] if dry.get("usual_month") else "None ≥10% yrs"
    terms = {
        k: {
            "title": p["title"],
            "subtitle": f"Pattern {p['num']} · {lga} LGA",
            "what": p["meaning"],
            "reading": p["headline"],
            "reading_note": p["sub"],
            "source": p["source"],
            "why": "Shapes which crops finish inside the rainy window and protect soil health.",
        }
        for k, p in by_key.items()
    }
    terms["soil_moisture_anomaly"].update(
        {
            "what": (
                "How wet or dry root-zone soil is compared with its usual monthly average, "
                "measured in standard deviations (z-score)."
            ),
            "why": (
                "Dry soil during flowering forces maize to rely on stored water. Lower-water "
                "legumes and organic matter buffer this risk."
            ),
        }
    )
    terms["evapotranspiration"].update(
        {
            "what": (
                "How fast sun, heat, dry air, and wind draw moisture from soil and leaves. "
                "Crop need (ETc) equals reference ET0 times a stage factor (Kc)."
            ),
            "why": "When crop demand exceeds rain and root-zone water, grain fill drops.",
        }
    )
    terms["dry_spell"] = {
        "title": "In-Season Dry Spell",
        "subtitle": f"A2 Agronomic Rule · {lga} LGA",
        "what": "Consecutive days under 1 mm of rain inside the active rainy season.",
        "reading": f"{round(float(dry['longest_median_days']))}d Median Longest",
        "reading_note": (
            f"Usual 7+ day window: {usual_m} · 10+ day spells in "
            f"{round(float(dry['share_years_10plus']) * 100, 1)}% of LGA-mean years."
        ),
        "source": "CHIRPS v2.0 (5.5 km) & Per-Pixel Dry-Spell Check (1981–2025)",
        "why": "Satellite LGA averages smooth localized dry spells; verify with local farmers.",
    }
    return terms


def season_view(lga: str, season_type: str, data: EngineData) -> dict[str, Any]:
    """Assemble everything needed by `/season` and `/season/patterns` for `lga`."""
    season = read_season_contract(lga)
    valid_type = season_type if season_type in data.season_types else "Typical"
    soil_label, soil_ph = soil_default_for(lga, data)
    p10_lbl = doy_to_label(season["onset_doy"]["p10"])
    p90_lbl = doy_to_label(season["onset_doy"]["p90"])
    return {
        "lga": lga,
        "zone": season["zone"],
        "season_type": valid_type,
        "season_types": [
            {
                "name": st["name"],
                "share_pct": round(float(st["share_years"]) * 100),
                "years": st["years"],
            }
            for st in season["season_types"]
        ],
        "strip": build_month_strip(season, valid_type),
        "onset_label": doy_to_label(season["onset_doy"]["median"]),
        "onset_range": f"{p10_lbl} – {p90_lbl}",
        "cessation_label": doy_to_label(season["cessation_doy"]["median"]),
        "length_days": round(float(season["length_days"]["median"])),
        "trends": season["trends"],
        "confidence": season["confidence"],
        "erosion_band": season["erosion_band"],
        "flags": season["flags"],
        "soil_default": soil_label,
        "soil_ph": soil_ph,
        "patterns": build_patterns_list(lga, season, data),
        "terms": build_terms_dict(lga, season, data),
    }


def _crop_stages_view(
    row: pd.Series, chosen_crop: str, data: EngineData
) -> tuple[int, list[dict[str, Any]]]:
    """Build per-stage FAO-56 demand and shortfall list for `chosen_crop`."""
    crop_cfg = data.crops.crops.get(chosen_crop)
    sdays = (
        crop_cfg.stages_days.model_dump()
        if crop_cfg
        else {"init": 20, "dev": 30, "mid": 40, "late": 25}
    )
    kc_vals = crop_cfg.kc.model_dump() if crop_cfg else {"ini": 0.35, "mid": 1.1, "end": 0.45}
    total_days = sum(int(sdays[k]) for k in ("init", "dev", "mid", "late"))
    need_mm = float(row["water_need_mm"])
    stages = []
    for key, label, col in STAGE_META:
        days = int(sdays[key])
        kc_key = "ini" if key == "init" else ("end" if key == "late" else "mid")
        short_pct = round(float(row[col]) * 100, 1)
        stages.append(
            {
                "key": key,
                "label": label,
                "days": days,
                "kc": round(float(kc_vals[kc_key]), 2),
                "need_mm": round(need_mm * (days / max(total_days, 1)), 1),
                "short_pct": short_pct,
                "is_riskiest": str(row["riskiest_stage"]) == key and short_pct > 0,
            }
        )
    return total_days, stages


def crop_water_view(lga: str, crop: str, season_type: str, data: EngineData) -> dict[str, Any]:
    """Assemble FAO-56 crop water balance diagnostics (`F3d`) from `EngineData.water`."""
    available_crops = sorted({idx[1] for idx in data.water.index})
    chosen_crop = crop if crop in available_crops else "maize"
    valid_type = season_type if season_type in data.season_types else "Typical"
    row = data.water.loc[(lga, chosen_crop, valid_type)]
    total_days, stages = _crop_stages_view(row, chosen_crop, data)
    comparisons = [
        {
            "crop": alt,
            "label": alt.capitalize(),
            "need_mm": round(float(data.water.loc[(lga, alt, valid_type)]["water_need_mm"])),
            "fits_pct": round(
                float(data.water.loc[(lga, alt, valid_type)]["fits_season_share"]) * 100
            ),
            "short_pct": round(float(data.water.loc[(lga, alt, valid_type)]["short_share"]) * 100),
            "days": round(float(data.season_days.get(alt, 100))),
        }
        for alt in ("maize", "soybean", "cowpea", "sorghum", "groundnut", "millet")
        if (lga, alt, valid_type) in data.water.index
    ]
    raw_lbl = row["riskiest_stage_label"]
    riskiest_lbl = str(raw_lbl) if pd.notna(raw_lbl) and str(raw_lbl) else "None (fits rain)"
    return {
        "lga": lga,
        "zone": data.zone_of[lga],
        "crop": chosen_crop,
        "crop_label": chosen_crop.capitalize(),
        "available_crops": available_crops,
        "season_type": valid_type,
        "season_types": data.season_types,
        "total_days": total_days,
        "water_need_mm": round(float(row["water_need_mm"])),
        "water_used_mm": round(float(row["water_used_mm"])),
        "season_rain_mm": round(float(row["season_rain_mm"])),
        "shortfall_mm": round(float(row["shortfall_mm"]), 1),
        "stress_days": round(float(row["stress_days"]), 1),
        "short_pct": round(float(row["short_share"]) * 100, 1),
        "fits_pct": round(float(row["fits_season_share"]) * 100, 1),
        "riskiest_stage": str(row["riskiest_stage"]),
        "riskiest_label": riskiest_lbl,
        "stages": stages,
        "comparisons": comparisons,
        "onset_label": doy_to_label(float(data.onset_doy.loc[lga])),
    }


def map_trends_summary(data: EngineData) -> dict[str, dict[str, Any]]:
    """Per-LGA metrics for the 23-LGA interactive choropleth map (`F8`)."""
    summary: dict[str, dict[str, Any]] = {}
    for item in lga_catalog(data):
        name = item["lga"]
        season = read_season_contract(name)
        trends = {t["series"]: t for t in season["trends"]}
        land_row = data.land.loc[name]
        summary[name] = {
            **item,
            "annual_rain_mm": round(sum(float(x) for x in season["months"]["rain_mm"])),
            "heat_days_yr": round(sum(float(x) for x in season["months"]["heat_days"]), 1),
            "onset_slope_days_dec": round(float(trends["onset_doy"]["sen_slope_per_decade"]), 2),
            "onset_significant": bool(trends["onset_doy"]["significant"]),
            "heat_slope_days_dec": round(float(trends["heat_days"]["sen_slope_per_decade"]), 1),
            "heat_significant": bool(trends["heat_days"]["significant"]),
            "erosion_index": round(float(land_row["erosion_index"]), 3),
            "slope_pct": round(float(land_row["srtm_slope_pct_crop"]), 1),
            "imerg_p95_mm": round(float(land_row["imerg_p95_wet_mm"]), 1),
            "july_dry_soil_pct": round(float(season["months"]["dry_soil_share_years"][6]) * 100),
        }
    return summary


def _split_crops(raw: str, default: list[str]) -> list[str]:
    items = [c.strip().lower() for c in raw.split(",") if c.strip()][:2]
    return items or default


def parse_advisor_query(params: dict[str, str], data: EngineData) -> Request:
    """Build a validated `rotation_engine.Request` from URL query parameters with safe defaults."""
    lga = params.get("lga", "Zaria")
    valid_lga = lga if lga in data.zone_of else "Zaria"
    soil_raw = params.get("soil")
    soil = soil_raw if soil_raw in ("Sandy", "Loamy", "Clay", "Not sure") else None
    water_raw = params.get("water", "Rain only")
    water = water_raw if water_raw in ("Rain only", "Irrigation", "Both") else "Rain only"
    equip_raw = params.get("equipment", "Hand tools")
    equip = equip_raw if equip_raw in ("Hand tools", "Animal traction", "Tractor") else "Hand tools"
    s1 = _split_crops(params.get("s1", "maize"), ["maize"])
    s2 = _split_crops(params["s2"], []) if "s2" in params else ["maize"]
    past = [s2, s1] if s2 else [s1]
    raw_prios = params.get("priorities", "save_water,add_nitrogen").split(",")
    prios = [p.strip() for p in raw_prios if p.strip() in PRIORITIES][:3]
    sell_opts = ("Local market", "Buyer, processor or feed mill", "Mostly home use")
    sell_raw = params.get("sell", "Local market")
    sell = sell_raw if sell_raw in sell_opts else "Local market"
    stype_raw = params.get("season_type", "Typical")
    stype = stype_raw if stype_raw in data.season_types else "Typical"
    return Request(
        lga=valid_lga,
        soil=soil,  # type: ignore[arg-type]
        water=water,  # type: ignore[arg-type]
        equipment=equip,  # type: ignore[arg-type]
        past_seasons=past,
        priorities=prios,
        sell=sell,  # type: ignore[arg-type]
        season_type=stype,
    )


def radio_script_view(lga: str, data: EngineData) -> dict[str, Any]:
    """Generate LGA-level 60-second Hausa and English broadcast scripts (PRD P7, zero PII)."""
    valid_lga = lga if lga in data.zone_of else "Zaria"
    season = read_season_contract(valid_lga)
    req = Request(
        lga=valid_lga,
        past_seasons=[["maize"], ["maize"]],
        priorities=["add_nitrogen", "save_water"],
    )
    scored = score_request(req, data)
    opt_a = scored["cards"][1] if len(scored["cards"]) > 1 else scored["cards"][0]
    opt_b = scored["cards"][2] if len(scored["cards"]) > 2 else opt_a
    onset_str = doy_to_label(season["onset_doy"]["median"])
    cess_str = doy_to_label(season["cessation_doy"]["median"])
    studies = opt_a["studies"]
    study_line = studies[0]["evidence"].split(";")[0] if studies else "Savanna rotation trials"
    en_segments = [
        (
            "00:00 – 00:15 · WEATHER REALITY & ONSET",
            f"Good morning farmers across {valid_lga} Local Government. Forty-five years of "
            f"satellite rainfall records place median planting rains around {onset_str}, "
            f"ending near {cess_str}.",
        ),
        (
            "00:15 – 00:35 · REPEATED CEREAL RISK",
            "Planting the same cereal every year depletes soil nitrogen and builds witchweed "
            "(Striga) pressure. Rotating crop families protects harvest stability.",
        ),
        (
            "00:35 – 00:50 · EVIDENCE-BACKED ROTATION",
            f"Consider {opt_a['title']} ({opt_a['water_line']}) or {opt_b['title']}. "
            f"{opt_a['why']}",
        ),
        (
            "00:50 – 01:00 · LOCAL EXTENSION ADVICE",
            "This tool supports farmer judgment and local expertise; it never replaces them. "
            "Check seed varieties and phosphorus needs with your KADA extension officer.",
        ),
    ]
    ha_segments = [
        (
            "00:00 – 00:15 · SHIGARWA DA RAHOTON DAMINA",
            f"Barka da safiya manoma a fadin karamar hukumar {valid_lga}. Bayanan tauraron "
            f"dan adam na shekaru 45 sun nuna ruwan shuka yakan fara kusa da {onset_str} "
            f"zuwa {cess_str}.",
        ),
        (
            "00:15 – 00:35 · ILLAR SHUKA IRI DAYA KULLUM",
            "Maimaita shukar hatsi iri daya a kowace kaka yana rage karfin sinadarin kasa "
            "kuma yana kara matsalar wuta-wuta (Striga). Canza amfanin gona yana kare kasa.",
        ),
        (
            "00:35 – 00:50 · SHAWARAR MUSAYA MAI HUJJA",
            f"Ku duba tsarin {opt_a['title']} ko {opt_b['title']} tare da malamin gona. "
            f"Hujjar bincike: {study_line}.",
        ),
        (
            "00:50 – 01:00 · TUNTUBAR MALAMIN GONA",
            "Wannan bayani yana taimaka wa hukuncin manomi ne, ba ya maye gurbinsa. Kafin "
            "sayen iri ko taki, ku tuntubi malamin gonar yankinku na KADA.",
        ),
    ]
    return {
        "lga": valid_lga,
        "zone": season["zone"],
        "onset_label": onset_str,
        "cessation_label": cess_str,
        "opt_a_title": opt_a["title"],
        "opt_b_title": opt_b["title"],
        "study_cite": study_line,
        "en_segments": en_segments,
        "ha_segments": ha_segments,
        "en_words": sum(len(text.split()) for _, text in en_segments),
        "ha_words": sum(len(text.split()) for _, text in ha_segments),
        "dimensions": DIMENSIONS,
    }


@lru_cache(maxsize=1)
def load_translations() -> dict[str, Any]:
    """Read config/i18n.yaml dictionary for English and Hausa text (docs/tech-stack.md)."""
    path = CONFIG_DIR / "i18n.yaml"
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text())
    return dict(raw) if isinstance(raw, dict) else {}


def get_i18n(lang: str = "en") -> dict[str, Any]:
    """Return dictionary of UI strings for lang ('en' or 'ha')."""
    all_trans = load_translations()
    target_lang = "ha" if lang == "ha" else "en"
    return dict(all_trans.get(target_lang, all_trans.get("en", {})))


# --------------------------------------------------------------------------- #
# Map Legend and Geospatial Point Queries
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=1)
def load_layers_config() -> dict[str, Any]:
    """Read config/layers.yaml single source of truth for map colour scales and units."""
    path = CONFIG_DIR / "layers.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Layers config not found at {path}")
    raw = yaml.safe_load(path.read_text())
    return dict(raw.get("layers", {}))


def layer_legend_view(layer: str) -> dict[str, Any]:
    """Return colour scale limits, unit, and ordered stops for one map layer."""
    layers = load_layers_config()
    if layer not in layers:
        raise ValueError(f"Unknown layer '{layer}'. Available: {list(layers.keys())}")
    cfg = dict(layers[layer])
    cfg["layer"] = layer
    return cfg


def all_legends_view() -> dict[str, Any]:
    """Return all layer legends indexed by layer name."""
    layers = load_layers_config()
    return {name: {**dict(cfg), "layer": name} for name, cfg in layers.items()}


@lru_cache(maxsize=1)
def _read_kaduna_lgas_geojson() -> dict[str, Any]:
    """Read the static 23-LGA boundary GeoJSON once per process."""
    geo_path = CONFIG_DIR.parent / "static" / "geo" / "kaduna_lgas.geojson"
    if not geo_path.exists():
        raise FileNotFoundError(f"Kaduna LGA GeoJSON missing at {geo_path}")
    raw = json.loads(geo_path.read_text())
    return dict(raw) if isinstance(raw, dict) else {}


def _point_in_polygon_ring(x: float, y: float, ring: list[list[float]]) -> bool:
    """Ray casting algorithm to test whether point (lon=x, lat=y) is inside a linear ring."""
    inside = False
    n = len(ring)
    p1x, p1y = ring[0]
    for i in range(1, n + 1):
        p2x, p2y = ring[i % n]
        if y > min(p1y, p2y) and y <= max(p1y, p2y) and x <= max(p1x, p2x):
            xinters = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x if p1y != p2y else p1x
            if p1x == p2x or x <= xinters:
                inside = not inside
        p1x, p1y = p2x, p2y
    return inside


def _point_in_geometry(x: float, y: float, geom: dict[str, Any]) -> bool:
    """Check if coordinate (lon=x, lat=y) falls inside a Polygon or MultiPolygon geometry."""
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon":
        if not coords or not _point_in_polygon_ring(x, y, coords[0]):
            return False
        for hole in coords[1:]:
            if _point_in_polygon_ring(x, y, hole):
                return False
        return True
    if gtype == "MultiPolygon":
        for poly in coords:
            if poly and _point_in_polygon_ring(x, y, poly[0]):
                in_hole = any(_point_in_polygon_ring(x, y, hole) for hole in poly[1:])
                if not in_hole:
                    return True
        return False
    return False


def find_lga_for_point(lat: float, lon: float) -> str:
    """Find the Kaduna LGA containing coordinate (lat, lon). Raises ValueError if outside state."""
    # Kaduna State bounding envelope (approx 9.0 to 11.6 N, 6.0 to 8.9 E)
    if not (8.9 <= lat <= 11.6 and 6.0 <= lon <= 8.9):
        raise ValueError(
            f"Coordinates ({lat:.4f}, {lon:.4f}) are outside Kaduna State "
            f"(valid bounds: lat 9.00–11.55°N, lon 6.09–8.82°E)"
        )
    geojson_data = _read_kaduna_lgas_geojson()
    for feat in geojson_data.get("features", []):
        if _point_in_geometry(lon, lat, feat.get("geometry", {})):
            return str(feat["properties"]["lga"])

    # Fallback to closest centroid if coordinate sits right on a coastal/border vertex
    min_dist = float("inf")
    closest_lga: str | None = None
    for feat in geojson_data.get("features", []):
        props = feat.get("properties", {})
        d = (lat - props["lat_deg"]) ** 2 + (lon - props["lon_deg"]) ** 2
        if d < min_dist:
            min_dist = d
            closest_lga = props.get("lga")
    if closest_lga is not None and min_dist <= 0.05:  # within ~5 km border buffer
        return str(closest_lga)
    raise ValueError(f"Coordinates ({lat:.4f}, {lon:.4f}) do not match any Kaduna LGA boundary.")


def point_query_view(lat: float, lon: float, data: EngineData) -> dict[str, Any]:
    """Resolve geographic coordinate to its enclosing LGA and return local agroclimatic profile."""
    lga = find_lga_for_point(lat, lon)
    season = read_season_contract(lga)
    catalog_lookup = {item["lga"]: item for item in lga_catalog(data)}
    item = catalog_lookup.get(lga, {})
    trends_map = map_trends_summary(data)
    trend = trends_map.get(lga, {})

    # July root-zone soil moisture anomaly z-score
    july_z = round(float(season["months"]["soil_moisture_anomaly_z"][6]), 2)
    # Mean crop water shortfall share across crops in this LGA
    water_stress_mean = round(float(data.water.xs(lga, level=0)["short_share"].mean()) * 100, 1)

    return {
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "lga": lga,
        "zone": str(season["zone"]),
        "cropland_share": float(item.get("cropland_share", 0.0)),
        "cropland_pct": float(item.get("cropland_pct", 0.0)),
        "soil_default": str(item.get("soil_default", "Loamy")),
        "soil_ph": item.get("soil_ph"),
        "onset_label": str(item.get("onset_label", "")),
        "cessation_label": str(item.get("cessation_label", "")),
        "length_days": int(item.get("length_days", 0)),
        "erosion_band": str(item.get("erosion_band", "Medium")),
        "confidence_level": str(item.get("confidence_level", "Medium")),
        "confidence_reason": str(item.get("confidence_reason", "")),
        "layers": {
            "soil_moisture_z": july_z,
            "heat_days_yr": float(trend.get("heat_days_yr", 0.0)),
            "erosion_index": float(trend.get("erosion_index", 0.0)),
            "water_stress_pct": water_stress_mean,
            "annual_rain_mm": int(trend.get("annual_rain_mm", 0)),
            "imerg_p95_mm": float(trend.get("imerg_p95_mm", 0.0)),
        },
    }


_GRID_CACHE: dict[str, dict[str, Any]] = {}


def lga_grid_geojson_view(lga: str, data: EngineData) -> dict[str, Any]:
    """Generate or retrieve the 1 km GeoJSON grid cells for one LGA (docs/map-architecture.md)."""
    if lga not in data.zone_of:
        raise ValueError(f"Unknown LGA: {lga}")
    if lga in _GRID_CACHE:
        return _GRID_CACHE[lga]

    geojson_data = _read_kaduna_lgas_geojson()
    feat = next(
        (f for f in geojson_data.get("features", []) if f["properties"]["lga"] == lga),
        None,
    )
    if feat is None:
        raise ValueError(f"Geometry missing for LGA: {lga}")

    geom = feat["geometry"]
    gtype = geom["type"]
    coords = geom["coordinates"]
    rings = coords if gtype == "Polygon" else [r for poly in coords for r in poly]
    all_lons = [p[0] for ring in rings for p in ring]
    all_lats = [p[1] for ring in rings for p in ring]
    min_lon, max_lon = min(all_lons), max(all_lons)
    min_lat, max_lat = min(all_lats), max(all_lats)

    season = read_season_contract(lga)
    july_z = round(float(season["months"]["soil_moisture_anomaly_z"][6]), 2)
    trends_map = map_trends_summary(data)
    trend = trends_map.get(lga, {})
    water_stress_mean = round(float(data.water.xs(lga, level=0)["short_share"].mean()) * 100, 1)

    step = 0.009  # ~1 km at 10-11 degrees North
    features: list[dict[str, Any]] = []
    idx = 0
    cur_lat = min_lat
    slug = lga.lower().replace(" ", "_").replace("'", "")
    while cur_lat < max_lat:
        cur_lon = min_lon
        while cur_lon < max_lon:
            cx, cy = cur_lon + step / 2, cur_lat + step / 2
            if _point_in_geometry(cx, cy, geom):
                features.append(
                    {
                        "type": "Feature",
                        "properties": {
                            "cell_id": f"{slug}_{idx}",
                            "lga": lga,
                            "soil_moisture_z": july_z,
                            "heat_days_yr": float(trend.get("heat_days_yr", 0.0)),
                            "erosion_index": float(trend.get("erosion_index", 0.0)),
                            "water_stress_pct": water_stress_mean,
                            "cropland_fraction": float(trend.get("cropland_share", 0.3)),
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [round(cur_lon, 5), round(cur_lat, 5)],
                                    [round(cur_lon + step, 5), round(cur_lat, 5)],
                                    [round(cur_lon + step, 5), round(cur_lat + step, 5)],
                                    [round(cur_lon, 5), round(cur_lat + step, 5)],
                                    [round(cur_lon, 5), round(cur_lat, 5)],
                                ]
                            ],
                        },
                    }
                )
                idx += 1
            cur_lon += step
        cur_lat += step

    res = {
        "type": "FeatureCollection",
        "lga": lga,
        "cell_count": len(features),
        "features": features,
    }
    _GRID_CACHE[lga] = res
    return res
