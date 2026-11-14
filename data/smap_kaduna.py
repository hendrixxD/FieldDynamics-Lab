#!/usr/bin/env python3
"""
SMAP L4 root zone soil moisture for Kaduna State, summarised per LGA.

Product : SPL4SMGP V008 (SMAP L4 Global 3-hourly 9 km Surface and Root Zone Soil
          Moisture Geophysical Data), NSIDC DAAC.
Source  : Earth Engine NASA/SMAP/SPL4SMGP/008 by default. Earth Engine sends back only
          the Kaduna window, so 2015 to date takes minutes. --source earthdata reads the
          native 9 km EASE-Grid files instead, but each day costs ~13 MB of download
          because every variable is stored as one global chunk.
Boundary: data/processed/kaduna_lgas.geojson (geoBoundaries, built by data.boundaries) by
          default. Any admin level 2 file works, e.g. OCHA COD-AB with --adm1-field ADM1_EN.

What it does
  1. Reads the Kaduna LGAs (or a state outline with --aoi) and builds a bounding box.
  2. Picks ONE time step per day (default 13:30 UTC) for every month in --months.
  3. Saves each day's Kaduna window of three moisture variables as a tiny .npz, so the
     run can be stopped and resumed at any time.
  4. Maps every cell to an LGA and writes three CSVs:
        lga_daily.csv    one row per LGA per day
        lga_monthly.csv  monthly means + anomaly vs that LGA's own climatology
        lga_season.csv   one row per LGA per year over --season-months: season mean,
                         anomaly, share of dry days, and rank of the year

Earth Engine holds this product on a ~0.095 degree lat/lon grid rather than the native
equal-area EASE grid. Across Kaduna (9-12 N) cell areas differ by under 2%, so a plain
mean over cells is still close to an area-weighted mean.

Setup
  Everything is in requirements.txt. Earth Engine needs EE_PROJECT in .env. Earthdata
  needs a free Earthdata Login: EARTHDATA_USERNAME and EARTHDATA_PASSWORD in .env, or
  a ~/.netrc entry.

Examples
  python -m data.smap_kaduna                    # 2015-2026, all months, per LGA
  python -m data.smap_kaduna --aoi data/kaduna_aoi.geojson  # one whole-state row
  python -m data.smap_kaduna --source earthdata --start-year 2024 --end-year 2024 \\
      --months 4-10 --every-n-days 5            # native cross-check, slow

  Note: the read window is fixed by the first run into a --cache folder (saved in
  grid.npz) so cached days always line up. Use a new --cache folder for a new window.
"""

import argparse
import calendar
import datetime
import logging
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("smap_kaduna")

SHORT_NAME = "SPL4SMGP"
VERSION = "008"
EE_COLLECTION = "NASA/SMAP/SPL4SMGP/008"
FIRST_DAY = datetime.date(2015, 3, 31)  # first SPL4SMGP granule

# Variable name in output -> path inside the SPL4SMGP HDF5 file. Earth Engine band
# names equal the output names.
VARIABLES = {
    "sm_rootzone": "Geophysical_Data/sm_rootzone",  # m3/m3, 0-100 cm
    "sm_surface": "Geophysical_Data/sm_surface",  # m3/m3, 0-5 cm
    # 0-1, wilting point to saturation
    "sm_rootzone_wetness": "Geophysical_Data/sm_rootzone_wetness",
}
FILL_VALUE = -9999.0  # SMAP fill value; Earth Engine masked pixels are given it too
FILL_THRESHOLD = -9000.0
GRANULE_TIME_RE = re.compile(r"_(\d{8})T(\d{6})_")
EE_BAND_RE = re.compile(r"^(\d{8})_\d{4}_(.+)$")  # e.g. 20240401_1330_sm_rootzone


# --------------------------------------------------------------------------- #
# Boundaries
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
        states = sorted(gdf[adm1_field].unique())
        sys.exit(f"No rows with {adm1_field} == '{state}'. Values present: {states}")
    lgas = lgas.to_crs(4326)
    lgas = lgas[[adm2_field, "geometry"]].rename(columns={adm2_field: "lga"})
    return lgas.reset_index(drop=True)


def load_aoi(path, label="Kaduna State"):
    """State outline (e.g. kaduna_aoi.geojson) as a one-row 'lga' frame."""
    import geopandas as gpd

    aoi = gpd.read_file(path).to_crs(4326)
    geom = aoi.union_all() if hasattr(aoi, "union_all") else aoi.unary_union
    return gpd.GeoDataFrame({"lga": [label]}, geometry=[geom], crs=4326)


