# Data Sources

Every dataset we use is logged here. Fill in each row when the data is pulled, using the real values for that pull. Rows left blank have not been pulled yet.

## NASA datasets

Access date is the day the processed table was first committed (2026-11-14 for every pull); each pull script's docstring gives the exact command. All Earth Engine pulls average each LGA on Google's servers, so only small tables are downloaded.

| Name | Product version | Resolution | Date range | Access date | Link |
| --- | --- | --- | --- | --- | --- |
| GPM | IMERG Final Run V07, half-hourly, Earth Engine `NASA/GPM_L3/IMERG_V07` (1998 to May 2000 from the TRMM-era extension, flagged `imerg_trmm_era`) | 0.1° (about 11 km) | 1998-01-01 to 2026-11-11 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V07) |
| MODIS | MOD13Q1 V6.1 NDVI, 16-day, Earth Engine `MODIS/061/MOD13Q1` (cropland and all land) | 250 m | 2000-02-18 to 2026-10-16 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD13Q1) |
| MODIS | MOD11A2 V6.1 land surface temperature, 8-day, `MODIS/061/MOD11A2` | 1 km | 2000-02-18 to 2026-11-01 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD11A2) |
| MODIS | MOD16A2GF V6.1 actual and potential ET, 8-day, `MODIS/061/MOD16A2GF` | 500 m | 2000-01-01 to 2025-12-27 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD16A2GF) |
| SMAP | SPL4SMGP V008, Earth Engine `NASA/SMAP/SPL4SMGP/008`, 13:30 UTC step daily: `sm_rootzone`, `sm_surface`, `sm_rootzone_wetness` | 9 km native, held by Earth Engine on a ~0.095° lat/lon grid; mean of cell centres per LGA | 2015-03-31 to 2026-11-10 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_SMAP_SPL4SMGP_008), [NSIDC](https://nsidc.org/data/spl4smgp/versions/8) |
| Landsat | HLS v2 (Landsat 8/9 HLSL30 with Sentinel-2 HLSS30), `NASA/HLS/HLSL30/v002`, `NASA/HLS/HLSS30/v002`: dry and peak season NDVI medians, a check on MODIS drift | 30 m | 2014 to 2026 seasons | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_HLS_HLSL30_v002) |
| SRTM | SRTM GL1 v3, `USGS/SRTMGL1_003`: elevation, slope, cropland slope shares | 30 m | Static (2000) | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/USGS_SRTMGL1_003) |
| NASA POWER | Daily API, community AG: temperature, humidity, dew point, solar radiation, wind (rain pulled but not used: see PRD section 4) | MERRA-2 grid, 0.5° x 0.625° | 1981-01-01 to latest (solar radiation filled with FAO-56 Eq. 50 where missing, flagged) | 2026-11-14 | [API](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) |
| GLDAS | GLDAS-2.1 Noah, 3-hourly, `NASA/GLDAS/V021/NOAH/G025/T3H`: root-zone and surface soil moisture, ET (soil moisture before SMAP; its post-2015 drying is not trusted, see A7) | 0.25° | 2000-01-01 to 2026-10-15 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_GLDAS_V021_NOAH_G025_T3H) |
| VIIRS | VNP13A1 V2 NDVI, 16-day, `NASA/VIIRS/002/VNP13A1`: MODIS continuity check | 500 m | 2012-01-17 to 2026-10-24 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/NASA_VIIRS_002_VNP13A1) |
| GRACE-FO | Not used: about 300 km footprint covers Kaduna in a handful of pixels (PRD section 4). Long-term water trends come from rain, water balance and dry spells (A7) | | ECOSTRESS | Not used: patchy coverage over Nigeria (PRD section 4). Surface heat comes from MODIS LST, evapotranspiration from FAO-56 and MODIS ET | | | | |
| GEDI | Not used: measures forest structure and biomass, not annual crops (PRD section 4) | | | | |
| NASA GIBS | Not used yet: the map (F8) is not built | | | | |

SMAP check against the native files: on 51 days (every 5th day of April to October 2024, and 8 days of July 2023), per-LGA daily means from Earth Engine were compared with the same days read from the native EASE-Grid HDF5 files via Earthdata (`python -m data.smap_kaduna --source earthdata`). Across all 23 LGAs, `sm_rootzone` differs by 0.0032 m³/m³ on average (bias +0.0002, native mean 0.209, correlation 0.996). Kauru differs most, by 0.019 on average and 0.025 at most. `sm_rootzone_wetness` differs by 0.0065 on average (correlation 0.997).

## Partner and other datasets

