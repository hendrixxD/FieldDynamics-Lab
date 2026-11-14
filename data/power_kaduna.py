#!/usr/bin/env python3
"""
NASA POWER daily agroclimatology for Kaduna State, summarised per LGA.

Source  : NASA POWER Daily API, community=AG (power.larc.nasa.gov).
Boundary: OCHA COD-AB Nigeria admin level 2 (LGA) shapefile from HDX.

Why grid cells and not LGA centroids
  POWER meteorology sits on the MERRA-2 grid (0.5 deg lat x 0.625 deg lon,
  about 55 x 68 km). Most Kaduna LGAs are smaller than one cell, so asking for
  each LGA centroid would hand several LGAs the same cell, and a large LGA
  would be represented by whichever single cell its centroid hits. Instead the
  script pulls every cell that touches the state once, then gives each LGA an
  area-weighted mean of the cells it overlaps. Solar radiation is on a coarser
  1 deg grid inside POWER; the weights use the meteorology grid for all
  variables.

What it does
  1. Builds the MERRA-2 cells covering Kaduna and the overlap weight of every
     cell in every LGA (cell_lga_weights.csv).
  2. Pulls the full daily record, 1 January 1981 to today, for each cell in
     10-year chunks, with retries. Days POWER has not published yet (it runs
     a few days behind) are trimmed, so the output ends on the latest real
     day. Every chunk is cached, so a dropped connection loses nothing: rerun
     and it continues. Rerunning on a later date pulls only the newest chunk
     again, which picks up the days added since.
  3. Computes daily reference evapotranspiration per cell: FAO-56
     Penman-Monteith (main) and Hargreaves (check), as the PRD's A9 asks.
  4. Writes:
       power_cell_daily.parquet  one row per cell per day (raw + ET0)
       power_lga_daily.parquet   one row per LGA per day (area-weighted)
       power_lga_monthly.csv     one row per LGA per month: rain total,
                                 rain days, heat days, ET0 total, etc.

Variables pulled
  PRECTOTCORR  rainfall, mm/day (bias-corrected)
  T2M, T2M_MAX, T2M_MIN, T2MDEW   temperature and dew point, C
  RH2M         relative humidity, %
  ALLSKY_SFC_SW_DWN  solar radiation, MJ/m2/day
  WS2M         wind speed at 2 m, m/s
  PS           surface pressure, kPa
  GWETTOP, GWETROOT  surface and root-zone soil wetness, 0 to 1

Setup
  pip install requests geopandas pandas numpy shapely pyarrow
  No login needed for POWER.

Examples
  # per LGA, download extent from the AOI (recommended)
  python power_kaduna.py --shapefile data/nga_admbnda_adm2_osgof_20190417.shp \
      --aoi data/kaduna_aoi.geojson --out out_power/
  # whole state only, no LGA file needed
  python power_kaduna.py --aoi data/kaduna_aoi.geojson --out out_power_state/
"""

import argparse
import datetime as dt
import gzip
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("power_kaduna")

API = "https://power.larc.nasa.gov/api/temporal/daily/point"
PARAMETERS = [
    "PRECTOTCORR",
    "T2M",
    "T2M_MAX",
    "T2M_MIN",
    "T2MDEW",
    "RH2M",
    "ALLSKY_SFC_SW_DWN",
    "WS2M",
    "PS",
    "GWETTOP",
    "GWETROOT",
]
LAT_STEP, LON_STEP = 0.5, 0.625  # MERRA-2 grid
POWER_START = dt.date(1981, 1, 1)
HEAT_C, COLD_C, RAIN_DAY_MM = 35.0, 2.0, 1.0
EQUAL_AREA = 6933  # EPSG:6933, global equal-area


