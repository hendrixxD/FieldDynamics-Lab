"""Reading the feature tables and writing model outputs, shared by the training notebooks."""

import json
from pathlib import Path
from typing import Any

import pandas as pd

from model.settings import ModelConfig


def load_tables(features_dir: Path, cfg: ModelConfig) -> tuple[pd.DataFrame, ...]:
    """Year, month and static feature tables, the first two cut to the model years."""
    year = pd.read_parquet(features_dir / "lga_year_features.parquet")
    month = pd.read_parquet(features_dir / "lga_month_features.parquet")
    static = pd.read_parquet(features_dir / "lga_static.parquet")
    year, month = (
        d[d["year"].between(cfg.years.start, cfg.years.end)].reset_index(drop=True)
        for d in (year, month)
    )
    missing = sorted(set(year["lga"]) - set(cfg.lga_zone))
    if missing:
        raise ValueError(f"LGAs with no senatorial zone in config/models.yaml: {missing}")
    return year, month, static


def write_csv(table: pd.DataFrame, path: Path, digits: int = 4) -> Path:
    """Write a table with numeric columns rounded, creating the folder if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    numeric = table.select_dtypes("number").columns
    table.assign(**table[numeric].round(digits)).to_csv(path, index=False)
    return path


def write_json(summary: dict[str, Any], path: Path) -> Path:
    """Write a run summary as indented JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, default=float))
    return path
