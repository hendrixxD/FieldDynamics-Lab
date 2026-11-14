#!/usr/bin/env python3
"""
Extra Earth Engine datasets per Kaduna LGA: one registry, one command each.

Every dataset is averaged per LGA on Google's servers (one reduction per
month or year, every date stacked as bands), cached per chunk as CSV so a
dropped connection loses nothing, and written to
data/processed/extras/<dataset>/:
    <dataset>_lga.csv          one row per LGA per date (native time step)
    <dataset>_lga_monthly.csv  monthly means; rate bands also get a monthly total

Datasets (python -m data.gee_extras --list)
  srtm        NASA SRTM 30 m: elevation, slope; share of cropland on slopes
              above 2% and 5% (PRD A10 erosion slope term). Static.
  lst         MODIS MOD11A2 V6.1 land surface temperature, day and night, 8-day
              1 km, 2000 on (heat stress at the surface; ECOSTRESS stand-in).
  et          MODIS MOD16A2GF V6.1 actual and potential evapotranspiration,
              8-day 500 m, 2000-2024 (actual/potential ET = crop water stress).
  viirs_ndvi  VIIRS VNP13A1 V2 NDVI, 16-day 500 m, 2012 on, all land and
              cropland (MODIS NDVI continuity as Terra ages).
  gldas       GLDAS-2.1 Noah land model: root-zone (0-100 cm) and 0-10 cm soil
              moisture, evapotranspiration, daily from 3-hourly, 0.25 deg,
              2000 on (soil moisture before SMAP's 2015 start).
  era5land    ERA5-Land daily: max/min temperature, dew point, rain, solar
              radiation, 2 m wind, soil water layers 1-2, 0.1 deg, 1981 on
              (independent check on POWER temperature and ET0 inputs).
  hls         NASA HLS v2 (Landsat 8/9 + Sentinel-2) NDVI at 30 m: median of
              cloud-free scenes in the dry season (Jan-Mar) and peak season
              (Aug-Sep) per year, all land and cropland, 2013 on.
  openlandmap OpenLandMap soil water content at 33 kPa (field capacity),
              250 m, at 0, 10, 30, 60, 100 and 200 cm. Earth Engine has no
              1500 kPa (wilting point) layer, so only field capacity is checked
              (Hengl and Gupta 2019): mapped from measured soil profiles with no
              pedotransfer functions. Cropland mean per LGA. Static. The
              independent reference for choosing the A8 soil water model.

Run (from the repo root)
  python -m data.gee_extras --list
  python -m data.gee_extras srtm
  python -m data.gee_extras openlandmap
  python -m data.gee_extras lst --start 2024-01-01 --end 2024-12-31 --out data/processed/extras_test
  python -m data.gee_extras all          # every dataset in turn
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import math
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger("gee_extras")

REPO_ROOT = Path(__file__).resolve().parents[1]
WORLDCOVER = "ESA/WorldCover/v200"
CROPLAND_CLASS = 40


# --------------------------------------------------------------------------- #
# Shared helpers (same conventions as data/smap_gee.py)
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


def wide_to_long(wide, bands, key_len=9):
    """lga x d<YYYYMMDD>_<band> columns -> one row per LGA per date."""
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


def crop_mask_for(proj, steps=((100, 1024),)):
    """WorldCover cropland fraction at the dataset's projection, >= 0.5 -> cropland.

    For coarse grids the 10 m map is first averaged to 100 m, then to the
    target grid, which keeps each reduceResolution within Earth Engine limits.
    """
    import ee

    img = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS).toFloat()
    for scale, max_px in steps:
        img = img.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=max_px).reproject(
            crs="EPSG:4326", scale=scale
        )
    img = img.reduceResolution(reducer=ee.Reducer.mean(), maxPixels=1024).reproject(
        crs=proj["crs"], crsTransform=proj["transform"]
    )
    return img.gte(0.5)


def computed(reduced):
    import ee

    reduced = reduced.map(lambda f: ee.Feature(ee.Feature(None).copyProperties(f)))
    return ee.data.computeFeatures({"expression": reduced, "fileFormat": "PANDAS_DATAFRAME"})


# --------------------------------------------------------------------------- #
# Dataset registry
# --------------------------------------------------------------------------- #
@dataclass
class Series:
    name: str
    collection: str
    start: dt.date
    bands: list[str]  # output band names, in order
    transform: Callable  # ee.Image -> ee.Image with `bands`
    step: str = "native"  # "native": one date per image; "daily": mean of a day's images
    chunk: str = "year"  # "year" or "month"
    rate_bands: dict = field(default_factory=dict)  # band -> unit per day, monthly total added
    cropland: bool = False  # add <band>_crop versions on cropland only
    end: dt.date | None = None
    refresh_days: int = 120
    note: str = ""


def _lst(img):
    # QC bits 0-1: 0 = good, 1 = other quality. "Other" lets cloud-contaminated
    # rainy-season pixels through (night values near 0 C seen in review), so
    # good quality only.
    qc_d = img.select("QC_Day").bitwiseAnd(3).eq(0)
    qc_n = img.select("QC_Night").bitwiseAnd(3).eq(0)
    day = img.select("LST_Day_1km").multiply(0.02).subtract(273.15).updateMask(qc_d)
    night = img.select("LST_Night_1km").multiply(0.02).subtract(273.15).updateMask(qc_n)
    return day.rename("lst_day_c").addBands(night.rename("lst_night_c"))


def _et(img):
    import ee

    d = img.date()
    year_end = ee.Date.fromYMD(d.get("year").add(1), 1, 1)
    ndays = ee.Number(8).min(year_end.difference(d, "day"))  # last composite is 5-6 days
    et = img.select("ET").updateMask(img.select("ET").lte(32700)).multiply(0.1).divide(ndays)
    pet = img.select("PET").updateMask(img.select("PET").lte(32700)).multiply(0.1).divide(ndays)
    ratio = et.divide(pet).rename("et_ratio")
    return et.rename("et_mm_day").addBands(pet.rename("pet_mm_day")).addBands(ratio)


def _viirs(img):
    # NASA/VIIRS/002/VNP13A1 NDVI in Earth Engine is already scaled to -1..1
    # (unlike MODIS's x0.0001 integers); scaling again gave values ~4e-5.
    ndvi = img.select("NDVI").toFloat()
    return ndvi.updateMask(ndvi.gte(-1).And(ndvi.lte(1))).rename("ndvi")


def _gldas(img):
    return (
        img.select("RootMoist_inst")
        .rename("rootzone_mm")  # 0-100 cm water
        .addBands(img.select("SoilMoi0_10cm_inst").divide(100).rename("sm_0_10cm"))  # m3/m3
        .addBands(img.select("Evap_tavg").multiply(86400).rename("evap_mm_day"))
    )


def _era5(img):
    u = img.select("u_component_of_wind_10m")
    v = img.select("v_component_of_wind_10m")
    wind2 = (
        u.pow(2).add(v.pow(2)).sqrt().multiply(4.87 / math.log(67.8 * 10 - 5.42))
    )  # FAO-56 eq. 47
    return (
        img.select("temperature_2m_max")
        .subtract(273.15)
        .rename("tmax_c")
        .addBands(img.select("temperature_2m_min").subtract(273.15).rename("tmin_c"))
        .addBands(img.select("dewpoint_temperature_2m").subtract(273.15).rename("tdew_c"))
        .addBands(img.select("total_precipitation_sum").multiply(1000).max(0).rename("precip_mm"))
        .addBands(
            img.select("surface_solar_radiation_downwards_sum").divide(1e6).rename("rs_mj_m2")
        )
        .addBands(wind2.rename("wind2m_ms"))
        .addBands(img.select("volumetric_soil_water_layer_1").rename("swvl1_0_7cm"))
        .addBands(img.select("volumetric_soil_water_layer_2").rename("swvl2_7_28cm"))
    )


SERIES = {
    "lst": Series(
        "lst",
        "MODIS/061/MOD11A2",
        dt.date(2000, 2, 18),
        ["lst_day_c", "lst_night_c"],
        _lst,
        chunk="year",
    ),
    "et": Series(
        "et",
        "MODIS/061/MOD16A2GF",
        dt.date(2000, 1, 1),
        ["et_mm_day", "pet_mm_day", "et_ratio"],
        _et,
        chunk="year",
        rate_bands={"et_mm_day": "mm", "pet_mm_day": "mm"},
        refresh_days=400,
        note="Gap-filled MOD16A2GF ends with the last fully processed year.",
    ),
    "viirs_ndvi": Series(
        "viirs_ndvi",
        "NASA/VIIRS/002/VNP13A1",
        dt.date(2012, 1, 17),
        ["ndvi"],
        _viirs,
        chunk="year",
        cropland=True,
        refresh_days=60,
    ),
    "gldas": Series(
        "gldas",
        "NASA/GLDAS/V021/NOAH/G025/T3H",
        dt.date(2000, 1, 1),
        ["rootzone_mm", "sm_0_10cm", "evap_mm_day"],
        _gldas,
        step="daily",
        chunk="month",
        rate_bands={"evap_mm_day": "mm"},
    ),
    "era5land": Series(
        "era5land",
        "ECMWF/ERA5_LAND/DAILY_AGGR",
        dt.date(1981, 1, 1),
        [
            "tmax_c",
            "tmin_c",
            "tdew_c",
            "precip_mm",
            "rs_mj_m2",
            "wind2m_ms",
            "swvl1_0_7cm",
            "swvl2_7_28cm",
        ],
        _era5,
        chunk="month",
        rate_bands={"precip_mm": "mm"},
    ),
}
STATIC = ("srtm", "hls", "openlandmap")


# --------------------------------------------------------------------------- #
# Series extraction
# --------------------------------------------------------------------------- #
def chunks(spec, start, end):
    if spec.chunk == "year":
        for y in range(start.year, end.year + 1):
            yield f"{y}", max(dt.date(y, 1, 1), start), min(dt.date(y, 12, 31), end)
    else:
        m = dt.date(start.year, start.month, 1)
        while m <= end:
            nxt = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            yield f"{m:%Y-%m}", max(m, start), min(nxt - dt.timedelta(days=1), end)
            m = nxt


def pull_chunk(spec, col, fc, proj, crop, first, last):
    import ee

    span = col.filterDate(first.isoformat(), (last + dt.timedelta(days=1)).isoformat())
    out_bands = list(spec.bands) + ([f"{b}_crop" for b in spec.bands] if spec.cropland else [])

    def with_crop(img):
        if not spec.cropland:
            return img
        return img.addBands(img.updateMask(crop).rename([f"{b}_crop" for b in spec.bands]))

    def prefixed(img, day):
        names = ee.List(out_bands).map(lambda b: ee.String("d").cat(day).cat("_").cat(b))
        return img.select(out_bands).rename(names)

    if spec.step == "native":

        def add(img, acc):
            img = ee.Image(img)
            day = img.date().format("YYYYMMdd")
            return ee.Image(acc).addBands(prefixed(with_crop(spec.transform(img)), day))

        stacked = ee.Image(span.iterate(add, ee.Image().select([])))
    else:
        days = (
            span.aggregate_array("system:time_start")
            .map(lambda t: ee.Date(t).format("YYYYMMdd"))
            .distinct()
        )

        def add_day(day, acc):
            d0 = ee.Date.parse("YYYYMMdd", day)
            imgs = span.filterDate(d0, d0.advance(1, "day")).map(spec.transform)
            return ee.Image(acc).addBands(prefixed(with_crop(imgs.mean()), day))

        stacked = ee.Image(days.iterate(add_day, ee.Image().select([])))

    reduced = stacked.reduceRegions(
        collection=fc,
        reducer=ee.Reducer.mean(),
        crs=proj["crs"],
        crsTransform=proj["transform"],
        tileScale=4,
    )
    return wide_to_long(computed(reduced), out_bands)


def run_series(spec, project, lgas, start, end, out, workers):
    import ee

    ee.Initialize(project=project)
    col = ee.ImageCollection(spec.collection)
    first_img = ee.Image(col.first())
    proj = spec.transform(first_img).select(spec.bands[0]).projection().getInfo()
    last = ee.Date(col.aggregate_max("system:time_start")).format("YYYY-MM-dd").getInfo()
    start = max(start, spec.start)
    end = min(end, dt.date.fromisoformat(last))
    log.info(f"  {spec.collection}: {start} to {end}")
    fc = to_ee_features(lgas)
    crop = crop_mask_for(proj) if spec.cropland else None

    cache = out / "gee_cache"
    cache.mkdir(parents=True, exist_ok=True)
    refresh_after = end - dt.timedelta(days=spec.refresh_days)
    jobs = [
        (k, a, b, cache / f"{k}.csv")
        for k, a, b in chunks(spec, start, end)
        if not ((cache / f"{k}.csv").exists() and b < refresh_after)
    ]
    log.info(f"  {len(jobs)} chunk(s) to compute")

    def run(job):
        key, a, b, path = job
        err = None
        for attempt in range(1, 6):
            try:
                df = pull_chunk(spec, col, fc, proj, crop, a, b)
                df.to_csv(path, index=False)
                return f"{key} ok ({len(df)} rows)"
            except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
                err = exc
                time.sleep(15 * attempt)
        return f"{key} FAILED after 5 tries: {err}; rerun later"

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(run, j) for j in jobs]
        for n, fut in enumerate(as_completed(futs), 1):
            log.info(f"  [{n}/{len(jobs)}] {fut.result()}  ({time.time() - t0:.0f}s)")
    return start, end


def build_series_tables(spec, out, start, end):
    files = sorted((out / "gee_cache").glob("*.csv"))
    if not files:
        sys.exit("Nothing cached yet.")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    d["date"] = pd.to_datetime(d["date"])
    d = d[(d["date"].dt.date >= start) & (d["date"].dt.date <= end)]
    vals = [c for c in d.columns if c not in ("lga", "date")]
    d = d.dropna(subset=vals, how="all").drop_duplicates(["lga", "date"], keep="last")
    d = d.sort_values(["lga", "date"]).reset_index(drop=True)
    d.to_csv(out / f"{spec.name}_lga.csv", index=False)

    d["year"], d["month"] = d["date"].dt.year, d["date"].dt.month
    m = d.groupby(["lga", "year", "month"], as_index=False).agg(
        **{c: (c, "mean") for c in vals}, steps=("date", "nunique")
    )
    dim = pd.to_datetime(dict(year=m["year"], month=m["month"], day=1)).dt.days_in_month
    for band in spec.rate_bands:
        for c in (band, f"{band}_crop"):
            if c in m:
                m[c.replace("_mm_day", "_mm_month").replace("precip_mm", "precip_mm_month")] = (
                    m[c] * dim
                )
    if spec.step == "daily" or spec.chunk == "month":
        m["complete_month"] = m["steps"] == dim
    m.to_csv(out / f"{spec.name}_lga_monthly.csv", index=False)
    log.info(
        f"  {d['date'].min():%Y-%m-%d} to {d['date'].max():%Y-%m-%d}, "
        f"{d['lga'].nunique()} LGAs, {len(d)} rows -> {out}/"
    )


# --------------------------------------------------------------------------- #
# SRTM terrain (static)
# --------------------------------------------------------------------------- #
def run_srtm(project, lgas, out, scale):
    import ee

    ee.Initialize(project=project)
    fc = to_ee_features(lgas)
    dem = ee.Image("USGS/SRTMGL1_003").select("elevation")
    slope_deg = ee.Terrain.slope(dem)
    slope_pct = slope_deg.multiply(math.pi / 180).tan().multiply(100)
    crop = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS)
    img = (
        dem.rename("elev_m")
        .addBands(slope_pct.rename("slope_pct"))
        .addBands(slope_pct.updateMask(crop).rename("slope_pct_crop"))
        .addBands(slope_pct.gt(2).updateMask(crop).rename("crop_share_slope_gt2"))
        .addBands(slope_pct.gt(5).updateMask(crop).rename("crop_share_slope_gt5"))
        .addBands(slope_pct.gt(8).updateMask(crop).rename("crop_share_slope_gt8"))
    )
    reducer = ee.Reducer.mean().combine(ee.Reducer.percentile([90]), sharedInputs=True)
    for s in (scale, 90):
        try:
            log.info(f"  reducing SRTM at {s} m")
            df = computed(img.reduceRegions(collection=fc, reducer=reducer, scale=s, tileScale=16))
            break
        except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
            log.info(f"  {s} m failed ({exc}); retrying coarser")
    else:
        sys.exit("SRTM reduction failed at every scale")
    keep = ["lga"] + sorted(c for c in df.columns if c.endswith("_mean") or c.endswith("_p90"))
    df = df[keep]
    df.columns = [c.replace("_mean", "") for c in df.columns]
    df.to_csv(out / "srtm_lga_terrain.csv", index=False)
    log.info(df.round(2).to_string(index=False))
    log.info(f"  wrote {out / 'srtm_lga_terrain.csv'}")


# --------------------------------------------------------------------------- #
# OpenLandMap soil water content (reference for A8)
# --------------------------------------------------------------------------- #
OPENLANDMAP = {
    "fc_33kpa": "OpenLandMap/SOL/SOL_WATERCONTENT-33KPA_USDA-4B1C_M/v01",
}
OPENLANDMAP_DEPTHS_CM = (0, 10, 30, 60, 100, 200)


def run_openlandmap(project, lgas, out, scale):
    """Cropland mean per LGA of volumetric water content (%) at each depth and suction."""
    import ee

    ee.Initialize(project=project)
    fc = to_ee_features(lgas)
    crop = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS)
    bands = [f"b{d}" for d in OPENLANDMAP_DEPTHS_CM]
    img = None
    for name, asset in OPENLANDMAP.items():
        renamed = [f"{name}_{d}cm_pct" for d in OPENLANDMAP_DEPTHS_CM]
        layer = ee.Image(asset).select(bands, renamed).updateMask(crop)
        img = layer if img is None else img.addBands(layer)
    log.info(f"  reducing OpenLandMap water content at {scale} m")
    reduced = img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale, tileScale=16)
    df = computed(reduced)
    keep = ["lga"] + sorted(c for c in df.columns if c.startswith("fc_33kpa"))
    df = df[keep]
    df.to_csv(out / "openlandmap_lga_water.csv", index=False)
    log.info(df.round(1).to_string(index=False))
    log.info(f"  wrote {out / 'openlandmap_lga_water.csv'}")


# --------------------------------------------------------------------------- #
# HLS seasonal NDVI (Landsat 8/9 + Sentinel-2, 30 m)
# --------------------------------------------------------------------------- #
HLS_SEASONS = {"dry": (1, 3), "peak": (8, 9)}


def run_hls(project, lgas, start, end, out, scale, workers):
    import ee

    ee.Initialize(project=project)
    fc = to_ee_features(lgas)
    crop = ee.ImageCollection(WORLDCOVER).first().select("Map").eq(CROPLAND_CLASS)
    bad_bits = (
        (1 << 1) | (1 << 2) | (1 << 3) | (1 << 4) | (1 << 5)
    )  # cloud, adjacent, shadow, snow, water

    def ndvi(nir, red):
        def f(img):
            clear = img.select("Fmask").toInt().bitwiseAnd(bad_bits).eq(0)
            return img.normalizedDifference([nir, red]).rename("ndvi").updateMask(clear)

        return f

    cache = out / "gee_cache"
    cache.mkdir(parents=True, exist_ok=True)
    years = [y for y in range(max(start.year, 2013), end.year + 1)]
    jobs = [y for y in years if not (cache / f"{y}.csv").exists() or y >= end.year - 1]
    log.info(f"  HLS seasonal NDVI at {scale} m, {len(jobs)} year(s) to compute")

    def one_year(y, scale):
        bands = []
        for season, (m0, m1) in HLS_SEASONS.items():
            a = ee.Date.fromYMD(y, m0, 1)
            b = ee.Date.fromYMD(y + (m1 == 12), m1 % 12 + 1, 1)
            l30 = ee.ImageCollection("NASA/HLS/HLSL30/v002").filterDate(a, b).map(ndvi("B5", "B4"))
            s30 = ee.ImageCollection("NASA/HLS/HLSS30/v002").filterDate(a, b).map(ndvi("B8A", "B4"))
            both = l30.merge(s30).filterBounds(fc.geometry().bounds())
            # A season with no scenes (e.g. Jan-Mar 2013, before Landsat 8 began
            # operations) gives a band-less median; substitute a fully masked band.
            empty = ee.Image.constant(0).rename("ndvi").updateMask(ee.Image.constant(0))
            med = ee.Image(ee.Algorithms.If(both.size().gt(0), both.median(), empty))
            cnt = ee.Image(
                ee.Algorithms.If(
                    both.size().gt(0), both.count(), ee.Image.constant(0).rename("ndvi")
                )
            ).rename("clear_obs")
            bands += [
                med.rename(f"{season}_ndvi"),
                med.updateMask(crop).rename(f"{season}_ndvi_crop"),
                cnt.updateMask(crop).rename(f"{season}_clear_obs_crop"),
            ]
        img = ee.Image.cat(bands)
        df = computed(
            img.reduceRegions(collection=fc, reducer=ee.Reducer.mean(), scale=scale, tileScale=16)
        )
        df["year"], df["scale_m"] = y, scale
        return df

    def run(y):
        err = None
        for attempt in range(1, 5):
            s_m = scale if attempt <= 2 else scale * 2  # big medians can time out: go coarser
            try:
                one_year(y, s_m).to_csv(cache / f"{y}.csv", index=False)
                return f"{y} ok at {s_m} m"
            except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
                err = exc
                time.sleep(20 * attempt)
        return f"{y} FAILED: {err}; rerun later"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, fut in enumerate(as_completed([pool.submit(run, y) for y in jobs]), 1):
            log.info(f"  [{n}/{len(jobs)}] {fut.result()}")

    files = sorted(cache.glob("*.csv"))
    if not files:
        sys.exit("Nothing cached yet.")
    d = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    cols = ["lga", "year", "scale_m"] + [c for c in d.columns if c.startswith(("dry_", "peak_"))]
    d = d[cols].sort_values(["lga", "year"])
    d["peak_minus_dry_crop"] = d["peak_ndvi_crop"] - d["dry_ndvi_crop"]
    d.to_csv(out / "hls_lga_seasonal.csv", index=False)
    log.info(
        f"  {d['year'].min()} to {d['year'].max()}, {d['lga'].nunique()} LGAs -> "
        f"{out / 'hls_lga_seasonal.csv'}"
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def describe():
    log.info("srtm        static   NASA SRTM 30 m elevation and slope, cropland slope shares")
    log.info("openlandmap static   OpenLandMap 250 m water content at 33 kPa, 6 depths")
    log.info(
        "hls         2013-    HLS v2 30 m NDVI, dry (Jan-Mar) and peak (Aug-Sep) medians per year"
    )
    for s in SERIES.values():
        log.info(
            f"{s.name:11} {s.start.year}-    {s.collection}  [{', '.join(s.bands)}]"
            f"{'  + cropland' if s.cropland else ''}"
        )


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "dataset", nargs="?", help=f"one of {', '.join(list(STATIC) + list(SERIES))}, or all"
    )
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--project")
    ap.add_argument("--start", default="1981-01-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument(
        "--scale",
        type=int,
        default=None,
        help="reduction scale in m for srtm (default 30) and hls (default 60)",
    )
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/extras"))
    ap.add_argument("--skip-pull", action="store_true", help="rebuild tables from the cache only")
    args = ap.parse_args()

    if args.list or not args.dataset:
        describe()
        return
    names = list(STATIC) + list(SERIES) if args.dataset == "all" else [args.dataset]
    for n in names:
        if n not in SERIES and n not in STATIC:
            sys.exit(f"Unknown dataset {n}. Use --list.")

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    lgas = load_lgas(args.lgas)
    project = None if args.skip_pull else ee_project(args.project)

    for name in names:
        out = Path(args.out) / name
        out.mkdir(parents=True, exist_ok=True)
        log.info(f"== {name}")
        if name == "srtm":
            run_srtm(project, lgas, out, args.scale or 30)
        elif name == "openlandmap":
            run_openlandmap(project, lgas, out, args.scale or 250)
        elif name == "hls":
            run_hls(project, lgas, start, end, out, args.scale or 60, args.workers)
        else:
            spec = SERIES[name]
            s, e = (max(start, spec.start), end)
            if not args.skip_pull:
                s, e = run_series(spec, project, lgas, start, end, out, args.workers)
            build_series_tables(spec, out, s, e)
            if spec.note:
                log.info(f"  note: {spec.note}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
