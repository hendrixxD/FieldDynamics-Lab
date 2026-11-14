#!/usr/bin/env python3
"""
Per-LGA statistics for downloaded rasters, built for FAO GAEZ v4 layers.

GAEZ v4 (FAO and IIASA) gives crop suitability, attainable yield and yield
gaps for 50+ crops on a ~9 km grid, under rain-fed or irrigated, low or high
inputs, for 1981-2010 and future climates. It is not in Earth Engine, so this
script works from the GeoTIFFs: download them, then summarise per LGA.

Getting the files
  1. Open the GAEZ v4 Data Viewer (https://gaez.fao.org/pages/data-viewer).
  2. Theme 4 "Suitability and Attainable Yield"; pick e.g. "Crop suitability
     index (value)", rain-fed, high or low input, 1981-2010, and a crop.
  3. Click a pixel; "download this raster" gives a URL like
       https://s3.eu-west-1.amazonaws.com/data.gaezdev.aws.fao.org/res05/...tif
     The viewer's attribute table can also export the URLs of many layers.
  4. Put one URL per line in a text file and pass it with --urls, or drop the
     .tif files in the input folder yourself.
  Suggested set for the rotation engine: suitability index (rain-fed, low
  input) for maize, sorghum, pearl millet, rice, soybean, cowpea, groundnut,
  sesame, yam, cassava; the crop codes appear in each file name.

Output
  data/processed/gaez/raster_lga.csv: one row per LGA, one column per raster
  (column = file name without .tif): area-weighted mean (or majority class
  with --categorical), plus <name>__n_pixels.

Run (from the repo root)
  python -m data.raster_to_lga --urls data/raw/gaez/urls.txt
  python -m data.raster_to_lga --input data/raw/gaez
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("raster_to_lga")

REPO_ROOT = Path(__file__).resolve().parents[1]


def download(urls_file, folder):
    import requests

    folder.mkdir(parents=True, exist_ok=True)
    urls = [
        u.strip()
        for u in Path(urls_file).read_text().splitlines()
        if u.strip() and not u.startswith("#")
    ]
    for n, url in enumerate(urls, 1):
        path = folder / url.rstrip("/").split("/")[-1].split("?")[0]
        if path.exists() and path.stat().st_size > 0:
            log.info(f"  [{n}/{len(urls)}] {path.name} cached")
            continue
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)
        log.info(f"  [{n}/{len(urls)}] {path.name} {len(r.content) / 1e6:.1f} MB")


def zonal(raster_path, lgas, categorical):
    import rioxarray  # noqa: F401  (registers .rio)
    import xarray as xr

    with xr.open_dataarray(raster_path, engine="rasterio") as src:
        src = src.squeeze(drop=True)
        nodata = src.rio.nodata
        # cut to Kaduna and load into memory, so the file is closed cleanly
        da = src.rio.clip_box(*lgas.to_crs(src.rio.crs).total_bounds).load()
    gdf = lgas.to_crs(da.rio.crs)
    rows = []
    for r in gdf.itertuples():
        try:
            cut = da.rio.clip([r.geometry], all_touched=True, drop=True)
        except Exception:  # noqa: BLE001 - LGA outside the raster, kept as NaN
            rows.append((r.lga, np.nan, 0))
            continue
        v = cut.values.astype("float64").ravel()
        if nodata is not None:
            v = v[v != nodata]
        v = v[np.isfinite(v)]
        if v.size == 0:
            rows.append((r.lga, np.nan, 0))
        elif categorical:
            vals, counts = np.unique(v, return_counts=True)
            rows.append((r.lga, vals[np.argmax(counts)], v.size))
        else:
            rows.append((r.lga, v.mean(), v.size))
    return rows


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--input", default=str(REPO_ROOT / "data/raw/gaez"))
    ap.add_argument("--urls", help="text file, one raster URL per line, downloaded into --input")
    ap.add_argument("--categorical", action="store_true", help="majority class instead of mean")
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/gaez/raster_lga.csv"))
    args = ap.parse_args()

    import geopandas as gpd

    folder = Path(args.input)
    if args.urls:
        log.info("Downloading")
        download(args.urls, folder)
    rasters = sorted(folder.glob("*.tif"))
    if not rasters:
        sys.exit(f"No .tif files in {folder}")

    lgas = gpd.read_file(args.lgas)[["lga", "geometry"]]
    table = pd.DataFrame({"lga": lgas["lga"]})
    for n, path in enumerate(rasters, 1):
        rows = zonal(path, lgas, args.categorical)
        stats = pd.DataFrame(rows, columns=["lga", path.stem, f"{path.stem}__n_pixels"])
        table = table.merge(stats, on="lga", how="left")
        ok = stats[path.stem].notna().sum()
        log.info(
            f"  [{n}/{len(rasters)}] {path.stem}: {ok}/{len(lgas)} LGAs, "
            f"range {stats[path.stem].min():.3g} to {stats[path.stem].max():.3g}"
        )

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(args.out, index=False)
    log.info(f"Wrote {len(table)} LGAs x {len(rasters)} rasters to {args.out}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
