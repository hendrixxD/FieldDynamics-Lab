#!/usr/bin/env python3
"""
SMAP L4 root zone soil moisture for Kaduna State, summarised per LGA.

Product : SPL4SMGP V008 (SMAP L4 Global 3-hourly 9 km EASE-Grid Surface and
          Root Zone Soil Moisture Geophysical Data), NSIDC DAAC.
Boundary: data/processed/kaduna_lgas.geojson (geoBoundaries, built by data.boundaries) by
          default. Any admin level 2 file works, e.g. OCHA COD-AB with --adm1-field ADM1_EN.

What it does
  1. Reads the Kaduna LGAs and builds a bounding box.
  2. Searches Earthdata for SPL4SMGP granules in the growing-season months of
     each year, keeping ONE granule per day (default 13:30 UTC).
  3. Opens each granule remotely and reads only the Kaduna window of the
     three moisture variables (it never pulls the full ~140 MB global file
     to disk). Each day is cached as a tiny .npz, so the run can be stopped
     and resumed at any time.
  4. Maps every 9 km cell to an LGA and writes three CSVs:
        lga_daily.csv    one row per LGA per day
        lga_monthly.csv  monthly means + anomaly vs that LGA's own climatology
        lga_season.csv   one row per LGA per year: season mean, anomaly,
                         share of dry days, and rank of the year

EASE-Grid 2.0 is an equal-area grid, so a plain mean over cells is already
an area-weighted mean.

Setup
  Everything is in requirements.txt. You need a free Earthdata Login
  (urs.earthdata.nasa.gov). Put EARTHDATA_USERNAME and EARTHDATA_PASSWORD in .env,
  or use a ~/.netrc entry.

Example
  python -m data.smap_kaduna --start-year 2015 --end-year 2025 --months 4-10
  python -m data.smap_kaduna --every-n-days 5 --start-year 2024 --end-year 2024  # quick test
"""

import argparse
import calendar
import logging
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

SHORT_NAME = "SPL4SMGP"
VERSION = "008"

# Variable name in output -> path inside the SPL4SMGP HDF5 file
VARIABLES = {
    "sm_rootzone": "Geophysical_Data/sm_rootzone",  # m3/m3, 0-100 cm
    "sm_surface": "Geophysical_Data/sm_surface",  # m3/m3, 0-5 cm
    # 0-1, wilting point to saturation
    "sm_rootzone_wetness": "Geophysical_Data/sm_rootzone_wetness",
}
FILL_THRESHOLD = -9000.0  # SMAP fill value is -9999
GRANULE_TIME_RE = re.compile(r"_(\d{8})T(\d{6})_")


# --------------------------------------------------------------------------- #
# Boundaries
# --------------------------------------------------------------------------- #
def load_lgas(shapefile, state, adm1_field, adm2_field):
    import geopandas as gpd

    gdf = gpd.read_file(shapefile)
    for field in filter(None, (adm1_field, adm2_field)):
        if field not in gdf.columns:
            sys.exit(f"Field '{field}' not in shapefile. Columns are: {list(gdf.columns)}")
    # A file that already holds only the state's LGAs needs no state filter.
    if not adm1_field:
        lgas = gdf.copy()
    else:
        lgas = gdf[gdf[adm1_field].str.strip().str.lower() == state.lower()].copy()
    if lgas.empty:
        states = sorted(gdf[adm1_field].unique())
        sys.exit(f"No rows with {adm1_field} == '{state}'. Values present: {states}")
    lgas = lgas.to_crs(4326)
    lgas = lgas[[adm2_field, "geometry"]].rename(columns={adm2_field: "lga"})
    return lgas.reset_index(drop=True)


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
            f"  {len(missing)} LGA(s) smaller than a 9 km cell, using nearest cell: "
            f"{', '.join(missing)}"
        )
    return mapping.reset_index(drop=True)


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


