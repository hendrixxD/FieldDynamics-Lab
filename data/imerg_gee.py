#!/usr/bin/env python3
"""
GPM IMERG rainfall per Kaduna LGA, computed in Google Earth Engine.

Product : NASA/GPM_L3/IMERG_V07 (IMERG Final Run V07, half-hourly, 0.1 deg,
          about 11 km). The Final Run is gauge-adjusted and published a few
          months late, so the record ends a few months before today; use
          NASA POWER for the most recent months.
Boundary: data/processed/kaduna_lgas.geojson (built by data/isda.py).

What it does
  1. For every day, turns the 48 half-hourly images into two layers:
       precip_mm      daily rainfall total (mean rate x 24 h)
       max_rate_mmhr  the day's peak 30-minute rain rate (storm intensity)
     then takes the area-weighted mean per LGA. One Earth Engine reduction
     per month (all days stacked as bands), cached per month as a CSV, so a
     dropped connection loses nothing.
  2. Writes, in data/processed/imerg/:
       imerg_lga_daily.csv       one row per LGA per day
       imerg_lga_monthly.csv     rain total, rain days, days above 50 mm, days
                                 above the LGA's wet-day 95th percentile,
                                 heaviest day, peak 30-min rate (PRD A3)
       imerg_lga_indicators.csv  one row per LGA: wet-day 95th percentile,
                                 share of rain falling on those extreme days,
                                 mean yearly count of 50 mm days, typical
                                 storm peak rate (inputs to the PRD A10
                                 erosion index)
       imerg_vs_power.csv        if processed/power/power_lga_monthly.csv
                                 exists: per-LGA correlation and bias of
                                 monthly rain, IMERG vs POWER (the PRD's
                                 "cross-check of POWER rain")

Notes
  1998 to May 2000 come from IMERG's extension into the TRMM era, which NASA
  documents as less certain; those days are flagged trmm_era=True.

Run (from the repo root, like data/isda.py and data/smap_gee.py)
  python -m data.imerg_gee --start 2024-07-01 --end 2024-08-31 --out data/processed/imerg_test
  python -m data.imerg_gee
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

log = logging.getLogger("imerg_gee")

COLLECTION = "NASA/GPM_L3/IMERG_V07"
SOURCE_BAND = "precipitation"  # mm/hr, half-hourly
BANDS = ["precip_mm", "max_rate_mmhr"]
IMERG_START = dt.date(1998, 1, 1)
GPM_ERA = dt.date(2000, 6, 1)
WET_DAY_MM, HEAVY_MM, EXTREME_PCT = 1.0, 50.0, 95
REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Setup (same conventions as data/smap_gee.py)
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


def month_starts(start, end):
    m = dt.date(start.year, start.month, 1)
    while m <= end:
        yield m
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)


def month_end(m):
    return dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1) - dt.timedelta(days=1)


# --------------------------------------------------------------------------- #
# Earth Engine pull
# --------------------------------------------------------------------------- #
def wide_to_long(wide, bands):
    """lga x d<YYYYMMDD>_<band> columns -> one row per LGA per day."""
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


def pull_month(col, fc, proj, first, last):
    """One reduceRegions for the whole month: every day stacked as bands."""
    import ee

    month = col.filterDate(first.isoformat(), (last + dt.timedelta(days=1)).isoformat()).select(
        SOURCE_BAND
    )
    days = (
        month.aggregate_array("system:time_start")
        .map(lambda t: ee.Date(t).format("YYYYMMdd"))
        .distinct()
    )

    def add_day(day, acc):
        d0 = ee.Date.parse("YYYYMMdd", day)
        imgs = month.filterDate(d0, d0.advance(1, "day"))
        p = ee.String("d").cat(day).cat("_")
        total = imgs.mean().multiply(24).rename(p.cat("precip_mm"))
        peak = imgs.max().rename(p.cat("max_rate_mmhr"))
        count = ee.Image.constant(imgs.size()).toFloat().rename(p.cat("n_images"))
        return ee.Image(acc).addBands(total).addBands(peak).addBands(count)

    stacked = ee.Image(days.iterate(add_day, ee.Image().select([])))
    reduced = stacked.reduceRegions(
        collection=fc, reducer=ee.Reducer.mean(), crs=proj["crs"], crsTransform=proj["transform"]
    )
    reduced = reduced.map(lambda f: ee.Feature(ee.Feature(None).copyProperties(f)))
    wide = ee.data.computeFeatures({"expression": reduced, "fileFormat": "PANDAS_DATAFRAME"})
    return wide_to_long(wide, BANDS + ["n_images"])


def pull_all(project, lgas, start, end, cache, workers):
    import ee

    ee.Initialize(project=project)
    col = ee.ImageCollection(COLLECTION)
    proj = col.first().select(SOURCE_BAND).projection().getInfo()
    last_img = ee.Date(col.aggregate_max("system:time_start")).format("YYYY-MM-dd").getInfo()
    log.info(f"  {COLLECTION} available up to {last_img}")
    end = min(end, dt.date.fromisoformat(last_img))
    fc = to_ee_features(lgas)

    cache.mkdir(parents=True, exist_ok=True)
    # IMERG Final is revised as provisional months become permanent: refresh recent months
    refresh_after = end - dt.timedelta(days=150)
    jobs = []
    for m in month_starts(start, end):
        first, last = max(m, start), min(month_end(m), end)
        path = cache / f"{m:%Y-%m}.csv"
        if path.exists() and last < refresh_after:
            continue
        jobs.append((first, last, path))
    log.info(f"  {len(jobs)} month(s) to compute in Earth Engine")

    def run(job):
        first, last, path = job
        err = None
        for attempt in range(1, 6):
            try:
                df = pull_month(col, fc, proj, first, last)
                df.to_csv(path, index=False)
                return f"{first:%Y-%m} ok ({len(df)} rows)"
            except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
                err = exc
                time.sleep(15 * attempt)
        return f"{first:%Y-%m} FAILED after 5 tries: {err}; rerun later"

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
    d = d.dropna(subset=BANDS, how="all").drop_duplicates(["lga", "date"], keep="last")
    d["trmm_era"] = d["date"].dt.date < GPM_ERA
    d["year"], d["month"] = d["date"].dt.year, d["date"].dt.month
    return d.sort_values(["lga", "date"]).reset_index(drop=True)


def wet_day_threshold(daily):
    """Each LGA's 95th percentile of wet-day rain, GPM era only (more reliable)."""
    wet = daily[(daily["precip_mm"] >= WET_DAY_MM) & ~daily["trmm_era"]]
    return wet.groupby("lga")["precip_mm"].quantile(EXTREME_PCT / 100).rename("p95_wet_mm")


