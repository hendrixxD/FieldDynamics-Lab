# Data Sources

Every dataset we use is logged here. Fill in each row when the data is pulled, using the real values for that pull. Rows left blank have not been pulled yet.

## NASA datasets

| Name | Product version | Resolution | Date range | Access date | Link |
| --- | --- | --- | --- | --- | --- |
| GPM | | | | | |
| MODIS | | | | | |
| SMAP | SPL4SMGP V008, Earth Engine `NASA/SMAP/SPL4SMGP/008`, 13:30 UTC step daily: `sm_rootzone`, `sm_surface`, `sm_rootzone_wetness` | 9 km native, held by Earth Engine on a ~0.095° lat/lon grid; mean of cell centres per LGA | 2015-03-31 to 2026-11-10 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_SMAP_SPL4SMGP_008), [NSIDC](https://nsidc.org/data/spl4smgp/versions/8) |
| Landsat | | | | | |
| SRTM | | | | | |
| GRACE-FO | | | | | |
| ECOSTRESS | | | | | |
| NASA POWER | | | | | |
| GEDI | | | | | |
| NASA GIBS | | | | | |

SMAP check against the native files: on 51 days (every 5th day of April to October 2024, and 8 days of July 2023), per-LGA daily means from Earth Engine were compared with the same days read from the native EASE-Grid HDF5 files via Earthdata (`python -m data.smap_kaduna --source earthdata`). Across all 23 LGAs, `sm_rootzone` differs by 0.0032 m³/m³ on average (bias +0.0002, native mean 0.209, correlation 0.996). Kauru differs most, by 0.019 on average and 0.025 at most. `sm_rootzone_wetness` differs by 0.0065 on average (correlation 0.997).

## Partner and other datasets

| Name | Product version | Resolution | Date range | Access date | Link |
| --- | --- | --- | --- | --- | --- |
| iSDAsoil | v1, Earth Engine `ISDASOIL/Africa/v1`, all 21 layers | 30 m native, averaged to the 1 km grid | Single map, no time dimension | 2026-11-09 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/ISDASOIL_Africa_v1_ph) |
| geoBoundaries | gbOpen NGA ADM1 (NGA-ADM1-27671186) and ADM2 (NGA-ADM2-59680162), release commit 9469f09; source GRID3, CC BY 4.0 | Vector | Boundaries as of 2022 | 2026-11-09 | [API](https://www.geoboundaries.org/api/current/gbOpen/NGA/ADM2/) |
| ESA WorldCover | v200, Earth Engine `ESA/WorldCover/v200`, cropland class 40 only | 10 m native, averaged to the 1 km grid | 2021 | 2026-11-09 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v200) |
| FAO ECOCROP | | | | | |
| OpenStreetMap or CARTO Positron | | | | | |
| Esri World Imagery | | | | | |

## Validation references

Published IAR Samaru and Ahmadu Bello University trial results used to check rotation scoring go here, with a full citation for each.

| Citation | Finding used | Link |
| --- | --- | --- |
| | | |