def fetch_days(granules, bbox, cache_dir, retries=3):
    import earthaccess
    import h5py

    cache_dir.mkdir(parents=True, exist_ok=True)
    grid_file = cache_dir / "grid.npz"
    todo = [(d, g) for d, g in granules if not (cache_dir / f"{d}.npz").exists()]
    log.info(f"  {len(granules) - len(todo)} days already cached, {len(todo)} to fetch")

    for n, (day, g) in enumerate(todo, 1):
        for attempt in range(1, retries + 1):
            try:
                fobj = earthaccess.open([g])[0]
                with h5py.File(fobj, "r") as h5:
                    if not grid_file.exists():
                        window, lat_sub, lon_sub = grid_window(h5, bbox)
                        np.savez(grid_file, window=np.array(window), lat=lat_sub, lon=lon_sub)
                    window = tuple(np.load(grid_file)["window"])
                    data = read_window(h5, window)
                np.savez_compressed(cache_dir / f"{day}.npz", **data)
                log.info(f"  [{n}/{len(todo)}] {day} ok")
                break
            # Remote reads fail in many library-specific ways; log it and retry the day.
            except Exception as exc:  # noqa: BLE001
                log.info(f"  [{n}/{len(todo)}] {day} attempt {attempt} failed: {exc}")
                time.sleep(5 * attempt)
        else:
            log.info(f"  giving up on {day}; rerun the script later to retry it")


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


def season_summary(daily, dry_percentile):
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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--shapefile",
        default="data/processed/kaduna_lgas.geojson",
        help="admin level 2 (LGA) boundaries, any format geopandas reads",
    )
    ap.add_argument("--state", default="Kaduna")
    ap.add_argument(
        "--adm1-field",
        default="",
        help="state column to filter on; leave empty if the file is one state",
    )
    ap.add_argument("--adm2-field", default="lga")
    ap.add_argument("--start-year", type=int, default=2015)
    ap.add_argument("--end-year", type=int, default=2025)
    ap.add_argument(
        "--months",
        type=parse_months,
        default=parse_months("4-10"),
        help="growing-season months, e.g. 4-10 for April to October",
    )
    ap.add_argument(
        "--hour",
        default="133000",
        help="granule time to keep (UTC, hhmmss). SPL4SMGP times are 013000, 043000, ... 223000",
    )
    ap.add_argument(
        "--every-n-days",
        type=int,
        default=1,
        help="keep every Nth day; 3 or 5 is fine for a quick first pass",
    )
    ap.add_argument("--dry-percentile", type=float, default=20)
    ap.add_argument("--out", default="data/processed/smap")
    ap.add_argument(
        "--cache",
        default="data/raw/smap_cache",
        help="per-day Kaduna windows; raw pulls live under the git-ignored data/raw/",
    )
    ap.add_argument(
        "--skip-download",
        action="store_true",
        help="only rebuild the CSVs from what is already cached",
    )
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out = Path(args.out)
    cache = Path(args.cache)
    out.mkdir(parents=True, exist_ok=True)

    log.info("Loading LGA boundaries")
    lgas = load_lgas(args.shapefile, args.state, args.adm1_field, args.adm2_field)
    bbox = bbox_of(lgas)
    log.info(f"  {len(lgas)} LGAs, bbox (W,S,E,N) = {tuple(round(float(v), 3) for v in bbox)}")

    if not args.skip_download:
        import earthaccess
        from dotenv import load_dotenv

        load_dotenv()
        earthaccess.login()
        if args.start_year < 2015 or (args.start_year == 2015 and args.months[0] < 4):
            log.info("  note: SPL4SMGP starts 2015-03-31")
        log.info("Searching Earthdata")
        years = range(args.start_year, args.end_year + 1)
        granules = search_granules(years, args.months, bbox, args.hour, args.every_n_days)
        log.info("Fetching Kaduna windows")
        fetch_days(granules, bbox, cache)

    grid_file = cache / "grid.npz"
    if not grid_file.exists():
        sys.exit("No grid cached yet; run once without --skip-download.")
    grid = np.load(grid_file)

    log.info("Mapping 9 km cells to LGAs")
    mapping = cell_lga_map(grid["lat"], grid["lon"], lgas)
    mapping.to_csv(out / "cell_lga_map.csv", index=False)

    log.info("Aggregating")
    daily = aggregate_daily(cache, mapping)
    daily.to_csv(out / "lga_daily.csv", index=False)
    monthly_summary(daily).to_csv(out / "lga_monthly.csv", index=False)
    season_summary(daily, args.dry_percentile).to_csv(out / "lga_season.csv", index=False)
    log.info(f"Done. Wrote lga_daily.csv, lga_monthly.csv, lga_season.csv to {out}/")


if __name__ == "__main__":
    main()