# --------------------------------------------------------------------------- #
# Boundaries and grid
# --------------------------------------------------------------------------- #
def load_lgas(shapefile, state, adm1_field, adm2_field):
    import geopandas as gpd

    gdf = gpd.read_file(shapefile)
    if adm2_field not in gdf.columns and "lga" in gdf.columns:
        adm2_field = "lga"
    if adm2_field not in gdf.columns:
        sys.exit(f"Field '{adm2_field}' not in boundary file. Columns are: {list(gdf.columns)}")
    if adm1_field not in gdf.columns:
        # e.g. processed/kaduna_lgas.geojson: already one state's LGAs, no state column
        log.info(f"  no '{adm1_field}' column, so using every row as an LGA of {state}")
        lgas = gdf.copy()
    else:
        lgas = gdf[gdf[adm1_field].str.strip().str.lower() == state.lower()].copy()
    if lgas.empty:
        sys.exit(
            f"No rows with {adm1_field} == '{state}'. "
            f"Values present: {sorted(gdf[adm1_field].unique())}"
        )
    lgas = lgas.to_crs(4326)[[adm2_field, "geometry"]].rename(columns={adm2_field: "lga"})
    return lgas.reset_index(drop=True)


def load_aoi(path, label="Kaduna State"):
    """State outline (e.g. kaduna_aoi.geojson) as a one-row 'lga' frame."""
    import geopandas as gpd

    aoi = gpd.read_file(path).to_crs(4326)
    geom = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    return gpd.GeoDataFrame({"lga": [label]}, geometry=[geom], crs=4326)


def merra_cells(lgas):
    """Every MERRA-2 cell whose box touches the state, as a GeoDataFrame."""
    import geopandas as gpd
    from shapely.geometry import box

    w, s, e, n = lgas.total_bounds
    lat0 = np.floor((s - LAT_STEP / 2) / LAT_STEP) * LAT_STEP
    lat1 = np.ceil((n + LAT_STEP / 2) / LAT_STEP) * LAT_STEP
    lon0 = np.floor((w + 180 - LON_STEP / 2) / LON_STEP) * LON_STEP - 180
    lon1 = np.ceil((e + 180 + LON_STEP / 2) / LON_STEP) * LON_STEP - 180
    lats = np.round(np.arange(lat0, lat1 + 1e-9, LAT_STEP), 4)
    lons = np.round(np.arange(lon0, lon1 + 1e-9, LON_STEP), 4)

    rows = []
    for la in lats:
        for lo in lons:
            rows.append(
                {
                    "cell_id": f"{la:+.3f}_{lo:+.3f}",
                    "lat": la,
                    "lon": lo,
                    "geometry": box(
                        lo - LON_STEP / 2, la - LAT_STEP / 2, lo + LON_STEP / 2, la + LAT_STEP / 2
                    ),
                }
            )
    cells = gpd.GeoDataFrame(rows, crs=4326)
    state = lgas.union_all() if hasattr(lgas, "union_all") else lgas.unary_union
    return cells[cells.intersects(state)].reset_index(drop=True)


def cell_lga_weights(cells, lgas):
    """Share of each LGA's area that falls in each cell (rows sum to 1 per LGA)."""
    import geopandas as gpd

    inter = gpd.overlay(
        lgas.to_crs(EQUAL_AREA),
        cells.to_crs(EQUAL_AREA)[["cell_id", "geometry"]],
        how="intersection",
        keep_geom_type=True,
    )
    inter["area_km2"] = inter.geometry.area / 1e6
    inter["weight"] = inter["area_km2"] / inter.groupby("lga")["area_km2"].transform("sum")
    out = inter[["lga", "cell_id", "area_km2", "weight"]].sort_values(
        ["lga", "weight"], ascending=[True, False]
    )
    missing = sorted(set(lgas["lga"]) - set(out["lga"]))
    if missing:
        sys.exit(f"LGAs with no overlapping cell (check geometry): {missing}")
    return out.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def chunks(start, end, years=10):
    a = start
    while a <= end:
        b = min(dt.date(a.year + years - 1, 12, 31), end)
        yield a, b
        a = b + dt.timedelta(days=1)


