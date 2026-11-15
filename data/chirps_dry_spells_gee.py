#!/usr/bin/env python3
"""
Mid-season dry spells counted per CHIRPS pixel, then averaged over each LGA's cropland.

Why: A2 counts dry spells on LGA-average rain, and a day is dry there only when the whole LGA
gets under 1 mm, so local dry spells vanish (NiMet's July window never shows; see
docs/model-results.md, A2 fix). Here each 5.5 km pixel keeps its own count.

Method (all on Earth Engine's servers, one request per year)
  1. CHIRPS v2.0 daily (UCSB-CHG/CHIRPS/DAILY), 1 June to 30 September: inside the rains, after
     onset false starts and before the end-of-season dry-down that A2 used to count.
  2. Walk the days in order with ImageCollection.iterate. Per pixel: run = run + 1 on a dry day
     (under 1 mm), else 0. A month is flagged when the run reaches the threshold on one of its
     days (7 days: Sivakumar's false-start length, as in A2; 10 days: A2's long spell).
     One request per month (counting from 9 days before it) keeps each request small.
  3. Mask to cropland (ESA WorldCover 2021, class 40, most common class per 500 m cell) and
     take the mean per LGA: the share of the LGA's cropland in such a spell that month.
  A run already going on 1 June is counted from 1 June, so June shares are a lower bound.

Output: data/processed/chirps/chirps_lga_dry_spell_share.csv
  lga, year, month (6-9), cropland_share_7plus, cropland_share_10plus
Each year is cached in data/raw/chirps_dry_spells_cache/ so a dropped connection loses one year.

Run (from the repo root, Earth Engine authenticated)
  python -m data.chirps_dry_spells_gee --start-year 2024 --end-year 2024   # one year to test
  python -m data.chirps_dry_spells_gee                                     # 1981 to last year
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
from pathlib import Path

import pandas as pd

from data.gee_extras import CROPLAND_CLASS, WORLDCOVER, ee_project, load_lgas, to_ee_features

log = logging.getLogger("chirps_dry_spells_gee")

REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
BAND = "precipitation"
WET_MM = 1.0  # a dry day has under 1 mm, as in A2 (ETCCDI)
THRESHOLDS = (7, 10)  # days: A2's false-start length and A2's long dry spell
MONTHS = (6, 7, 8, 9)  # June to September, inside the rains
REDUCE_SCALE_M = 500  # cropland mask scale; CHIRPS (5.5 km) flags are read at it unchanged
LOOKBACK_DAYS = max(THRESHOLDS) - 1  # a spell reaching k days in a month can start k-1 days earlier
TILE_SCALE = 8  # smaller tiles for reduceRegions: Earth Engine's fix for its memory limit


def month_image(ee, year: int, month: int):
    """Band f'k{k}' is 1 where a k-day dry run reached k on a day of this month.

    The run is counted from up to LOOKBACK_DAYS before the month (never before 1 June), so a
    spell that starts late in the previous month is still seen. One month per request keeps the
    iterate short (about 40 days, 3 bands), which stays inside Earth Engine's memory limit.
    """
    first = dt.date(year, month, 1)
    start = max(dt.date(year, MONTHS[0], 1), first - dt.timedelta(days=LOOKBACK_DAYS))
    end = dt.date(year + (month == 12), month % 12 + 1, 1)
    days = (
        ee.ImageCollection(COLLECTION)
        .filterDate(start.isoformat(), end.isoformat())
        .select(BAND)
        .sort("system:time_start")
    )
    names = [f"k{k}" for k in THRESHOLDS]
    first_ms = ee.Date(first.isoformat()).millis()
    initial = ee.Image.constant([0] * (1 + len(names))).rename(["run", *names]).toInt()

    def step(img, acc):
        acc = ee.Image(acc)
        in_month = ee.Image.constant(ee.Number(img.get("system:time_start")).gte(first_ms))
        run = acc.select("run").add(1).multiply(img.lt(WET_MM)).rename("run").toInt()
        flags = [
            acc.select(f"k{k}").Or(run.gte(k).And(in_month)).rename(f"k{k}").toInt()
            for k in THRESHOLDS
        ]
        return ee.Image.cat([run, *flags])

    return ee.Image(days.iterate(step, initial)).select(names), days.first().projection()


def pull_month(ee, year: int, month: int, fc) -> pd.DataFrame:
    """Share of each LGA's cropland flagged in one month, per threshold."""
    image, _ = month_image(ee, year, month)
    # Cropland straight from WorldCover, reduced at 500 m: Earth Engine reads the map's stored
    # coarser levels (the most common class per cell) instead of averaging 10 m pixels on every
    # request, which ran out of memory. A cell counts as cropland when cropland is its most common
    # class, close to the >= 0.5 cropland fraction the other pulls use.
    crop = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS)
    reduced = image.updateMask(crop).reduceRegions(
        fc, ee.Reducer.mean(), scale=REDUCE_SCALE_M, tileScale=TILE_SCALE
    )
    rows = pd.DataFrame([f["properties"] for f in reduced.getInfo()["features"]])
    renamed = {f"k{k}": f"cropland_share_{k}plus" for k in THRESHOLDS}
    return rows.rename(columns=renamed).assign(year=year, month=month)


def pull_year(ee, year: int, fc) -> pd.DataFrame:
    """Every month of the window for one year."""
    return pd.concat([pull_month(ee, year, m, fc) for m in MONTHS], ignore_index=True)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--project", help="Earth Engine cloud project (default: config.settings)")
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--start-year", type=int, default=1981)
    ap.add_argument("--end-year", type=int, default=dt.date.today().year - 1)
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/chirps"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    import ee

    ee.Initialize(project=ee_project(args.project))
    fc = to_ee_features(load_lgas(args.lgas))
    cache = REPO_ROOT / "data/raw/chirps_dry_spells_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for year in range(args.start_year, args.end_year + 1):
        path = cache / f"{year}.csv"
        if not path.exists():
            pull_year(ee, year, fc).to_csv(path, index=False)
            log.info("%d done", year)
    years = range(args.start_year, args.end_year + 1)
    table = pd.concat([pd.read_csv(cache / f"{y}.csv") for y in years], ignore_index=True)
    out = Path(args.out) / "chirps_lga_dry_spell_share.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["lga", "year", "month", "cropland_share_7plus", "cropland_share_10plus"]
    table[cols].sort_values(["lga", "year", "month"]).to_csv(out, index=False)
    log.info("Wrote %d rows to %s", len(table), out)


if __name__ == "__main__":
    main()