def monthly_summary(daily, p95):
    d = daily.merge(p95, on="lga")
    d["wet_day"] = (d["precip_mm"] >= WET_DAY_MM).where(d["precip_mm"].notna())
    d["heavy_day"] = (d["precip_mm"] > HEAVY_MM).where(d["precip_mm"].notna())
    d["extreme_day"] = (d["precip_mm"] > d["p95_wet_mm"]).where(d["precip_mm"].notna())
    m = d.groupby(["lga", "year", "month"], as_index=False).agg(
        days=("date", "nunique"),
        rain_mm=("precip_mm", _full_sum),
        rain_days=("wet_day", _full_sum),
        heavy_days_50mm=("heavy_day", _full_sum),
        extreme_days_p95=("extreme_day", _full_sum),
        max_daily_rain_mm=("precip_mm", "max"),
        peak_rate_mmhr=("max_rate_mmhr", "max"),
        trmm_era=("trmm_era", "any"),
    )
    full = pd.to_datetime(dict(year=m["year"], month=m["month"], day=1)).dt.days_in_month
    m["complete_month"] = m["days"] == full
    return m


def lga_indicators(daily, p95):
    d = daily[~daily["trmm_era"]].merge(p95, on="lga")
    # per-year figures use complete calendar years only (2000 and the latest year are partial)
    n = d.groupby(["lga", "year"])["date"].transform("nunique")
    d = d[n >= 365]
    years = d.groupby("lga")["year"].nunique()
    wet = d[d["precip_mm"] >= WET_DAY_MM]
    out = pd.DataFrame(
        {
            "p95_wet_mm": p95,
            "mean_annual_rain_mm": d.groupby("lga")["precip_mm"].sum() / years,
            "extreme_rain_share": (
                wet[wet["precip_mm"] > wet["p95_wet_mm"]].groupby("lga")["precip_mm"].sum()
                / wet.groupby("lga")["precip_mm"].sum()
            ),
            "heavy_days_50mm_per_year": (d["precip_mm"] > HEAVY_MM).groupby(d["lga"]).sum() / years,
            "wet_day_peak_rate_p50_mmhr": wet.groupby("lga")["max_rate_mmhr"].median(),
            "wet_day_peak_rate_p90_mmhr": wet.groupby("lga")["max_rate_mmhr"].quantile(0.9),
            "years": years,
        }
    )
    return out.reset_index().rename(columns={"index": "lga"})


