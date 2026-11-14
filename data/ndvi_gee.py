#!/usr/bin/env python3
"""
MODIS NDVI per Kaduna LGA, all land and cropland only, computed in Earth Engine.

Products: MODIS/061/MOD13Q1 (Terra MODIS 16-day 250 m vegetation indices, V6.1)
          ESA/WorldCover/v200 (10 m land cover; class 40 = cropland), PRD A1
Boundary: data/processed/kaduna_lgas.geojson (built by data/isda.py).

What it does
  1. Builds a 250 m cropland mask from WorldCover (a MODIS pixel counts as
     cropland when at least half of its 10 m WorldCover pixels are class 40)
     and the cropland share of every LGA; LGAs under 10% cropland are flagged
     low_cropland (PRD A1).
  2. For every 16-day composite since 2000-02-18, keeps pixels whose
     SummaryQA is good or marginal, and takes per LGA:
       ndvi_all    mean NDVI over all land
       ndvi_crop   mean NDVI over cropland only
       valid_crop  share of cropland pixels that passed the quality check
     One Earth Engine reduction per year (all composites stacked), cached per
     year, so a dropped connection loses nothing.
  3. PRD A4, bare-field detection, on the cropland series:
       composites with fewer than 30% valid cropland pixels are treated as
       missing and filled by linear interpolation (flagged interpolated);
       Savitzky-Golay smoothing, window 5 composites, order 2;
       a month is bare when smoothed cropland NDVI is below 0.25 (tunable);
       bare probability per LGA and month = share of years it was bare.
  4. PRD A6 target: mean smoothed cropland NDVI in August-September per
     LGA-year, as a z-score against that LGA's own mean.

Writes, in data/processed/ndvi/:
  lga_cropland.csv          cropland share and low_cropland flag per LGA
  ndvi_lga_composites.csv   one row per LGA per 16-day composite
  ndvi_lga_monthly.csv      monthly smoothed NDVI and bare flag per LGA-year
  ndvi_bare_probability.csv 12-month bare probability per LGA (A4 output)
  ndvi_lga_peak_season.csv  Aug-Sep cropland NDVI and anomaly z per LGA-year (A6)

Run (from the repo root)
  python -m data.ndvi_gee --start 2024-01-01 --end 2024-12-31 --out data/processed/ndvi_test
  python -m data.ndvi_gee
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

log = logging.getLogger("ndvi_gee")

COLLECTION = "MODIS/061/MOD13Q1"
WORLDCOVER = "ESA/WorldCover/v200"
CROPLAND_CLASS = 40
MODIS_START = dt.date(2000, 2, 18)
BANDS = ["ndvi_all", "ndvi_crop", "valid_crop"]
MIN_VALID = 0.30
SG_WINDOW, SG_ORDER = 5, 2
LOW_CROPLAND = 0.10
PEAK_MONTHS = (8, 9)
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
# Earth Engine
# --------------------------------------------------------------------------- #
def cropland_mask(proj):
    """Fraction of each 250 m MODIS pixel that WorldCover calls cropland."""
    import ee

    wc = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS)
    return (
        wc.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024)
        .reproject(crs=proj["crs"], crsTransform=proj["transform"])
        .rename("crop_frac")
    )


def pull_cropland(fc, crop_frac, proj):
    import ee

    reduced = crop_frac.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        crs=proj["crs"],
        crsTransform=proj["transform"],
        tileScale=4,
    )
    reduced = reduced.map(lambda f: ee.Feature(ee.Feature(None).copyProperties(f)))
    df = ee.data.computeFeatures({"expression": reduced, "fileFormat": "PANDAS_DATAFRAME"})
    df = df.rename(columns={"mean": "cropland_share"})[["lga", "cropland_share"]]
    df["low_cropland"] = df["cropland_share"] < LOW_CROPLAND
    return df


def pull_span(col, fc, crop, proj, first, last):
    """One reduceRegions per span: each composite contributes three bands."""
    import ee

    span = col.filterDate(first.isoformat(), (last + dt.timedelta(days=1)).isoformat())

    def add_composite(img, acc):
        img = ee.Image(img)
        p = ee.String("d").cat(img.date().format("YYYYMMdd")).cat("_")
        ndvi = img.select("NDVI").multiply(0.0001)
        ok = img.select("SummaryQA").bitwiseAnd(3).lte(1)  # good or marginal
        all_ = ndvi.updateMask(ok).rename(p.cat("ndvi_all"))
        crp = ndvi.updateMask(ok.And(crop)).rename(p.cat("ndvi_crop"))
        valid = ok.unmask(0).updateMask(crop).toFloat().rename(p.cat("valid_crop"))
        return ee.Image(acc).addBands(all_).addBands(crp).addBands(valid)

    stacked = ee.Image(span.iterate(add_composite, ee.Image().select([])))
    reduced = stacked.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        crs=proj["crs"],
        crsTransform=proj["transform"],
        tileScale=4,
    )
    reduced = reduced.map(lambda f: ee.Feature(ee.Feature(None).copyProperties(f)))
    wide = ee.data.computeFeatures({"expression": reduced, "fileFormat": "PANDAS_DATAFRAME"})
    return wide_to_long(wide, BANDS)


def pull_all(project, lgas, start, end, out, cache, workers):
    import ee

    ee.Initialize(project=project)
    col = ee.ImageCollection(COLLECTION).select(["NDVI", "SummaryQA"])
    proj = col.first().select("NDVI").projection().getInfo()
    last_img = ee.Date(col.aggregate_max("system:time_start")).format("YYYY-MM-dd").getInfo()
    log.info(f"  {COLLECTION} latest composite starts {last_img}")
    end = min(end, dt.date.fromisoformat(last_img))
    fc = to_ee_features(lgas)
    crop_frac = cropland_mask(proj)
    crop = crop_frac.gte(0.5)

    crop_csv = out / "lga_cropland.csv"
    if not crop_csv.exists():
        log.info("  computing cropland share per LGA (WorldCover class 40)")
        pull_cropland(fc, crop_frac, proj).to_csv(crop_csv, index=False)

    cache.mkdir(parents=True, exist_ok=True)
    refresh_after = end - dt.timedelta(days=60)
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
                df = pull_span(col, fc, crop, proj, first, last)
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
# A4: smoothing, bare months; A6: peak-season anomaly
# --------------------------------------------------------------------------- #
def smooth(series):
    try:
        from scipy.signal import savgol_filter
    except ImportError:
        sys.exit("scipy is needed for the Savitzky-Golay step: pip install scipy")
    if series.notna().sum() < SG_WINDOW:
        return series
    return pd.Series(savgol_filter(series.to_numpy(), SG_WINDOW, SG_ORDER), index=series.index)


def build_composites(cache, start, end):
    files = sorted(cache.glob("*.csv"))
    if not files:
        sys.exit("Nothing cached yet.")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"].dt.date >= start) & (d["date"].dt.date <= end)]
    d = d.drop_duplicates(["lga", "date"], keep="last").sort_values(["lga", "date"])

    parts = []
    for _lga, g in d.groupby("lga"):
        g = g.copy()
        bad = g["ndvi_crop"].isna() | (g["valid_crop"].fillna(0) < MIN_VALID)
        g["interpolated"] = bad
        g["ndvi_crop_filled"] = g["ndvi_crop"].mask(bad).interpolate(limit_direction="both")
        g["ndvi_crop_sg"] = smooth(g["ndvi_crop_filled"]).clip(-0.2, 1.0)
        parts.append(g)
    out = pd.concat(parts, ignore_index=True)
    out["year"], out["month"] = out["date"].dt.year, out["date"].dt.month
    return out


def monthly_bare(comp, threshold, mode="relative", rel_frac=0.25):
    """A month is bare when smoothed cropland NDVI is below the threshold.

    absolute: one threshold for every LGA (PRD default 0.25). Review showed it
      never fires in the south (Chikun, Kachia, Zangon Kataf), where trees in
      the farm mosaic keep dry-season cropland NDVI near 0.30.
    relative (default): per LGA, low + rel_frac x (high - low), with low/high =
      the 5th/95th percentile of that LGA's smoothed series. This is the PRD's
      "tuned per zone", made automatic and reproducible.
    """
    m = comp.groupby(["lga", "year", "month"], as_index=False).agg(
        ndvi_crop_sg=("ndvi_crop_sg", "mean"),
        ndvi_all=("ndvi_all", "mean"),
        composites=("date", "nunique"),
        interpolated_share=("interpolated", "mean"),
    )
    if mode == "relative":
        q = comp.groupby("lga")["ndvi_crop_sg"].quantile([0.05, 0.95]).unstack()
        thr = (q[0.05] + rel_frac * (q[0.95] - q[0.05])).rename("bare_threshold")
        m = m.merge(thr, on="lga")
    else:
        m["bare_threshold"] = threshold
    m["bare"] = m["ndvi_crop_sg"] < m["bare_threshold"]
    prob = (
        m.groupby(["lga", "month"])["bare"]
        .mean()
        .rename("bare_probability")
        .reset_index()
        .pivot(index="lga", columns="month", values="bare_probability")
    )
    prob.columns = [f"m{c:02d}" for c in prob.columns]
    return m, prob.reset_index()


def peak_season(comp):
    p = comp[comp["month"].isin(PEAK_MONTHS)]
    s = p.groupby(["lga", "year"], as_index=False).agg(
        peak_ndvi_crop=("ndvi_crop_sg", "mean"),
        composites=("date", "nunique"),
        interpolated_share=("interpolated", "mean"),
    )
    s = s[s["composites"] >= 3]  # need most of Aug-Sep
    base = s.groupby("lga")["peak_ndvi_crop"].agg(lga_mean="mean", lga_std="std")
    s = s.merge(base, on="lga")
    s["anomaly_z"] = (s["peak_ndvi_crop"] - s["lga_mean"]) / s["lga_std"]
    return s.drop(columns=["lga_std"])


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--project", help="Earth Engine cloud project (default: config.settings)")
    ap.add_argument("--start", default=MODIS_START.isoformat())
    ap.add_argument("--end", default=None, help="default: latest MODIS composite")
    ap.add_argument(
        "--bare-mode",
        choices=["relative", "absolute"],
        default="relative",
        help="relative: per-LGA threshold from its own NDVI range (default); "
        "absolute: --bare-threshold everywhere",
    )
    ap.add_argument(
        "--bare-threshold",
        type=float,
        default=0.25,
        help="absolute mode: smoothed cropland NDVI below this marks a bare month",
    )
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/ndvi"))
    ap.add_argument("--skip-pull", action="store_true", help="rebuild tables from the cache only")
    args = ap.parse_args()

    start = max(dt.date.fromisoformat(args.start), MODIS_START)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    out, cache = Path(args.out), Path(args.out) / "gee_cache"
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading LGAs")
    lgas = load_lgas(args.lgas)
    log.info(f"  {len(lgas)} LGAs")
    if not args.skip_pull:
        log.info(f"Computing in Earth Engine from {start}")
        end = pull_all(ee_project(args.project), lgas, start, end, out, cache, args.workers)

    crop_csv = out / "lga_cropland.csv"
    if crop_csv.exists():
        c = pd.read_csv(crop_csv)
        low = c.loc[c["low_cropland"], "lga"].tolist()
        log.info(
            f"  cropland share {c['cropland_share'].min():.0%} to {c['cropland_share'].max():.0%}; "
            f"low_cropland: {', '.join(low) or 'none'}"
        )

    log.info("Building tables")
    comp = build_composites(cache, start, end)
    comp.to_csv(out / "ndvi_lga_composites.csv", index=False)
    monthly, prob = monthly_bare(comp, args.bare_threshold, args.bare_mode)
    monthly.to_csv(out / "ndvi_lga_monthly.csv", index=False)
    prob.to_csv(out / "ndvi_bare_probability.csv", index=False)
    peak_season(comp).to_csv(out / "ndvi_lga_peak_season.csv", index=False)
    log.info(
        f"  {comp['date'].min():%Y-%m-%d} to {comp['date'].max():%Y-%m-%d}, "
        f"{comp['lga'].nunique()} LGAs, {len(comp)} composites; "
        f"{comp['interpolated'].mean():.1%} cropland values interpolated"
    )
    log.info(f"Done. Files in {out}/")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
