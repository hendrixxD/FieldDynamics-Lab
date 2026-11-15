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
  3. Mask to cropland (ESA WorldCover 2021, class 40, via data.gee_extras.crop_mask_for) and
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

from data.gee_extras import crop_mask_for, ee_project, load_lgas, to_ee_features

log = logging.getLogger("chirps_dry_spells_gee")

REPO_ROOT = Path(__file__).resolve().parents[1]
COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
BAND = "precipitation"
WET_MM = 1.0  # a dry day has under 1 mm, as in A2 (ETCCDI)
THRESHOLDS = (7, 10)  # days: A2's false-start length and A2's long dry spell
MONTHS = (6, 7, 8, 9)  # June to September, inside the rains
SCALE_M = 5566  # CHIRPS 0.05 degree pixel


def year_image(ee, year: int):
    """One image per year: band f'm{month}_{k}' is 1 where a k-day dry run reached k that month."""
    days = (
        ee.ImageCollection(COLLECTION)
        .filterDate(f"{year}-06-01", f"{year}-10-01")
        .select(BAND)
        .sort("system:time_start")
    )
    names = [f"m{m}_{k}" for m in MONTHS for k in THRESHOLDS]
    start = ee.Image.constant([0] * (1 + len(names))).rename(["run", *names]).toInt()

    def step(img, acc):
        acc = ee.Image(acc)
        month = ee.Date(img.get("system:time_start")).get("month")
        run = acc.select("run").add(1).multiply(img.lt(WET_MM)).rename("run").toInt()
        flags = [
            acc.select(f"m{m}_{k}")
            .Or(run.gte(k).And(ee.Image.constant(month.eq(m))))
            .rename(f"m{m}_{k}")
            .toInt()
            for m in MONTHS
            for k in THRESHOLDS
        ]
        return ee.Image.cat([run, *flags])

    return ee.Image(days.iterate(step, start)).select(names), days.first().projection()


def pull_year(ee, year: int, fc) -> pd.DataFrame:
    """Share of each LGA's cropland flagged per month and threshold, for one year."""
    image, proj = year_image(ee, year)
    crop = crop_mask_for(proj.getInfo())
    reduced = image.updateMask(crop).reduceRegions(fc, ee.Reducer.mean(), scale=SCALE_M)
    rows = [f["properties"] for f in reduced.getInfo()["features"]]
    wide = pd.DataFrame(rows)
    long = wide.melt(id_vars=["lga"], var_name="key", value_name="share")
    long[["month", "threshold"]] = long["key"].str.extract(r"m(\d+)_(\d+)").astype(int)
    table = long.pivot_table(index=["lga", "month"], columns="threshold", values="share")
    table.columns = [f"cropland_share_{k}plus" for k in table.columns]
    return table.reset_index().assign(year=year)


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