def bbox_of(lgas, buffer_deg=0.1):
    w, s, e, n = lgas.total_bounds
    return (w - buffer_deg, s - buffer_deg, e + buffer_deg, n + buffer_deg)


# --------------------------------------------------------------------------- #
# Grid window and cell -> LGA map
# --------------------------------------------------------------------------- #
def grid_window(h5, bbox):
    """Row/col slice of the global 9 km EASE grid that covers bbox.

    On EASE-Grid 2.0 global, latitude is constant along a row and longitude
    constant along a column, so one column of cell_lat and one row of
    cell_lon are enough.
    """
    w, s, e, n = bbox
    lat = h5["cell_lat"][:, 0]
    lon = h5["cell_lon"][0, :]
    rows = np.where((lat >= s) & (lat <= n))[0]
    cols = np.where((lon >= w) & (lon <= e))[0]
    if rows.size == 0 or cols.size == 0:
        raise RuntimeError("Bounding box does not intersect the SMAP grid")
    r0, r1, c0, c1 = rows.min(), rows.max() + 1, cols.min(), cols.max() + 1
    return (int(r0), int(r1), int(c0), int(c1)), lat[r0:r1], lon[c0:c1]


def cell_lga_map(lat_sub, lon_sub, lgas):
    """Assign each window cell to the LGA its centre falls in.

    Small urban LGAs (e.g. Kaduna North/South) can be smaller than one 9 km
    cell and catch no centre at all. Those get the single nearest cell and are
    flagged, so the app can say the value is coarse for that LGA.
    """
    import geopandas as gpd

    jj, ii = np.meshgrid(np.arange(lon_sub.size), np.arange(lat_sub.size))
    pts = gpd.GeoDataFrame(
        {"i": ii.ravel(), "j": jj.ravel()},
        geometry=gpd.points_from_xy(
            np.tile(lon_sub, lat_sub.size), np.repeat(lat_sub, lon_sub.size)
        ),
        crs=4326,
    )
    joined = gpd.sjoin(pts, lgas, predicate="within", how="inner")
    mapping = joined[["i", "j", "lga"]].assign(nearest_fallback=False)

    missing = sorted(set(lgas["lga"]) - set(mapping["lga"]))
    extra = []
    for name in missing:
        centre = lgas.loc[lgas["lga"] == name, "geometry"].iloc[0].representative_point()
        d2 = (pts.geometry.x - centre.x) ** 2 + (pts.geometry.y - centre.y) ** 2
        k = int(np.argmin(d2.values))
        extra.append(
            {
                "i": int(pts.i.iloc[k]),
                "j": int(pts.j.iloc[k]),
                "lga": name,
                "nearest_fallback": True,
            }
        )
    if extra:
        mapping = pd.concat([mapping, pd.DataFrame(extra)], ignore_index=True)
        log.info(
            f"  {len(missing)} LGA(s) smaller than one cell, using nearest cell: "
            f"{', '.join(missing)}"
        )
    return mapping.reset_index(drop=True)


def with_retries(fn, label, retries=3):
    """Run fn, retrying with a growing pause. Returns None once every attempt has failed."""
    for attempt in range(1, retries + 1):
        try:
            return fn()
        # Remote reads fail in many library-specific ways; log it and retry.
        except Exception as exc:  # noqa: BLE001
            log.info(f"  {label} attempt {attempt} failed: {exc}")
            time.sleep(5 * attempt)
    log.info(f"  giving up on {label}; rerun the script later to retry it")
    return None


# --------------------------------------------------------------------------- #
# Earth Engine
# --------------------------------------------------------------------------- #
def wanted_days(years, months, every_n_days, today):
    """YYYYMMDD strings for the chosen months, every Nth day, within the product's record."""
    picked = []
    for year in years:
        days = [
            datetime.date(year, month, d)
            for month in months
            for d in range(1, calendar.monthrange(year, month)[1] + 1)
        ]
        days = [d for d in days if FIRST_DAY <= d < today]
        picked += [d.strftime("%Y%m%d") for d in days[::every_n_days]]
    return picked


