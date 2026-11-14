#!/usr/bin/env python3
"""
SMAP L4 soil moisture per Kaduna LGA, computed in Google Earth Engine.

Product : NASA/SMAP/SPL4SMGP/008 (SMAP L4 Global 3-hourly 9 km EASE-Grid
          Surface and Root Zone Soil Moisture Geophysical Data, V008),
          served by Earth Engine at 11 km pixels.
Boundary: data/processed/kaduna_lgas.geojson (built by data/isda.py).

Why Earth Engine
  The averaging over each LGA runs on Google's servers. Only the finished
  table comes back (about 23 LGAs x 4,200 days, a few MB), instead of
  pulling SMAP file blocks over mobile data.

What it does
  1. Loads the 23 LGAs and sends them to Earth Engine (slightly simplified,
     which does not change an 11 km average).
  2. For every day from 2015-03-31 to the latest available, averages the
     eight 3-hourly SMAP images into one daily image, then takes the
     area-weighted mean of each LGA. Earth Engine weights partly covered
     pixels, so small urban LGAs (Kaduna North, Kaduna South) still get a
     value; they are flagged as coarse because they cover about one pixel.
  3. Works month by month and caches each month as a CSV, so a dropped
     connection loses nothing: rerun and it continues. The two most recent
     months are always refreshed, because SMAP runs a few days behind.
  4. Writes:
       lga_daily.csv    one row per LGA per day
       lga_monthly.csv  monthly means + anomaly z-score vs that LGA's own
                        2015-to-latest mean for that month (PRD A3)
       lga_season.csv   one row per LGA per growing season: mean, anomaly,
                        share of dry days, rank of the year (PRD A6 feature)

Instrument outages
  SMAP L4 never has gaps: during the SMAP outages of 19 June to 23 July 2019
  and 6 August to 20 September 2022 the values come from the land model
  alone. Those days are flagged model_only=True so the confidence layer
  (PRD A12) can lower confidence for them.

Run (from the repo root, like data/isda.py)
  python -m data.smap_gee
  python -m data.smap_gee --start 2024-01-01 --end 2024-03-31 --out data/processed/smap_test
  Earth Engine project comes from config.settings (EE_PROJECT) or --project.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

log = logging.getLogger("smap_gee")

COLLECTION = "NASA/SMAP/SPL4SMGP/008"
BANDS = ["sm_rootzone", "sm_surface", "sm_rootzone_wetness"]
SMAP_START = dt.date(2015, 3, 31)
PIXEL_KM2 = 11.0 * 11.0
OUTAGES = [
    (dt.date(2019, 6, 19), dt.date(2019, 7, 23)),
    (dt.date(2022, 8, 6), dt.date(2022, 9, 20)),
]
REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def ee_project(cli_value):
    if cli_value:
        return cli_value
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from config.settings import Settings

        return Settings().ee_project
    except Exception as exc:  # noqa: BLE001 - any settings failure ends the run
        sys.exit(f"No Earth Engine project: pass --project, or set it in config.settings ({exc})")


def load_lgas(path):
    import geopandas as gpd

    lgas = gpd.read_file(path).to_crs(4326)
    if "lga" not in lgas.columns:
        sys.exit(f"{path} has no 'lga' column. Columns: {list(lgas.columns)}")
    lgas["area_km2"] = lgas.to_crs(6933).area / 1e6
    lgas["geometry"] = lgas.geometry.simplify(0.0005, preserve_topology=True)  # about 50 m
    return lgas[["lga", "area_km2", "geometry"]]


def to_ee_features(lgas):
    import ee

    feats = [
        ee.Feature(ee.Geometry(row.geometry.__geo_interface__), {"lga": row.lga})
        for row in lgas.itertuples()
    ]
    return ee.FeatureCollection(feats)


def month_starts(start, end):
    m = dt.date(start.year, start.month, 1)
    while m <= end:
        yield m
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)


# --------------------------------------------------------------------------- #
# Earth Engine pull
# --------------------------------------------------------------------------- #
def pull_month(col, fc, bands, proj, first, last):
    """Daily LGA means for first..last (inclusive), as a DataFrame."""
    import ee

    n = (last - first).days + 1
    start = ee.Date(first.isoformat())

    def per_day(i):
        d0 = start.advance(i, "day")
        day = col.filterDate(d0, d0.advance(1, "day")).select(bands)
        img = day.mean().set("n_images", day.size())
        reduced = img.reduceRegions(
            collection=fc,
            reducer=ee.Reducer.mean(),
            crs=proj["crs"],
            crsTransform=proj["transform"],
        )
        return reduced.map(
            lambda f: (
                ee.Feature(ee.Feature(None).copyProperties(f))
                .set("date", d0.format("YYYY-MM-dd"))
                .set("n_images", day.size())
            )
        )

    fcs = ee.List.sequence(0, n - 1).map(per_day)
    out = ee.FeatureCollection(fcs).flatten()
    df = ee.data.computeFeatures({"expression": out, "fileFormat": "PANDAS_DATAFRAME"})
    keep = ["lga", "date", "n_images"] + bands
    return df[[c for c in keep if c in df.columns]]


def pull_all(project, lgas, start, end, cache, workers):
    import ee

    ee.Initialize(project=project)
    col = ee.ImageCollection(COLLECTION)
    available = col.first().bandNames().getInfo()
    bands = [b for b in BANDS if b in available]
    missing = sorted(set(BANDS) - set(bands))
    if missing:
        log.info(f"  note: {COLLECTION} has no band(s) {missing}; continuing without them")
    proj = col.first().select(bands[0]).projection().getInfo()
    fc = to_ee_features(lgas)

    cache.mkdir(parents=True, exist_ok=True)
    refresh_after = dt.date.today() - dt.timedelta(days=62)
    jobs = []
    for m in month_starts(start, end):
        last = min(
            dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1) - dt.timedelta(days=1), end
        )
        first = max(m, start)
        path = cache / f"{m:%Y-%m}.csv"
        if path.exists() and last < refresh_after:
            continue
        jobs.append((first, last, path))
    log.info(f"  {len(jobs)} month(s) to compute in Earth Engine")

    def run(job):
        first, last, path = job
        for _attempt in range(1, 4):
            try:
                df = pull_month(col, fc, bands, proj, first, last)
                df.to_csv(path, index=False)
                return f"{first:%Y-%m} ok ({len(df)} rows)"
            except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
                err = exc
        return f"{first:%Y-%m} FAILED after 3 tries: {err}; rerun later"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, fut in enumerate(as_completed([pool.submit(run, j) for j in jobs]), 1):
            log.info(f"  [{n}/{len(jobs)}] {fut.result()}")
    return bands


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def build_daily(cache, lgas, start, end):
    files = sorted(cache.glob("*.csv"))
    if not files:
        sys.exit("Nothing cached yet.")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"].dt.date >= start) & (d["date"].dt.date <= end)]
    bands = [b for b in BANDS if b in d.columns]
    d = d.dropna(subset=bands, how="all")  # days SMAP has not published yet
    d = d.drop_duplicates(["lga", "date"], keep="last")

    d["model_only"] = False
    for a, b in OUTAGES:
        d.loc[(d["date"].dt.date >= a) & (d["date"].dt.date <= b), "model_only"] = True
    d = d.merge(lgas[["lga", "area_km2"]], on="lga", how="left")
    d["coarse"] = d["area_km2"] < 2 * PIXEL_KM2
    d["year"], d["month"] = d["date"].dt.year, d["date"].dt.month
    return d.sort_values(["lga", "date"]).reset_index(drop=True)


def monthly_summary(daily):
    vals = [b for b in BANDS if b in daily.columns]
    m = daily.groupby(["lga", "year", "month"], as_index=False).agg(
        **{b: (b, "mean") for b in vals},
        days=("date", "nunique"),
        model_only_days=("model_only", "sum"),
        coarse=("coarse", "first"),
    )
    clim = (
        m.groupby(["lga", "month"])["sm_rootzone"]
        .agg(clim_rootzone="mean", clim_rootzone_std="std")
        .reset_index()
    )
    m = m.merge(clim, on=["lga", "month"])
    m["anomaly_rootzone"] = m["sm_rootzone"] - m["clim_rootzone"]
    m["anomaly_z"] = m["anomaly_rootzone"] / m["clim_rootzone_std"]
    return m


def season_summary(daily, months, dry_percentile):
    s = daily[daily["month"].isin(months)].copy()
    wet = "sm_rootzone_wetness" if "sm_rootzone_wetness" in s else "sm_rootzone"
    thr = s.groupby("lga")[wet].quantile(dry_percentile / 100).rename("dry_threshold")
    s = s.merge(thr, on="lga")
    s["dry_day"] = s[wet] < s["dry_threshold"]
    out = s.groupby(["lga", "year"], as_index=False).agg(
        season_rootzone=("sm_rootzone", "mean"),
        season_wetness=(wet, "mean"),
        dry_day_share=("dry_day", "mean"),
        days=("date", "nunique"),
        model_only_days=("model_only", "sum"),
        coarse=("coarse", "first"),
    )
    full = len(
        pd.date_range("2001-01-01", "2001-12-31")
        .to_series()
        .dt.month.pipe(lambda x: x[x.isin(months)])
    )
    out["complete_season"] = out["days"] >= full - 3
    base = (
        out[out["complete_season"]]
        .groupby("lga")["season_rootzone"]
        .agg(lga_mean="mean", lga_std="std")
    )
    out = out.merge(base, on="lga", how="left")
    out["anomaly_pct"] = 100 * (out["season_rootzone"] - out["lga_mean"]) / out["lga_mean"]
    out["anomaly_z"] = (out["season_rootzone"] - out["lga_mean"]) / out["lga_std"]
    out["wettest_rank"] = (
        out[out["complete_season"]].groupby("lga")["season_rootzone"].rank(ascending=False)
    )
    return out.drop(columns=["lga_std"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_months(text):
    a, _, b = text.partition("-")
    a, b = int(a), int(b or a)
    if not (1 <= a <= b <= 12):
        raise argparse.ArgumentTypeError("months must look like 4-10")
    return list(range(a, b + 1))


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--project", help="Earth Engine cloud project (default: config.settings)")
    ap.add_argument("--start", default=SMAP_START.isoformat())
    ap.add_argument("--end", default=None, help="default today; unpublished days are dropped")
    ap.add_argument("--season-months", type=parse_months, default=parse_months("4-10"))
    ap.add_argument("--dry-percentile", type=float, default=20)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/smap"))
    ap.add_argument("--skip-pull", action="store_true", help="rebuild tables from the cache only")
    args = ap.parse_args()

    start = max(dt.date.fromisoformat(args.start), SMAP_START)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    out = Path(args.out)
    cache = out / "gee_cache"
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading LGAs")
    lgas = load_lgas(args.lgas)
    log.info(
        f"  {len(lgas)} LGAs; {int((lgas['area_km2'] < 2 * PIXEL_KM2).sum())} smaller than "
        f"two 11 km pixels (flagged coarse)"
    )

    if not args.skip_pull:
        log.info(f"Computing in Earth Engine, {start} to {end}")
        pull_all(ee_project(args.project), lgas, start, end, cache, args.workers)

    log.info("Building tables")
    daily = build_daily(cache, lgas, start, end)
    daily.to_csv(out / "lga_daily.csv", index=False)
    monthly_summary(daily).to_csv(out / "lga_monthly.csv", index=False)
    season_summary(daily, args.season_months, args.dry_percentile).to_csv(
        out / "lga_season.csv", index=False
    )
    log.info(
        f"Done. {daily['date'].min():%Y-%m-%d} to {daily['date'].max():%Y-%m-%d}, "
        f"{daily['lga'].nunique()} LGAs, {len(daily)} rows. Files in {out}/"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
