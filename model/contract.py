"""The data contract files the app reads (PRD section 11, R36): lgas.json and season/{lga}.json.

Every file is built from the model outputs in data/processed/models (A2 to A7, A10, A12) and the
feature tables, never from fallback averages, so each carries source "model". They are written
outside data/ (Vercel skips data/), next to the engine bundle:

    python -m model.contract        # also run at the end of python -m model.build
"""

import json
import logging
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from config import CONFIG_DIR
from model.settings import ModelConfig, load_model_config
from model.water_pipeline import canonical_lga

MONTHS = list(range(1, 13))
SOURCE = "model"


def doy_range(values: pd.DataFrame, column: str) -> pd.DataFrame:
    """Per LGA: median, 10th and 90th percentile of a day-of-year column, and the year count.

    The 10th to 90th percentile is the range shown on the season strip: 8 years in 10 fall in it.
    """
    group = values.dropna(subset=[column]).groupby("lga")[column]
    return pd.DataFrame(
        {
            "median": group.median(),
            "p10": group.quantile(0.1),
            "p90": group.quantile(0.9),
            "years": group.size(),
        }
    )


def dry_spell_months(spells: pd.DataFrame, seasons: pd.Series) -> pd.DataFrame:
    """Share of years with a mid-season dry spell (A2, 7+ days) starting in each month.

    spells has lga, year and start (one row per spell, from a2_dry_spells.csv); seasons gives
    each LGA's number of years with a season, the denominator, so a year with no spell counts.
    Returns LGA x month 1 to 12.
    """
    start = pd.to_datetime(spells["start"])
    years = spells.assign(month=start.dt.month).drop_duplicates(["lga", "year", "month"])
    counts = pd.crosstab(years["lga"], years["month"]).reindex(
        index=seasons.index, columns=MONTHS, fill_value=0
    )
    return counts.div(seasons, axis=0)


def dry_spell_window(shares: pd.Series, min_share: float) -> dict[str, Any]:
    """The month mid-season dry spells most often start in, and how often (R18).

    Below min_share no month is a usual dry-spell month, and the window is null with the reason:
    LGA-average rain rarely shows one, which is not the same as farms never having one.
    """
    month = int(shares.idxmax())
    share = float(shares.loc[month])
    if share < min_share:
        return {
            "usual_month": None,
            "share_years": share,
            "note": "LGA-average rain rarely shows a 7+ day dry spell inside the season; "
            "local dry spells can still happen",
        }
    return {"usual_month": month, "share_years": share, "note": None}


