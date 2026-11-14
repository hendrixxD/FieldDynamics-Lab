#!/usr/bin/env python3
"""
CHIRPS daily rainfall per Kaduna LGA, 1981 to date, computed in Earth Engine.

Product : UCSB-CHG/CHIRPS/DAILY (CHIRPS v2.0 Final, 0.05 deg, about 5.5 km),
          satellite infrared blended with rain-gauge records. Published with
          a lag of a few weeks to months.
Boundary: data/processed/kaduna_lgas.geojson (built by data/isda.py).

Why CHIRPS
  NASA POWER rainfall (MERRA-2 PRECTOTCORR) proved unusable for Kaduna: single
  days above 1,000 mm and annual totals swinging between ~500 and ~7,000 mm.
  IMERG is reliable but starts in 2000. CHIRPS is the standard long, gauge-
  blended rainfall record for West African agriculture and covers 1981 on,
  which the PRD's long-term rainfall trend (A7) and onset history (A2) need.
  Use --collection UCSB-CHC/CHIRPS/V3/DAILY_SAT for CHIRPS v3; note its daily
  split comes from IMERG, so it is no longer independent of IMERG.

What it does
  One Earth Engine reduction per year (every day stacked as a band), cached per
  year. Writes, in data/processed/chirps/:
    chirps_lga_daily.csv     one row per LGA per day (precip_mm)
    chirps_lga_monthly.csv   rain total, rain days, days above 50 mm, heaviest day
    chirps_lga_annual.csv    annual total and April-October season total per
                             LGA-year (A7 trend input), complete-year flag
    chirps_vs_imerg.csv      if processed/imerg/imerg_lga_monthly.csv exists:
                             per-LGA monthly correlation and bias, 2001 onward

Run (from the repo root)
  python -m data.chirps_gee --start 2024-01-01 --end 2024-12-31 --out data/processed/chirps_test
  python -m data.chirps_gee
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("chirps_gee")

DEFAULT_COLLECTION = "UCSB-CHG/CHIRPS/DAILY"
SOURCE_BAND = "precipitation"  # mm/day
CHIRPS_START = dt.date(1981, 1, 1)
WET_DAY_MM, HEAVY_MM = 1.0, 50.0
SEASON_MONTHS = range(4, 11)  # April to October
REPO_ROOT = Path(__file__).resolve().parents[1]


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
    lgas["geometry"] = lgas.geometry.simplify(0.0005, preserve_topology=True)
    return lgas[["lga", "geometry"]]


def to_ee_features(lgas):
    import ee

    return ee.FeatureCollection(
        [
            ee.Feature(ee.Geometry(r.geometry.__geo_interface__), {"lga": r.lga})
            for r in lgas.itertuples()
        ]
    )


def wide_to_long(wide, bands):
    value_cols = [c for c in wide.columns if c.startswith("d") and "_" in c and c[1:9].isdigit()]
    if not value_cols:
        return pd.DataFrame(columns=["lga", "date"] + bands)
    long = wide.melt(id_vars=["lga"], value_vars=value_cols, var_name="key", value_name="value")
    long["date"] = pd.to_datetime(long["key"].str[1:9], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    long["band"] = long["key"].str[10:]
    out = long.pivot_table(
        index=["lga", "date"], columns="band", values="value", aggfunc="first"
    ).reset_index()
    out.columns.name = None
    return out[["lga", "date"] + [b for b in bands if b in out.columns]]


# --------------------------------------------------------------------------- #
# Earth Engine pull
# --------------------------------------------------------------------------- #
def pull_span(col, fc, proj, first, last):
    """One reduceRegions for first..last: each daily image is one band."""
    import ee

    span = col.filterDate(first.isoformat(), (last + dt.timedelta(days=1)).isoformat()).select(
        SOURCE_BAND
    )

    def add_day(img, acc):
        img = ee.Image(img)
        name = ee.String("d").cat(img.date().format("YYYYMMdd")).cat("_precip_mm")
        return ee.Image(acc).addBands(img.rename(name))

    stacked = ee.Image(span.iterate(add_day, ee.Image().select([])))
    reduced = stacked.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        crs=proj["crs"],
        crsTransform=proj["transform"],
        tileScale=2,
    )
    reduced = reduced.map(lambda f: ee.Feature(ee.Feature(None).copyProperties(f)))
    wide = ee.data.computeFeatures({"expression": reduced, "fileFormat": "PANDAS_DATAFRAME"})
    return wide_to_long(wide, ["precip_mm"])


def pull_all(project, collection, lgas, start, end, cache, workers):
    import ee

    ee.Initialize(project=project)
    col = ee.ImageCollection(collection)
    proj = col.first().select(SOURCE_BAND).projection().getInfo()
    last_img = ee.Date(col.aggregate_max("system:time_start")).format("YYYY-MM-dd").getInfo()
    log.info(f"  {collection} available up to {last_img}")
    end = min(end, dt.date.fromisoformat(last_img))
    fc = to_ee_features(lgas)

    cache.mkdir(parents=True, exist_ok=True)
    refresh_after = end - dt.timedelta(days=120)  # preliminary days get revised
    jobs = []
    for y in range(start.year, end.year + 1):
        first, last = max(dt.date(y, 1, 1), start), min(dt.date(y, 12, 31), end)
        path = cache / f"{y}.csv"
        if path.exists() and last < refresh_after:
            continue
        jobs.append((first, last, path))
    log.info(f"  {len(jobs)} year(s) to compute in Earth Engine")

    def run(job):
        first, last, path = job
        err = None
        for attempt in range(1, 6):
            try:
                df = pull_span(col, fc, proj, first, last)
                df.to_csv(path, index=False)
                return f"{first.year} ok ({len(df)} rows)"
            except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
                err = exc
                time.sleep(15 * attempt)
        return f"{first.year} FAILED after 5 tries: {err}; rerun later"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run, j) for j in jobs]
        for n, fut in enumerate(as_completed(futs), 1):
            log.info(f"  [{n}/{len(jobs)}] {fut.result()}  ({time.time() - t0:.0f}s)")
    return end


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #
def _full_sum(s):
    return s.sum() if len(s) and s.notna().all() else np.nan


def build_daily(cache, start, end):
    files = sorted(cache.glob("*.csv"))
    if not files:
        sys.exit("Nothing cached yet.")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"].dt.date >= start) & (d["date"].dt.date <= end)]
    d = d.dropna(subset=["precip_mm"]).drop_duplicates(["lga", "date"], keep="last")
    d["year"], d["month"] = d["date"].dt.year, d["date"].dt.month
    return d.sort_values(["lga", "date"]).reset_index(drop=True)


def monthly_summary(daily):
    d = daily.copy()
    d["wet_day"] = d["precip_mm"] >= WET_DAY_MM
    d["heavy_day"] = d["precip_mm"] > HEAVY_MM
    m = d.groupby(["lga", "year", "month"], as_index=False).agg(
        days=("date", "nunique"),
        rain_mm=("precip_mm", _full_sum),
        rain_days=("wet_day", "sum"),
        heavy_days_50mm=("heavy_day", "sum"),
        max_daily_rain_mm=("precip_mm", "max"),
    )
    full = pd.to_datetime(dict(year=m["year"], month=m["month"], day=1)).dt.days_in_month
    m["complete_month"] = m["days"] == full
    m.loc[~m["complete_month"], ["rain_mm"]] = np.nan
    return m


def annual_summary(daily):
    d = daily.copy()
    d["in_season"] = d["month"].isin(SEASON_MONTHS)
    a = d.groupby(["lga", "year"], as_index=False).agg(
        days=("date", "nunique"),
        annual_mm=("precip_mm", "sum"),
        season_mm=("precip_mm", lambda s: s[d.loc[s.index, "in_season"]].sum()),
        rain_days=("precip_mm", lambda s: int((s >= WET_DAY_MM).sum())),
    )
    full = np.where(pd.to_datetime(a["year"].astype(str) + "-12-31").dt.is_leap_year, 366, 365)
    a["complete_year"] = a["days"] == full
    a.loc[~a["complete_year"], ["annual_mm", "season_mm", "rain_days"]] = np.nan
    return a


def cross_check_imerg(monthly, imerg_csv):
    im = pd.read_csv(imerg_csv)
    im = im[im["complete_month"].astype(bool) & ~im["trmm_era"].astype(bool)]
    j = (
        monthly[monthly["complete_month"]]
        .merge(
            im[["lga", "year", "month", "rain_mm"]].rename(columns={"rain_mm": "imerg_rain_mm"}),
            on=["lga", "year", "month"],
        )
        .dropna(subset=["rain_mm", "imerg_rain_mm"])
    )
    if j.empty:
        return None
    rows = []
    for lga, g in j.groupby("lga"):
        rows.append(
            {
                "lga": lga,
                "months": len(g),
                "corr_monthly": g["rain_mm"].corr(g["imerg_rain_mm"]),
                "chirps_mean_mm": g["rain_mm"].mean(),
                "imerg_mean_mm": g["imerg_rain_mm"].mean(),
                "bias_pct_chirps_vs_imerg": 100
                * (g["rain_mm"].mean() / g["imerg_rain_mm"].mean() - 1),
            }
        )
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--project", help="Earth Engine cloud project (default: config.settings)")
    ap.add_argument("--collection", default=DEFAULT_COLLECTION)
    ap.add_argument("--start", default=CHIRPS_START.isoformat())
    ap.add_argument("--end", default=None, help="default: latest CHIRPS day")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/chirps"))
    ap.add_argument(
        "--imerg", default=str(REPO_ROOT / "data/processed/imerg/imerg_lga_monthly.csv")
    )
    ap.add_argument("--skip-pull", action="store_true", help="rebuild tables from the cache only")
    args = ap.parse_args()

    start = max(dt.date.fromisoformat(args.start), CHIRPS_START)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    out, cache = Path(args.out), Path(args.out) / "gee_cache"
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading LGAs")
    lgas = load_lgas(args.lgas)
    log.info(f"  {len(lgas)} LGAs")
    if not args.skip_pull:
        log.info(f"Computing in Earth Engine from {start}")
        end = pull_all(
            ee_project(args.project), args.collection, lgas, start, end, cache, args.workers
        )

    log.info("Building tables")
    daily = build_daily(cache, start, end)
    daily.to_csv(out / "chirps_lga_daily.csv", index=False)
    monthly = monthly_summary(daily)
    monthly.to_csv(out / "chirps_lga_monthly.csv", index=False)
    annual = annual_summary(daily)
    annual.to_csv(out / "chirps_lga_annual.csv", index=False)
    log.info(
        f"  {daily['date'].min():%Y-%m-%d} to {daily['date'].max():%Y-%m-%d}, "
        f"{daily['lga'].nunique()} LGAs, {len(daily)} rows; "
        f"median annual rain {annual['annual_mm'].median():.0f} mm"
    )

    if Path(args.imerg).exists():
        cc = cross_check_imerg(monthly, args.imerg)
        if cc is not None:
            cc.to_csv(out / "chirps_vs_imerg.csv", index=False)
            log.info(
                f"  IMERG cross-check: monthly correlation median {cc['corr_monthly'].median():.3f}"
                f" (min {cc['corr_monthly'].min():.3f}, "
                f"{cc.loc[cc['corr_monthly'].idxmin(), 'lga']}); CHIRPS vs IMERG bias median "
                f"{cc['bias_pct_chirps_vs_imerg'].median():+.1f}%"
            )
    log.info(f"Done. Files in {out}/")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