def save_ee_days(props, wanted, cache_dir):
    """Write each wanted day in a sampleRectangle result to <day>.npz. Returns days saved."""
    by_day = defaultdict(dict)
    for key, values in props.items():
        m = EE_BAND_RE.match(key)
        if m and m.group(1) in wanted and m.group(2) in VARIABLES:
            arr = np.array(values, dtype="float32")
            arr[arr < FILL_THRESHOLD] = np.nan
            by_day[m.group(1)][m.group(2)] = arr
    for day, arrays in by_day.items():
        if set(arrays) != set(VARIABLES):
            raise ValueError(f"{day}: Earth Engine returned {sorted(arrays)}")
        np.savez_compressed(cache_dir / f"{day}.npz", **arrays)
    return len(by_day)


def fetch_days_ee(days, bbox, hour, cache_dir):
    """Fetch uncached days from Earth Engine, one request per month."""
    import ee

    from config.settings import Settings

    ee.Initialize(project=Settings().ee_project)
    col = ee.ImageCollection(EE_COLLECTION).select(list(VARIABLES))
    proj = col.first().select("sm_rootzone").projection()
    region = ee.Geometry.Rectangle(list(bbox), "EPSG:4326", False)

    cache_dir.mkdir(parents=True, exist_ok=True)
    grid_file = cache_dir / "grid.npz"
    if not grid_file.exists():
        ll = ee.Image.pixelLonLat().reproject(proj).sampleRectangle(region).getInfo()
        lat = np.array(ll["properties"]["latitude"])[:, 0]
        lon = np.array(ll["properties"]["longitude"])[0, :]
        np.savez(grid_file, window=np.array([0, lat.size, 0, lon.size]), lat=lat, lon=lon)

    todo = [d for d in days if not (cache_dir / f"{d}.npz").exists()]
    log.info(f"  {len(days) - len(todo)} days already cached, {len(todo)} to fetch")
    months = defaultdict(set)
    for day in todo:
        months[day[:6]].add(day)
    for n, (ym, month_days) in enumerate(sorted(months.items()), 1):
        start = datetime.date(int(ym[:4]), int(ym[4:]), 1)
        end = (start + datetime.timedelta(days=32)).replace(day=1)
        images = col.filterDate(start.isoformat(), end.isoformat()).filter(
            ee.Filter.stringEndsWith("system:index", f"_{hour[:4]}")
        )
        sample = images.toBands().reproject(proj).sampleRectangle(region, defaultValue=FILL_VALUE)
        props = with_retries(lambda s=sample: s.getInfo()["properties"], ym)
        saved = save_ee_days(props, month_days, cache_dir) if props else 0
        log.info(f"  [{n}/{len(months)}] {ym}: {saved} of {len(month_days)} days")


# --------------------------------------------------------------------------- #
# Earthdata search and reading
# --------------------------------------------------------------------------- #
def granule_name(g):
    try:
        return g["umm"]["GranuleUR"]
    except (KeyError, TypeError):
        return Path(g.data_links()[0]).name


def search_granules(years, months, bbox, hour, every_n_days):
    import earthaccess

    picked = []
    for year in years:
        start = f"{year}-{months[0]:02d}-01"
        last_day = calendar.monthrange(year, months[-1])[1]
        end = f"{year}-{months[-1]:02d}-{last_day}T23:59:59"
        res = earthaccess.search_data(
            short_name=SHORT_NAME, version=VERSION, temporal=(start, end), bounding_box=bbox
        )
        keep = {}
        for g in res:
            m = GRANULE_TIME_RE.search(granule_name(g))
            if not m:
                continue
            day, hhmmss = m.groups()
            if int(day[4:6]) not in months or hhmmss != hour:
                continue
            keep[day] = g
        days = sorted(keep)[::every_n_days]
        picked += [(d, keep[d]) for d in days]
        log.info(f"  {year}: {len(res)} granules found, {len(days)} days kept")
    return picked


def read_window(h5, window):
    r0, r1, c0, c1 = window
    out = {}
    for name, path in VARIABLES.items():
        arr = h5[path][r0:r1, c0:c1].astype("float32")
        arr[arr < FILL_THRESHOLD] = np.nan
        out[name] = arr
    return out


def fetch_day_earthdata(g, bbox, grid_file):
    import earthaccess
    import h5py

    fobj = earthaccess.open([g], show_progress=False)[0]
    with h5py.File(fobj, "r") as h5:
        if not grid_file.exists():
            window, lat_sub, lon_sub = grid_window(h5, bbox)
            np.savez(grid_file, window=np.array(window), lat=lat_sub, lon=lon_sub)
        return read_window(h5, tuple(np.load(grid_file)["window"]))