def monthly_means(month: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Long-term mean of each column per LGA and calendar month, over the rows given."""
    return month.groupby(["lga", "month"])[columns].mean()


def clean(value: Any) -> Any:
    """JSON-safe: NaN becomes null, numpy numbers become Python numbers rounded to 3 places."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [clean(v) for v in value]
    if isinstance(value, np.bool_ | bool):
        return bool(value)
    if isinstance(value, np.integer | int):
        return int(value)
    if isinstance(value, np.floating | float):
        return None if math.isnan(value) else round(float(value), 3)
    return value


# --------------------------------------------------------------------------- #
# Reading the model outputs
# --------------------------------------------------------------------------- #
def read_lga_table(path: Path, lgas: list[str]) -> pd.DataFrame:
    """A CSV with an lga column, names mapped to the feature tables' spelling."""
    table = pd.read_csv(path)
    return table.assign(lga=canonical_lga(table["lga"], lgas))


def load_inputs(cfg: ModelConfig) -> dict[str, pd.DataFrame]:
    """Every table the contract files are built from, LGA names in the feature tables' spelling."""
    out, lgas, c = cfg.paths.output_dir, sorted(cfg.lga_zone), cfg.contract

    def read(path: Path) -> pd.DataFrame:
        return read_lga_table(path, lgas)

    a2, spells = read(cfg.paths.onset), read(cfg.paths.dry_spells)
    month = pd.read_parquet(cfg.paths.monthly_features)
    month = month.assign(lga=canonical_lga(month["lga"], lgas))
    return {
        "a2": a2[a2["year"].between(c.years.start, c.years.end)],
        "spells": spells[spells["year"].between(c.years.start, c.years.end)],
        "a3": read(out / "a3" / "a3_lga_month_indicators.csv").set_index(["lga", "month"]),
        "a4": read(out / "a4" / "a4_bare_probability.csv").set_index("lga"),
        "a5_profiles": read(out / "a5" / "a5_monthly_profiles.csv"),
        "a5_share": read(out / "a5" / "a5_lga_type_share.csv").set_index("lga"),
        "a7": read(out / "a7" / "a7_lga_trends.csv"),
        "a10": read(out / "a10" / "a10_lga_land_risk.csv").set_index("lga"),
        "a12": read(out / "a12" / "a12_lga_confidence.csv").set_index("lga"),
        "static": read(cfg.paths.static_features.with_suffix(".csv")).set_index("lga"),
        "ndvi": monthly_means(
            month[month["year"].between(cfg.years.start, cfg.years.end)], ["ndvi_crop_sg"]
        ),
        "solar_filled_years": pd.DataFrame(
            {"year": sorted(month.loc[month["power_rs_filled_days"] > 0, "year"].unique())}
        ),
    }


# --------------------------------------------------------------------------- #
# One LGA's season file
# --------------------------------------------------------------------------- #
A3_MONTHLY = {  # contract name -> A3 column
    "rain_mm": "rain_mm_mean",
    "et0_mm": "power_et0_pm_mm_mean",
    "water_balance_mm": "water_balance_mm_mean",
    "tmax_c": "power_tmax_mean_c_mean",
    "heat_days": "power_heat_days_mean",
    "heat_share_years": "heat_share_years",
    "frost_share_years": "frost_share_years",
    "very_heavy_rain_share_years": "very_heavy_rain_50mm_share_years",
    "extreme_rain_share_years": "extreme_rain_p95_share_years",
    "soil_moisture_anomaly_z": "smap_anomaly_z_mean",
    "dry_soil_share_years": "soil_dry_share_years",
}
PROFILE = {  # contract name -> A5 profile column
    "rain_mm": "rain_mm",
    "ndvi_cropland": "ndvi_crop_sg",
    "tmax_c": "power_tmax_mean_c",
    "heat_days": "power_heat_days",
    "water_balance_mm": "water_balance_mm",
}


def months_block(lga: str, t: dict[str, pd.DataFrame], spells: pd.DataFrame) -> dict[str, Any]:
    """The 12-month arrays of the season strip (R17, R18, R20)."""
    a3 = t["a3"].loc[lga].reindex(MONTHS)
    block = {"month": MONTHS} | {k: a3[col].tolist() for k, col in A3_MONTHLY.items()}
    block["ndvi_cropland"] = t["ndvi"].loc[lga].reindex(MONTHS)["ndvi_crop_sg"].tolist()
    block["bare_probability"] = t["a4"].loc[lga, [f"m{m:02d}" for m in MONTHS]].tolist()
    block["dry_spell_start_share"] = spells.loc[lga].tolist()
    return block


def season_types_block(lga: str, t: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """Each A5 season type: how often it comes here and its monthly profile (I8, F3c)."""
    profiles = t["a5_profiles"][t["a5_profiles"]["lga"] == lga]
    types = []
    for name, rows in profiles.groupby("season_type"):
        rows = rows.set_index("month").reindex(MONTHS)
        types.append(
            {
                "name": name,
                "share_years": t["a5_share"].loc[lga, name],
                "years": int(rows["n_years"].max()),
                "profile_source": str(rows["source"].iloc[0]),
                "monthly": {k: rows[col].tolist() for k, col in PROFILE.items()},
            }
        )
    return types


def trends_block(lga: str, t: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    """A7 trends that may reach the season strip and cards (trends.card_series)."""
    rows = t["a7"][(t["a7"]["lga"] == lga) & t["a7"]["on_card"]]
    keep = ["series", "card_line", "sen_slope_per_decade", "significant", "first_year", "last_year"]
    records: list[dict[str, Any]] = rows[keep].to_dict("records")
    return records


def onset_block(t: dict[str, pd.DataFrame], lga: str) -> dict[str, Any]:
    """Rain start, rain end and season length: median and the range 8 years in 10 fall in."""
    a2 = t["a2"]
    return {
        name: doy_range(a2, column).loc[lga].to_dict()
        for name, column in (
            ("onset_doy", "onset_doy"),
            ("cessation_doy", "cessation_doy"),
            ("length_days", "length_days"),
        )
    }


def flags_block(lga: str, t: dict[str, pd.DataFrame], cfg: ModelConfig) -> dict[str, Any]:
    """Data-quality flags that lower confidence or need saying on the strip."""
    static, conf = t["static"].loc[lga], t["a12"].loc[lga]
    return {
        "low_cropland": static["low_cropland"],
        "urban_lga": static["urban_lga"],
        "imerg_suspect": static["imerg_suspect"],
        "cropland_share": static["cropland_share"],
        "ndvi_filled_share": conf["ndvi_filled_share"],
        "smap_years": [cfg.water_balance.smap_years.start, cfg.water_balance.smap_years.end],
        "et0_solar_filled_years": t["solar_filled_years"]["year"].tolist(),
    }


def season_file(lga: str, t: dict[str, pd.DataFrame], cfg: ModelConfig) -> dict[str, Any]:
    """season/{lga}.json (PRD section 11)."""
    a2_all = t["a2"].dropna(subset=["onset_doy"])
    spells = dry_spell_months(t["spells"], a2_all.groupby("lga").size())
    a2 = a2_all[a2_all["lga"] == lga]
    body: dict[str, Any] = clean(
        {
            "contract_version": cfg.contract.version,
            "source": SOURCE,
            "lga": lga,
            "zone": cfg.lga_zone[lga],
            "years": {
                "climate": [cfg.contract.years.start, cfg.contract.years.end],
                "ndvi_and_season_types": [cfg.years.start, cfg.years.end],
            },
            "months": months_block(lga, t, spells),
            **onset_block(t, lga),
            "dry_spell": dry_spell_window(spells.loc[lga], cfg.contract.dry_spell_min_share)
            | {
                "share_years_10plus": float((a2["dry_spells_10plus"] > 0).mean()),
                "longest_median_days": float(a2["longest_dry_spell_days"].median()),
            },
            "season_types": season_types_block(lga, t),
            "trends": trends_block(lga, t),
            "erosion_band": t["a10"].loc[lga, "erosion_band"],
            "confidence": {
                "level": t["a12"].loc[lga, "level"],
                "reason": t["a12"].loc[lga, "reason"],
            },
            "flags": flags_block(lga, t, cfg),
        }
    )
    return body


def lgas_file(t: dict[str, pd.DataFrame], cfg: ModelConfig) -> dict[str, Any]:
    """lgas.json: every LGA with its zone, cropland share and low-cropland flag (I1)."""
    static = t["static"]
    body: dict[str, Any] = clean(
        {
            "contract_version": cfg.contract.version,
            "source": SOURCE,
            "lgas": [
                {
                    "lga": lga,
                    "zone": zone,
                    "cropland_share": static.loc[lga, "cropland_share"],
                    "low_cropland": static.loc[lga, "low_cropland"],
                }
                for lga, zone in sorted(cfg.lga_zone.items())
            ],
        }
    )
    return body


def contract_dir(cfg: ModelConfig) -> Path:
    """Where the app reads the contract, resolved from the repo root."""
    return CONFIG_DIR.parent / cfg.paths.contract


def write_contract(cfg: ModelConfig | None = None, dest: Path | None = None) -> Path:
    """Write lgas.json and one season/{lga}.json per LGA."""
    cfg = cfg or load_model_config()
    dest = dest or contract_dir(cfg)
    tables = load_inputs(cfg)
    (dest / "season").mkdir(parents=True, exist_ok=True)
    (dest / "lgas.json").write_text(json.dumps(lgas_file(tables, cfg), indent=1))
    for lga in sorted(cfg.lga_zone):
        body = json.dumps(season_file(lga, tables, cfg), indent=1)
        (dest / "season" / f"{lga}.json").write_text(body)
    return dest


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("contract").info("Contract files written to %s", write_contract())