| Name | Product version | Resolution | Date range | Access date | Link |
| --- | --- | --- | --- | --- | --- |
| iSDAsoil | v1, Earth Engine `ISDASOIL/Africa/v1`, all 21 layers | 30 m native, averaged to the 1 km grid | Single map, no time dimension | 2026-11-09 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/ISDASOIL_Africa_v1_ph) |
| geoBoundaries | gbOpen NGA ADM1 (NGA-ADM1-27671186) and ADM2 (NGA-ADM2-59680162), release commit 9469f09; source GRID3, CC BY 4.0 | Vector | Boundaries as of 2022 | 2026-11-09 | [API](https://www.geoboundaries.org/api/current/gbOpen/NGA/ADM2/) |
| ESA WorldCover | v200, Earth Engine `ESA/WorldCover/v200`, cropland class 40 only | 10 m native, averaged to the 1 km grid | 2021 | 2026-11-09 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/ESA_WorldCover_v200) |
| CHIRPS | v2.0 Final daily, UCSB Climate Hazards Center, Earth Engine `UCSB-CHG/CHIRPS/DAILY` (rainfall everywhere, 1981 on) | 0.05° (about 5.5 km) | 1981-01-01 to 2026-09-30 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/UCSB-CHG_CHIRPS_DAILY) |
| ERA5-Land | Daily aggregates, `ECMWF/ERA5_LAND/DAILY_AGGR`: independent check on POWER temperature and on GLDAS soil water | 0.1° | 1981-01-01 to 2026-11-05 | 2026-11-14 | [Catalog](https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_DAILY_AGGR) |
| OpenLandMap | Soil water content at 33 and 1500 kPa, Hengl and Gupta (2019), v0.1, doi:10.5281/zenodo.2784001 (A8 reference) | 250 m | Static | 2026-11-14 | [Zenodo](https://zenodo.org/records/2784001) |
| FAO ECOCROP | Crop temperature, rain, pH and texture ranges, from the Recocrop 0.4-2 R package (Hijmans, GPL-3), file pinned by SHA-256 in `config/models.yaml` | Per crop (14 crops) | Static | 2026-11-14 | [Recocrop](https://github.com/cran/Recocrop/tree/0.4-2) |
| KDBS market prices | Kaduna State Bureau of Statistics retail price API, 24 commodities, all 23 LGAs (export dated in the file name; the API gives no collection date) | LGA | Export of 2026-11-14 | 2026-11-14 | [API](https://kdbs-server.azurewebsites.net/api/v1/market-prices) |
| OpenStreetMap or CARTO Positron | Not used yet: the map (F8) is not built | | | | |
| Esri World Imagery | Not used yet: the map (F8) is not built | | | | |

## Validation references

Published trials used to check rotation scoring (A13). Every status in `config/rotations.yaml` was checked against its paper on 14 Nov 2026; a study never admits or removes a candidate.

| Citation | Finding used | Link |
| --- | --- | --- |
| Franke et al. (2008), Nutrient Cycling in Agroecosystems 82: 117 to 135 (Zaria and Ibadan) | Maize after dual-purpose soybean gave the highest maize yields (RT02); forage legume plus manure (RT08); Zaria soils need P and S, not N alone (card soil-test line) | Full paper still to be pulled before quoting yields (PRD sources) |
| Usman (2017), ABU Zaria thesis on long-term rotation and maize yield | Continuous maize as the low soil quality baseline (RT01); maize then soybean and cowpea (RT02, RT03) | [ABU Kubanni](https://kubanni.abu.edu.ng/items/c9eabdda-8da7-4635-ad62-91ab58a1988a/full) |
| Ebireri (2013), ABU Zaria thesis on soil quality under legume management | Legume cover raised organic carbon (RT08) | [ABU Kubanni](https://kubanni.abu.edu.ng/handle/123456789/1144) |
| CIAT, crop rotation and nitrogen in the northern Guinea savanna | Maize then soybean or cowpea (RT02, RT03) | [Alliance Bioversity-CIAT](https://alliancebioversityciat.org/publications-data/effect-crop-rotation-and-nitrogen-fertilization-yield-and-nitrogen-efficiency) |
| Kermah et al. (2018), Experimental Agriculture 55(5): 673 to 691 (northern Ghana Guinea savanna) | Groundnut and soybean added 0.38 to 1.01 t/ha to the next maize (RT04) | [doi:10.1017/S0014479718000273](https://doi.org/10.1017/S0014479718000273) |
| 11-year northern Ghana Guinea savanna trial, Journal of Agronomy (2006) | Maize then groundnut the best combination (RT04); maize after cassava declined slowly (RT09) | [scialert ja.2006.232.238](https://scialert.net/abstract/?doi=ja.2006.232.238) |
| Shuaibu, Garba and Voncir (2015), AJFAND 15(3): 10060 to 10076 (Bauchi) | Sorghum after cowpea or soybean beat sorghum after fallow (RT05) | Citation in `config/rotations.yaml` |
| Bado, Bationo and Cescas (2006) (Burkina Faso Guinean savanna); Bagayoko et al. (2000) | Cowpea and groundnut before sorghum raised sorghum yield (RT05, RT06a) | Citation in `config/rotations.yaml` |
| Bationo and Ntare (2000) (Niger) | Millet then groundnut (RT06b) | Citation in `config/rotations.yaml` |
| Kureh, Kamara and Tarfa (2001 to 2003), JARTS (northern Guinea savanna, Nigeria) | Legume rotation cut Striga 35% after one year, 76% after two (RT07, Striga trap crop credit) | [jarts.info](https://www.jarts.info) |
| NiMet Seasonal Climate Prediction 2025, Kaduna State Farmers' Weather Guide (HEDA) | A2 onset check per LGA; July 10 to 25 dry-spell window (not resolvable at LGA scale, see A2 fix) | [HEDA](https://ejsdprogram.hedang.org/?p=2450) |