def fetch_days_earthdata(granules, bbox, cache_dir):
    cache_dir.mkdir(parents=True, exist_ok=True)
    grid_file = cache_dir / "grid.npz"
    todo = [(d, g) for d, g in granules if not (cache_dir / f"{d}.npz").exists()]
    log.info(f"  {len(granules) - len(todo)} days already cached, {len(todo)} to fetch")
    for n, (day, g) in enumerate(todo, 1):
        data = with_retries(lambda g=g: fetch_day_earthdata(g, bbox, grid_file), day)
        if data is not None:
            np.savez_compressed(cache_dir / f"{day}.npz", **data)
            log.info(f"  [{n}/{len(todo)}] {day} ok")


# --------------------------------------------------------------------------- #
# Aggregation
# --------------------------------------------------------------------------- #
def aggregate_daily(cache_dir, mapping):
    files = sorted(p for p in cache_dir.glob("*.npz") if p.stem.isdigit())
    if not files:
        sys.exit("No cached days to aggregate.")
    ii, jj = mapping["i"].to_numpy(), mapping["j"].to_numpy()
    frames = []
    for p in files:
        arrs = np.load(p)
        df = mapping[["lga"]].copy()
        for name in VARIABLES:
            df[name] = arrs[name][ii, jj]
        df = df.groupby("lga", as_index=False).mean()
        df.insert(1, "date", pd.to_datetime(p.stem, format="%Y%m%d"))
        frames.append(df)
    daily = pd.concat(frames, ignore_index=True)

    cells = mapping.groupby("lga").agg(
        n_cells=("i", "size"), nearest_fallback=("nearest_fallback", "any")
    )
    daily = daily.merge(cells, on="lga", how="left")
    daily["year"] = daily["date"].dt.year
    daily["month"] = daily["date"].dt.month
    return daily.sort_values(["lga", "date"]).reset_index(drop=True)


def monthly_summary(daily):
    m = daily.groupby(["lga", "year", "month"], as_index=False)[
        ["sm_rootzone", "sm_rootzone_wetness", "sm_surface"]
    ].mean()
    clim = (
        m.groupby(["lga", "month"])["sm_rootzone"]
        .agg(clim_rootzone="mean", clim_rootzone_std="std")
        .reset_index()
    )
    m = m.merge(clim, on=["lga", "month"])
    m["anomaly_rootzone"] = m["sm_rootzone"] - m["clim_rootzone"]
    m["anomaly_z"] = m["anomaly_rootzone"] / m["clim_rootzone_std"]
    return m


