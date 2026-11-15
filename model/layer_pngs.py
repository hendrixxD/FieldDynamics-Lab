"""Precompute 5 km state PNG overlays from config/layers.yaml and the serving bundle."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

import numpy as np
from matplotlib.image import imsave
from matplotlib.path import Path as MplPath

from config import CONFIG_DIR
from model.engine_data import EngineData, load_bundle
from model.view_data import (
    _read_kaduna_lgas_geojson,
    load_layers_config,
    map_trends_summary,
    read_season_contract,
)

log = logging.getLogger(__name__)

STATIC_LAYERS_DIR = CONFIG_DIR.parent / "static" / "layers"


def _hex_to_rgb(hex_colour: str) -> tuple[int, int, int]:
    """Convert `#rrggbb` colour strings from config/layers.yaml into 8-bit RGB."""
    clean = hex_colour.lstrip("#")
    return int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16)


def _polygon_rings(geom: dict[str, Any]) -> list[tuple[np.ndarray, list[np.ndarray]]]:
    """Return `(outer_ring, holes)` coordinate arrays (lon, lat) for Polygon/MultiPolygon."""
    gtype = geom.get("type")
    coords = geom.get("coordinates", [])
    if gtype == "Polygon" and coords:
        return [
            (np.asarray(coords[0], dtype=float), [np.asarray(h, dtype=float) for h in coords[1:]])
        ]
    if gtype == "MultiPolygon":
        return [
            (np.asarray(poly[0], dtype=float), [np.asarray(h, dtype=float) for h in poly[1:]])
            for poly in coords
            if poly
        ]
    return []


def compute_geojson_bounds(geojson_data: dict[str, Any]) -> tuple[float, float, float, float]:
    """Compute `(min_lat, min_lon, max_lat, max_lon)` envelope in EPSG:4326."""
    rings = [
        ring
        for feat in geojson_data.get("features", [])
        for outer, holes in _polygon_rings(feat.get("geometry", {}))
        for ring in [outer, *holes]
    ]
    all_pts = np.vstack(rings)
    min_lon, min_lat = float(all_pts[:, 0].min()), float(all_pts[:, 1].min())
    max_lon, max_lat = float(all_pts[:, 0].max()), float(all_pts[:, 1].max())
    return min_lat, min_lon, max_lat, max_lon


def rasterize_lga_index(
    geojson_data: dict[str, Any],
    bounds: tuple[float, float, float, float],
    canonical_lgas: list[str],
    height: int = 200,
    width: int = 240,
) -> tuple[np.ndarray, list[str]]:
    """Rasterize 23 LGA polygons onto an EPSG:4326 grid (-1 outside Kaduna)."""
    min_lat, min_lon, max_lat, max_lon = bounds
    lons = np.linspace(min_lon, max_lon, width, dtype=float)
    lats = np.linspace(max_lat, min_lat, height, dtype=float)
    gx, gy = np.meshgrid(lons, lats)
    points = np.column_stack([gx.ravel(), gy.ravel()])

    by_case = {name.casefold(): name for name in canonical_lgas}
    grid_idx = np.full(points.shape[0], -1, dtype=np.int32)
    lgas: list[str] = []
    for idx, feat in enumerate(geojson_data.get("features", [])):
        raw_name = str(feat["properties"]["lga"])
        lga_name = by_case.get(raw_name.casefold(), raw_name)
        lgas.append(lga_name)
        for outer, holes in _polygon_rings(feat.get("geometry", {})):
            inside = MplPath(outer[:, :2]).contains_points(points)
            for hole in holes:
                inside &= ~MplPath(hole[:, :2]).contains_points(points)
            grid_idx[inside] = idx
    return grid_idx.reshape(height, width), lgas


def lga_layer_values(data: EngineData, lgas: list[str]) -> dict[str, np.ndarray]:
    """Extract per-LGA scalar arrays for the four precomputed map layers."""
    trends = map_trends_summary(data)
    soil_z = [
        float(read_season_contract(lga)["months"]["soil_moisture_anomaly_z"][6]) for lga in lgas
    ]
    heat_d = [float(trends[lga]["heat_days_yr"]) for lga in lgas]
    erosion = [float(trends[lga]["erosion_index"]) for lga in lgas]
    stress = [float(data.water.xs(lga, level=0)["short_share"].mean()) * 100.0 for lga in lgas]
    return {
        "soil_moisture": np.asarray(soil_z, dtype=float),
        "heat_stress": np.asarray(heat_d, dtype=float),
        "erosion_risk": np.asarray(erosion, dtype=float),
        "water_stress": np.asarray(stress, dtype=float),
    }


def colour_grid_rgba(
    values: np.ndarray, valid_mask: np.ndarray, stops: list[dict[str, Any]]
) -> np.ndarray:
    """Interpolate layer values to an `(H, W, 4)` uint8 RGBA array using YAML stops."""
    ordered = sorted(stops, key=lambda s: float(s["value"]))
    stop_vals = np.asarray([float(s["value"]) for s in ordered], dtype=float)
    rgb = np.asarray([_hex_to_rgb(str(s["colour"])) for s in ordered], dtype=float)

    rgba = np.zeros((*values.shape, 4), dtype=np.uint8)
    valid_vals = values[valid_mask]
    for channel in range(3):
        interpolated = np.interp(valid_vals, stop_vals, rgb[:, channel])
        rgba[valid_mask, channel] = np.clip(np.rint(interpolated), 0, 255).astype(np.uint8)
    rgba[valid_mask, 3] = 225
    return rgba


def build_state_layer_pngs(
    out_dir: Path = STATIC_LAYERS_DIR, height: int = 200, width: int = 240
) -> list[Path]:
    """Write `bounds.json` and 5 km state PNG overlays under `static/layers/state/`."""
    data = load_bundle()
    geojson_data = _read_kaduna_lgas_geojson()
    bounds = compute_geojson_bounds(geojson_data)
    min_lat, min_lon, max_lat, max_lon = bounds
    grid_idx, lgas = rasterize_lga_index(
        geojson_data, bounds, list(data.zone_of.keys()), height=height, width=width
    )
    valid_mask = grid_idx >= 0
    layer_vals = lga_layer_values(data, lgas)
    layers_cfg = load_layers_config()

    state_dir = out_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for layer_name, vals in layer_vals.items():
        if layer_name not in layers_cfg:
            continue
        raster = np.zeros(grid_idx.shape, dtype=float)
        raster[valid_mask] = vals[grid_idx[valid_mask]]
        rgba = colour_grid_rgba(raster, valid_mask, layers_cfg[layer_name]["stops"])
        target = state_dir / f"{layer_name}.png"
        imsave(target, rgba)
        written.append(target)

    meta = {
        "crs": "EPSG:4326",
        "bounds": [[round(min_lat, 5), round(min_lon, 5)], [round(max_lat, 5), round(max_lon, 5)]],
        "layers": sorted(p.stem for p in written),
    }
    bounds_path = out_dir / "bounds.json"
    bounds_path.write_text(json.dumps(meta, indent=2) + "\n")
    written.append(bounds_path)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="Precompute 5 km Kaduna state map overlay PNGs.")
    parser.add_argument("--out", type=Path, default=STATIC_LAYERS_DIR)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    paths = build_state_layer_pngs(args.out)
    for path in paths:
        log.info("Wrote %s", path)


if __name__ == "__main__":
    main()
