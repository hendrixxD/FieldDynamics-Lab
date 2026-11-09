"""Kaduna's LGA boundaries from geoBoundaries."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import requests
import yaml
from pydantic import BaseModel, HttpUrl, PositiveInt

from config import CONFIG_DIR

DOWNLOAD_TIMEOUT_S = 120


class BoundariesConfig(BaseModel):
    """Settings from config/boundaries.yaml."""

    state_url: HttpUrl
    lga_url: HttpUrl
    state_name: str
    expected_lga_count: PositiveInt


def load_boundaries_config(path: Path = CONFIG_DIR / "boundaries.yaml") -> BoundariesConfig:
    """Read and validate the boundaries config."""
    return BoundariesConfig.model_validate(yaml.safe_load(path.read_text()))


def fetch(url: str, path: Path) -> Path:
    """Download url to path once. Raw pulls are never edited, so an existing file is kept."""
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=DOWNLOAD_TIMEOUT_S)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def select_state_lgas(
    states: gpd.GeoDataFrame, lgas: gpd.GeoDataFrame, state_name: str, expected_count: int
) -> gpd.GeoDataFrame:
    """Return the LGAs inside one state, with an 'lga' name column.

    geoBoundaries ADM2 has no parent state field, so an LGA belongs to the state that holds
    a point guaranteed to sit inside it (representative_point, not the centroid, which can
    fall outside an oddly shaped LGA).
    """
    state = states[states["shapeName"] == state_name]
    if len(state) != 1:
        raise ValueError(f"Expected one state named {state_name!r}, found {len(state)}")
    inside = lgas.representative_point().within(state.geometry.iloc[0])
    selected = lgas.loc[inside, ["shapeName", "shapeID", "geometry"]]
    selected = selected.rename(columns={"shapeName": "lga", "shapeID": "lga_id"})
    if len(selected) != expected_count:
        raise ValueError(
            f"Expected {expected_count} LGAs in {state_name}, found {len(selected)}: "
            f"{sorted(selected['lga'])}"
        )
    return selected.sort_values("lga").reset_index(drop=True)


def load_kaduna_lgas(config: BoundariesConfig, raw_dir: Path, crs: str) -> gpd.GeoDataFrame:
    """Download (once) and return Kaduna's LGAs in the given CRS."""
    folder = raw_dir / "boundaries"
    states_path = fetch(str(config.state_url), folder / "geoBoundaries-NGA-ADM1.geojson")
    lgas_path = fetch(str(config.lga_url), folder / "geoBoundaries-NGA-ADM2.geojson")
    return select_state_lgas(
        gpd.read_file(states_path).to_crs(crs),
        gpd.read_file(lgas_path).to_crs(crs),
        config.state_name,
        config.expected_lga_count,
    )