def season_summary(daily, dry_percentile, season_months):
    # Only growing-season days count, so fetching all 12 months never dilutes the season.
    daily = daily[daily["month"].isin(season_months)]
    # Dry day = root zone wetness below this LGA's own Nth percentile across all
    # seasons. Relative to the LGA, so sandy and clay LGAs are judged fairly.
    p = (
        daily.groupby("lga")["sm_rootzone_wetness"]
        .quantile(dry_percentile / 100)
        .rename("dry_threshold")
    )
    d = daily.merge(p, on="lga")
    d["dry_day"] = d["sm_rootzone_wetness"] < d["dry_threshold"]

    s = d.groupby(["lga", "year"], as_index=False).agg(
        season_rootzone=("sm_rootzone", "mean"),
        season_wetness=("sm_rootzone_wetness", "mean"),
        dry_day_share=("dry_day", "mean"),
        days=("date", "nunique"),
        n_cells=("n_cells", "first"),
        nearest_fallback=("nearest_fallback", "first"),
    )
    base = s.groupby("lga")["season_rootzone"].agg(lga_mean="mean", lga_std="std")
    s = s.merge(base, on="lga")
    s["anomaly_pct"] = 100 * (s["season_rootzone"] - s["lga_mean"]) / s["lga_mean"]
    s["anomaly_z"] = (s["season_rootzone"] - s["lga_mean"]) / s["lga_std"]
    s["wettest_rank"] = s.groupby("lga")["season_rootzone"].rank(ascending=False).astype(int)
    return s.drop(columns=["lga_std"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def parse_months(text):
    a, _, b = text.partition("-")
    a, b = int(a), int(b or a)
    if not (1 <= a <= b <= 12):
        raise argparse.ArgumentTypeError("months must look like 4-10")
    return list(range(a, b + 1))


def parse_args():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", choices=["earthengine", "earthdata"], default="earthengine")
    ap.add_argument(
        "--shapefile",
        help="admin level 2 (LGA) boundaries. Defaults to data/processed/kaduna_lgas.geojson "
        "unless --aoi is given alone",
    )
    ap.add_argument(
        "--aoi",
        help="state outline, e.g. kaduna_aoi.geojson. Sets the area read each day. Without "
        "--shapefile, results are one whole-state row instead of per LGA",
    )
    ap.add_argument("--state", default="Kaduna")
    ap.add_argument("--adm1-field", default="ADM1_EN")
    ap.add_argument("--adm2-field", default="ADM2_EN")
    ap.add_argument("--start-year", type=int, default=2015)
    ap.add_argument("--end-year", type=int, default=2026)
    ap.add_argument(
        "--months",
        type=parse_months,
        default=parse_months("1-12"),
        help="months to fetch; lga_daily and lga_monthly cover all of them",
    )
    ap.add_argument(
        "--season-months",
        type=parse_months,
        default=parse_months("4-10"),
        help="growing-season months that lga_season.csv summarises",
    )
    ap.add_argument(
        "--hour",
        default="133000",
        help="time step to keep (UTC, hhmmss). SPL4SMGP times are 013000, 043000, ... 223000",
    )
    ap.add_argument(
        "--every-n-days", type=int, default=1, help="keep every Nth day, for a quick first pass"
    )
    ap.add_argument("--dry-percentile", type=float, default=20)
    ap.add_argument("--out", default="data/processed/smap")
    ap.add_argument(
        "--cache",
        help="per-day windows. Defaults to data/raw/smap_cache/<source>, under the "
        "git-ignored data/raw/",
    )
    ap.add_argument(
        "--skip-download", action="store_true", help="only rebuild the CSVs from the cache"
    )
    return ap.parse_args()


def main():
    args = parse_args()
    # Libraries log every HTTP block at INFO, so only this script's progress is shown.
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    log.setLevel(logging.INFO)

    out = Path(args.out)
    cache = Path(args.cache or f"data/raw/smap_cache/{args.source}")
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading boundaries")
    aoi = load_aoi(args.aoi) if args.aoi else None
    shapefile = args.shapefile or (
        None if aoi is not None else "data/processed/kaduna_lgas.geojson"
    )
    lgas = load_lgas(shapefile, args.state, args.adm1_field, args.adm2_field) if shapefile else aoi
    bbox = bbox_of(aoi if aoi is not None else lgas)
    log.info(f"  {len(lgas)} LGAs, bbox (W,S,E,N) = {tuple(round(float(v), 3) for v in bbox)}")

    if not args.skip_download:
        years = range(args.start_year, args.end_year + 1)
        if args.source == "earthengine":
            log.info(f"Fetching Kaduna windows from Earth Engine {EE_COLLECTION}")
            today = datetime.datetime.now(datetime.UTC).date()
            days = wanted_days(years, args.months, args.every_n_days, today)
            fetch_days_ee(days, bbox, args.hour, cache)
        else:
            import earthaccess
            from dotenv import load_dotenv

            load_dotenv(".env")
            earthaccess.login()
            log.info("Searching Earthdata")
            granules = search_granules(years, args.months, bbox, args.hour, args.every_n_days)
            log.info("Fetching Kaduna windows")
            fetch_days_earthdata(granules, bbox, cache)

    grid_file = cache / "grid.npz"
    if not grid_file.exists():
        sys.exit("No grid cached yet; run once without --skip-download.")
    grid = np.load(grid_file)

    log.info("Mapping cells to LGAs")
    mapping = cell_lga_map(grid["lat"], grid["lon"], lgas)
    mapping.to_csv(out / "cell_lga_map.csv", index=False)

    log.info("Aggregating")
    daily = aggregate_daily(cache, mapping)
    daily.to_csv(out / "lga_daily.csv", index=False)
    monthly_summary(daily).to_csv(out / "lga_monthly.csv", index=False)
    season = season_summary(daily, args.dry_percentile, args.season_months)
    season.to_csv(out / "lga_season.csv", index=False)
    log.info(f"Done. Wrote lga_daily.csv, lga_monthly.csv, lga_season.csv to {out}/")


if __name__ == "__main__":
    main()