def cross_check_power(monthly, power_csv):
    p = pd.read_csv(power_csv)[["lga", "year", "month", "rain_mm"]].rename(
        columns={"rain_mm": "power_rain_mm"}
    )
    j = (
        monthly[monthly["complete_month"] & ~monthly["trmm_era"]]
        .merge(p, on=["lga", "year", "month"])
        .dropna(subset=["rain_mm", "power_rain_mm"])
    )
    if j.empty:
        return None
    rows = []
    for lga, g in j.groupby("lga"):
        rows.append(
            {
                "lga": lga,
                "months": len(g),
                "corr_monthly": g["rain_mm"].corr(g["power_rain_mm"]),
                "imerg_mean_mm": g["rain_mm"].mean(),
                "power_mean_mm": g["power_rain_mm"].mean(),
                "bias_pct": 100 * (g["power_rain_mm"].mean() / g["rain_mm"].mean() - 1),
            }
        )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--project", help="Earth Engine cloud project (default: config.settings)")
    ap.add_argument("--start", default=IMERG_START.isoformat())
    ap.add_argument("--end", default=None, help="default: latest IMERG Final day")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/imerg"))
    ap.add_argument(
        "--power",
        default=str(REPO_ROOT / "data/processed/power/power_lga_monthly.csv"),
        help="POWER monthly table for the rain cross-check (skipped if missing)",
    )
    ap.add_argument("--skip-pull", action="store_true", help="rebuild tables from the cache only")
    args = ap.parse_args()

    start = max(dt.date.fromisoformat(args.start), IMERG_START)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    out, cache = Path(args.out), Path(args.out) / "gee_cache"
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading LGAs")
    lgas = load_lgas(args.lgas)
    log.info(f"  {len(lgas)} LGAs")

    if not args.skip_pull:
        log.info(f"Computing in Earth Engine from {start}")
        end = pull_all(ee_project(args.project), lgas, start, end, cache, args.workers)

    log.info("Building tables")
    daily = build_daily(cache, start, end)
    p95 = wet_day_threshold(daily)
    daily.to_csv(out / "imerg_lga_daily.csv", index=False)
    monthly = monthly_summary(daily, p95)
    monthly.to_csv(out / "imerg_lga_monthly.csv", index=False)
    lga_indicators(daily, p95).to_csv(out / "imerg_lga_indicators.csv", index=False)
    log.info(
        f"  {daily['date'].min():%Y-%m-%d} to {daily['date'].max():%Y-%m-%d}, "
        f"{daily['lga'].nunique()} LGAs, {len(daily)} rows"
    )

    if Path(args.power).exists():
        cc = cross_check_power(monthly, args.power)
        if cc is not None:
            cc.to_csv(out / "imerg_vs_power.csv", index=False)
            log.info(
                f"  POWER cross-check: monthly rain correlation median "
                f"{cc['corr_monthly'].median():.3f} (min {cc['corr_monthly'].min():.3f}, "
                f"{cc.loc[cc['corr_monthly'].idxmin(), 'lga']}); POWER vs IMERG bias median "
                f"{cc['bias_pct'].median():+.1f}%"
            )
    else:
        log.info(f"  no POWER table at {args.power}; cross-check skipped")
    log.info(f"Done. Files in {out}/")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
