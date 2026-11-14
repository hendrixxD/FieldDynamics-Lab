"""OpenLandMap soil water content at 33 and 1500 kPa per Kaduna LGA, read from Zenodo.

Earth Engine hosts only the 33 kPa (field capacity) layer of this dataset. The 1500 kPa
(wilting point) layer exists only as global GeoTIFFs on Zenodo, so this script reads the
Kaduna window of each file over HTTP (GDAL /vsicurl/, range requests: no full download) and
averages it over each LGA. Both suctions come from the same files and the same LGA masks, so
field capacity, wilting point and available water are consistent with each other.

Source: Hengl, T. and Gupta, S. (2019). Soil water content (volumetric %) for 33kPa and
1500kPa suctions predicted at 6 standard depths (0, 10, 30, 60, 100 and 200 cm) at 250 m
resolution (v0.1). Zenodo, record 2784001, doi:10.5281/zenodo.2784001. Mapped from measured
soil profiles; no pedotransfer function. CC-BY-SA-4.0.

The mean is over all land in each LGA, not cropland only (no cropland mask on this grid). The
Earth Engine pull (python -m data.gee_extras openlandmap) gives the cropland-only 33 kPa value,
and notebooks/a8_soil_water.ipynb compares the two to show whether the mask matters.

    python -m data.openlandmap_zenodo
"""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("openlandmap_zenodo")

ZENODO_RECORD = "2784001"  # Hengl and Gupta (2019), v0.1, the record Earth Engine cites
FILES = {  # suction -> file-name stem; "_m_" is the mean prediction (not "_md_", the error)
    "fc_33kpa": "sol_watercontent.33kPa_usda.4b1c_m_250m",
    "wp_1500kpa": "sol_watercontent.1500kPa_usda.3c2a1a_m_250m",
}
DEPTHS_CM = (0, 10, 30, 60, 100, 200)  # the dataset's standard depths
PAD_DEG = 0.05  # margin around Kaduna's bounds when reading the window
GDAL_ENV = {  # read only the bytes needed; never list the remote directory
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "4",
    "GDAL_HTTP_RETRY_DELAY": "5",
}


def file_url(stem: str, depth_cm: int) -> str:
    """GDAL path of one Zenodo GeoTIFF."""
    name = f"{stem}_b{depth_cm}..{depth_cm}cm_1950..2017_v0.1.tif"
    return f"/vsicurl/https://zenodo.org/records/{ZENODO_RECORD}/files/{name}"


def read_window(url: str, bounds: tuple[float, float, float, float]):
    """The part of one global raster inside bounds (lon/lat), nodata as NaN."""
    import rioxarray

    raster = rioxarray.open_rasterio(url, masked=True).squeeze("band", drop=True)
    west, south, east, north = bounds
    return raster.rio.clip_box(west - PAD_DEG, south - PAD_DEG, east + PAD_DEG, north + PAD_DEG)


def lga_means(window, lgas: gpd.GeoDataFrame) -> pd.Series:
    """Mean of the window over each LGA polygon (pixel centres inside the polygon)."""
    from rasterio.features import geometry_mask

    values = window.to_numpy()
    means = {}
    for row in lgas.itertuples():
        inside = ~geometry_mask(
            [row.geometry], out_shape=values.shape, transform=window.rio.transform()
        )
        means[row.lga] = float(np.nanmean(np.where(inside, values, np.nan)))
    return pd.Series(means)


def build(lgas: gpd.GeoDataFrame) -> pd.DataFrame:
    """One row per LGA, one column per suction and depth ({name}_{depth}cm_pct)."""
    bounds = tuple(lgas.total_bounds)
    columns = {}
    for name, stem in FILES.items():
        for depth in DEPTHS_CM:
            log.info(f"  reading {name} at {depth} cm")
            columns[f"{name}_{depth}cm_pct"] = lga_means(
                read_window(file_url(stem, depth), bounds), lgas
            )
    table = pd.DataFrame(columns).rename_axis("lga").reset_index()
    check(table)
    return table


def check(table: pd.DataFrame) -> None:
    """Fail loudly on empty LGAs or values outside 0 to 100 % volume."""
    values = table.drop(columns="lga")
    if values.isna().any().any():
        raise ValueError(f"LGAs with no pixels:\n{table[values.isna().any(axis=1)]}")
    if not values.stack().between(0, 100).all():
        raise ValueError("Water content outside 0-100 %: check the raster's scale")
    wp = table.filter(like="wp_1500kpa").to_numpy()
    fc = table.filter(like="fc_33kpa").to_numpy()
    if (wp >= fc).any():
        raise ValueError("Wilting point at or above field capacity somewhere")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lgas", default=str(REPO_ROOT / "data/processed/kaduna_lgas.geojson"))
    ap.add_argument("--out", default=str(REPO_ROOT / "data/processed/extras/openlandmap"))
    args = ap.parse_args()
    os.environ.update(GDAL_ENV)
    lgas = gpd.read_file(args.lgas).to_crs(4326)[["lga", "geometry"]]
    table = build(lgas)
    out = Path(args.out) / "openlandmap_lga_water_zenodo.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out, index=False)
    log.info(table.round(1).to_string(index=False))
    log.info(f"  wrote {out}")


if __name__ == "__main__":
    main()