def fetch_chunk(cell, start, end, cache_dir, retries=5):
    path = cache_dir / f"{cell.cell_id}_{start:%Y%m%d}_{end:%Y%m%d}.json.gz"
    if path.exists():
        return path
    import requests

    params = {
        "parameters": ",".join(PARAMETERS),
        "community": "AG",
        "latitude": cell.lat,
        "longitude": cell.lon,
        "start": f"{start:%Y%m%d}",
        "end": f"{end:%Y%m%d}",
        "format": "JSON",
        "time-standard": "LST",
    }
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            r = requests.get(API, params=params, timeout=180)
            # POWER may refuse an end date past its latest day. For the most
            # recent chunk, step the end back a few days and try again.
            if (
                r.status_code in (400, 422)
                and (dt.date.today() - end).days < 30
                and end - dt.timedelta(days=3) >= start
            ):
                end -= dt.timedelta(days=3)
                params["end"] = f"{end:%Y%m%d}"
                path = cache_dir / f"{cell.cell_id}_{start:%Y%m%d}_{end:%Y%m%d}.json.gz"
                attempt -= 1
                continue
            if r.status_code == 429 or r.status_code >= 500:
                raise RuntimeError(f"HTTP {r.status_code}")
            r.raise_for_status()
            payload = r.json()
            if "properties" not in payload:
                raise RuntimeError(f"unexpected response: {str(payload)[:300]}")
            tmp = path.with_suffix(".tmp")
            with gzip.open(tmp, "wt") as f:
                json.dump(payload, f)
            tmp.rename(path)
            return path
        except Exception as exc:  # noqa: BLE001 - EE/network errors vary; retried
            wait = 10 * attempt
            log.info(
                f"  {cell.cell_id} {start.year}-{end.year} attempt {attempt} failed: {exc}; "
                f"retrying in {wait}s"
            )
            time.sleep(wait)
    log.info(f"  giving up on {cell.cell_id} {start.year}-{end.year}; rerun later to retry")
    return None


def download(cells, start, end, cache_dir, workers):
    cache_dir.mkdir(parents=True, exist_ok=True)
    jobs = [(c, a, b) for c in cells.itertuples() for a, b in chunks(start, end)]
    done = sum(
        (cache_dir / f"{c.cell_id}_{a:%Y%m%d}_{b:%Y%m%d}.json.gz").exists() for c, a, b in jobs
    )
    log.info(
        f"  {len(cells)} cells x {len(jobs) // max(len(cells), 1)} chunks = {len(jobs)} requests, "
        f"{done} cached"
    )
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(fetch_chunk, c, a, b, cache_dir) for c, a, b in jobs]
        for n, f in enumerate(as_completed(futs), 1):
            f.result()
            if n % 10 == 0 or n == len(futs):
                log.info(f"  {n}/{len(futs)} requests finished")


# --------------------------------------------------------------------------- #
# Parse
# --------------------------------------------------------------------------- #
UNIT_FIX = {  # convert to the units the ET0 maths expects, if POWER sends others
    "ALLSKY_SFC_SW_DWN": {"kW-hr/m^2/day": 3.6, "MJ/m^2/day": 1.0},
    "PS": {"kPa": 1.0, "hPa": 0.1, "Pa": 0.001},
}


def parse_cache(cells, cache_dir):
    frames = []
    for cell in cells.itertuples():
        parts = []
        for p in sorted(cache_dir.glob(f"{cell.cell_id}_*.json.gz")):
            with gzip.open(p, "rt") as f:
                payload = json.load(f)
            fill = payload.get("header", {}).get("fill_value", -999)
            units = {k: v.get("units") for k, v in payload.get("parameters", {}).items()}
            data = payload["properties"]["parameter"]
            df = pd.DataFrame(data)
            df.index = pd.to_datetime(df.index, format="%Y%m%d")
            df = df.replace(fill, np.nan).astype("float64")
            for var, table in UNIT_FIX.items():
                u = units.get(var)
                if var in df and u is not None:
                    if u not in table:
                        sys.exit(f"Unexpected unit for {var}: {u}")
                    df[var] *= table[u]
            parts.append(df)
        if not parts:
            continue
        # Files are read oldest-name first; where two cached chunks overlap
        # (the recent chunk is re-pulled as new days arrive), keep the latest
        # non-missing value for each day.
        df = pd.concat(parts).groupby(level=0).last().sort_index()
        df.insert(0, "cell_id", cell.cell_id)
        df.insert(1, "lat", cell.lat)
        df.insert(2, "lon", cell.lon)
        frames.append(df.rename_axis("date").reset_index())
    if not frames:
        sys.exit("Nothing cached yet.")
    out = pd.concat(frames, ignore_index=True)

    # Trim trailing days POWER has not filled yet (all key variables missing in
    # every cell), so "to date" means "to the latest day with real data".
    key = ["PRECTOTCORR", "T2M_MAX", "T2M_MIN"]
    has_data = out.groupby("date")[key].apply(lambda g: g.notna().any().all())
    last_good = has_data[has_data].index.max()
    return out[out["date"] <= last_good].reset_index(drop=True)


