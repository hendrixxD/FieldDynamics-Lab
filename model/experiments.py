"""The experiment log, docs/experiments.csv: one row per model run and validation scheme.

The training notebooks call these after each run (engineering standards, section 4).
"""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pandas as pd

from model.settings import ModelConfig


def today() -> str:
    """Run date for the log."""
    return dt.date.today().isoformat()


A5_LOG_METRICS = (
    "silhouette",
    "davies_bouldin",
    "seed_ari_min",
    "gmm_agreement",
    "stressed_gap_p_year_block",
)


def a5_row(a5: dict[str, Any], cfg: ModelConfig, today: str) -> dict[str, Any]:
    """The A5 run as one experiment log row."""
    return {
        "date": today,
        "model": f"A5 K-means k={a5['k']}",
        "features": ";".join(cfg.season_types.features),
        "split": "none (unsupervised)",
        "seed": cfg.random_seed,
        "metrics": json.dumps({k: a5[k] for k in A5_LOG_METRICS}),
        "notes": f"weather-only within-LGA anomalies; best silhouette k={a5['k_best_silhouette']}",
    }


def a6_rows(
    a6: dict[str, Any], metrics: pd.DataFrame, cfg: ModelConfig, today: str
) -> list[dict[str, Any]]:
    """One experiment log row per A6 model and validation scheme."""
    sm = cfg.stress_model
    features = ";".join([*sm.weather_features, *sm.static_features])
    rows = []
    for rec in metrics.to_dict(orient="records"):
        scores = {k: round(rec[k], 4) for k in ("rmse", "mae", "skill_vs_climatology")}
        optimistic = rec["model"] == "lgbm_tuned"
        rows.append(
            {
                "date": today,
                "model": f"A6 {rec['model']}",
                "features": features,
                "split": rec["scheme"],
                "seed": cfg.random_seed,
                "metrics": json.dumps(scores),
                "notes": "Optuna-tuned on the same years: optimistic"
                if optimistic
                else f"shipped={a6['shipped']}; has_skill={a6['has_skill']}",
            }
        )
    return rows


def a9_row(a9: dict[str, Any], today: str) -> dict[str, Any]:
    """The A9 water balance run as one experiment log row."""
    return {
        "date": today,
        "model": "A9 FAO-56 daily bucket",
        "features": "CHIRPS daily rain;POWER daily ET0 (Penman-Monteith);iSDA AWC;A2 onset",
        "split": "SMAP L4 root zone check, in-season days 2015-2025",
        "seed": "none (deterministic)",
        "metrics": json.dumps(
            {
                "smap_pearson_median": a9["smap_pearson_median"],
                "smap_pearson_range": a9["smap_pearson_range"],
                "short_share_by_crop": a9["short_share_by_crop"],
            }
        ),
        "notes": "Hargreaves ET0 baseline in a9_summary.json",
    }


def a8_rows(scores: pd.DataFrame, chosen: str, today: str) -> list[dict[str, Any]]:
    """One row per A8 soil water method, scored against OpenLandMap."""
    rows = []
    for method, group in scores.groupby("method"):
        metrics = {
            f"{getattr(r, 'reference', 'ref')}_{r.layer}_{r.quantity}_{k}": round(getattr(r, k), 4)
            for r in group.itertuples()
            for k in ("bias", "mae", "r")
        }
        rows.append(
            {
                "date": today,
                "model": f"A8 {method}",
                "features": "iSDA sand;silt;clay;organic carbon;bulk density;CEC;pH;stone",
                "split": "OpenLandMap cropland and all-land, 33 and 1500 kPa, 23 LGAs (no fitting)",
                "seed": "none (deterministic)",
                "metrics": json.dumps(metrics),
                "notes": "chosen for A9" if method == chosen else "",
            }
        )
    return rows


def log_experiments(path: Path, rows: list[dict[str, Any]]) -> None:
    """Append runs to the experiment log, replacing same-day runs of the same model and split."""
    new = pd.DataFrame(rows)
    existing = pd.read_csv(path) if path.exists() and path.stat().st_size else new.iloc[:0]
    key = ["date", "model", "split"]
    merged = existing.merge(new[key], on=key, how="left", indicator=True)
    kept = existing[(merged["_merge"] == "left_only").to_numpy()]
    pd.concat([kept, new], ignore_index=True).to_csv(path, index=False)


def component_row(
    model: str, features: str, split: str, metrics: dict[str, Any], notes: str, today: str
) -> dict[str, Any]:
    """One log row for a rule-based or statistical component (A4 bare fields, A7 trends)."""
    return {
        "date": today,
        "model": model,
        "features": features,
        "split": split,
        "seed": "none (deterministic)",
        "metrics": json.dumps(metrics),
        "notes": notes,
    }