# --------------------------------------------------------------------------- #
# FAO-56 reference evapotranspiration
# --------------------------------------------------------------------------- #
def _sat_vp(t):
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def extraterrestrial_radiation(lat_deg, doy):
    phi = np.radians(lat_deg)
    dr = 1 + 0.033 * np.cos(2 * np.pi * doy / 365)
    decl = 0.409 * np.sin(2 * np.pi * doy / 365 - 1.39)
    ws = np.arccos(np.clip(-np.tan(phi) * np.tan(decl), -1, 1))
    return (
        (24 * 60 / np.pi)
        * 0.0820
        * dr
        * (ws * np.sin(phi) * np.sin(decl) + np.cos(phi) * np.cos(decl) * np.sin(ws))
    )


def add_et0(df):
    tmax, tmin, tmean = df["T2M_MAX"], df["T2M_MIN"], (df["T2M_MAX"] + df["T2M_MIN"]) / 2
    pres = df["PS"]  # kPa
    z = (293 / 0.0065) * (1 - (pres / 101.3) ** (1 / 5.26))  # elevation from pressure, m
    ra = extraterrestrial_radiation(df["lat"], df["date"].dt.dayofyear)
    rs = df["ALLSKY_SFC_SW_DWN"]
    rso = (0.75 + 2e-5 * z) * ra
    ea = _sat_vp(df["T2MDEW"])
    es = (_sat_vp(tmax) + _sat_vp(tmin)) / 2
    delta = 4098 * _sat_vp(tmean) / (tmean + 237.3) ** 2
    gamma = 0.000665 * pres
    sigma = 4.903e-9
    rnl = (
        sigma
        * ((tmax + 273.16) ** 4 + (tmin + 273.16) ** 4)
        / 2
        * (0.34 - 0.14 * np.sqrt(ea.clip(lower=0)))
        * (1.35 * (rs / rso).clip(upper=1.0) - 0.35)
    )
    rn = 0.77 * rs - rnl
    u2 = df["WS2M"]
    df["ET0_PM"] = (
        (0.408 * delta * rn + gamma * (900 / (tmean + 273)) * u2 * (es - ea))
        / (delta + gamma * (1 + 0.34 * u2))
    ).clip(lower=0)
    df["ET0_HG"] = 0.0023 * (tmean + 17.8) * np.sqrt((tmax - tmin).clip(lower=0)) * ra * 0.408
    df["elev_m"] = z
    return df


# --------------------------------------------------------------------------- #
# Aggregate
# --------------------------------------------------------------------------- #
VALUE_COLS = PARAMETERS + ["ET0_PM", "ET0_HG"]


def to_lga_daily(cell_daily, weights):
    m = weights[["lga", "cell_id", "weight"]].merge(cell_daily, on="cell_id")
    cols = [c for c in VALUE_COLS if c in m]
    for c in cols:
        ok = m[c].notna()
        m[f"_w_{c}"] = m["weight"].where(ok, 0)
        m[f"_x_{c}"] = (m[c] * m["weight"]).where(ok, 0)
    g = m.groupby(["lga", "date"])
    out = pd.DataFrame(index=g.size().index)
    for c in cols:
        w = g[f"_w_{c}"].sum()
        out[c] = g[f"_x_{c}"].sum() / w.where(w > 0)
    return out.reset_index()


def to_lga_monthly(lga_daily):
    d = lga_daily.copy()
    d["year"], d["month"] = d["date"].dt.year, d["date"].dt.month
    d["rain_day"] = d["PRECTOTCORR"] >= RAIN_DAY_MM
    d["heat_day"] = d["T2M_MAX"] >= HEAT_C
    d["cold_day"] = d["T2M_MIN"] <= COLD_C
    m = d.groupby(["lga", "year", "month"], as_index=False).agg(
        days=("date", "nunique"),
        rain_mm=("PRECTOTCORR", "sum"),
        rain_days=("rain_day", "sum"),
        max_daily_rain_mm=("PRECTOTCORR", "max"),
        tmax_mean_c=("T2M_MAX", "mean"),
        tmin_mean_c=("T2M_MIN", "mean"),
        heat_days=("heat_day", "sum"),
        cold_days=("cold_day", "sum"),
        rh_mean_pct=("RH2M", "mean"),
        et0_pm_mm=("ET0_PM", "sum"),
        et0_hg_mm=("ET0_HG", "sum"),
        gwettop_mean=("GWETTOP", "mean"),
        gwetroot_mean=("GWETROOT", "mean"),
    )
    m["water_balance_mm"] = m["rain_mm"] - m["et0_pm_mm"]
    full = (
        m["days"] == pd.to_datetime(dict(year=m["year"], month=m["month"], day=1)).dt.days_in_month
    )
    m["complete_month"] = full
    return m


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--shapefile", help="COD-AB admin level 2 (LGA) shapefile")
    ap.add_argument(
        "--aoi",
        help="state outline, e.g. kaduna_aoi.geojson. Sets which cells "
        "are downloaded. Without --shapefile, results are one "
        "whole-state row instead of per LGA",
    )
    ap.add_argument("--state", default="Kaduna")
    ap.add_argument("--adm1-field", default="ADM1_EN")
    ap.add_argument("--adm2-field", default="ADM2_EN")
    ap.add_argument("--start", default=POWER_START.isoformat(), help="YYYY-MM-DD")
    ap.add_argument(
        "--end",
        default=None,
        help="YYYY-MM-DD; default is today. Days POWER has not published "
        "yet are trimmed, so the output ends at the latest real day",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=4,
        help="parallel requests; keep it at 5 or below to be polite to POWER",
    )
    ap.add_argument("--out", default="out_power")
    ap.add_argument("--skip-download", action="store_true")
    args = ap.parse_args()

    start = dt.date.fromisoformat(args.start)
    end = dt.date.fromisoformat(args.end) if args.end else dt.date.today()
    out, cache = Path(args.out), Path(args.out) / "cache"
    out.mkdir(parents=True, exist_ok=True)

    if not (args.shapefile or args.aoi):
        sys.exit("Give --shapefile (per-LGA results), --aoi (whole-state results), or both.")
    log.info("Loading boundaries")
    aoi = load_aoi(args.aoi) if args.aoi else None
    lgas = (
        load_lgas(args.shapefile, args.state, args.adm1_field, args.adm2_field)
        if args.shapefile
        else aoi
    )
    cells = merra_cells(aoi if aoi is not None else lgas)
    weights = cell_lga_weights(cells, lgas)
    weights.to_csv(out / "cell_lga_weights.csv", index=False)
    log.info(
        f"  {len(lgas)} LGAs over {len(cells)} POWER cells "
        f"({weights.groupby('lga').size().max()} cells at most per LGA)"
    )

    if not args.skip_download:
        log.info(f"Downloading {start} to {end}")
        download(cells, start, end, cache, args.workers)

    log.info("Parsing")
    cell_daily = add_et0(parse_cache(cells, cache))
    cell_daily = cell_daily[
        (cell_daily["date"].dt.date >= start) & (cell_daily["date"].dt.date <= end)
    ]
    cell_daily.to_parquet(out / "power_cell_daily.parquet", index=False)

    log.info("Aggregating to LGAs")
    lga_daily = to_lga_daily(cell_daily, weights)
    lga_daily.to_parquet(out / "power_lga_daily.parquet", index=False)
    to_lga_monthly(lga_daily).to_csv(out / "power_lga_monthly.csv", index=False)

    gaps = cell_daily[PARAMETERS].isna().mean().loc[lambda s: s > 0]
    if not gaps.empty:
        log.info("  missing-value share per variable (fill values from POWER):")
        for k, v in gaps.items():
            log.info(f"    {k}: {v:.2%}")
    log.info(
        f"Done. {lga_daily['date'].min():%Y-%m-%d} to {lga_daily['date'].max():%Y-%m-%d}, "
        f"files in {out}/"
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
