# Data Contract

**Status: draft v1.** No data has been pulled yet, so this is the agreed target. Confirm every column against the real data at first pull. Any schema change follows the versioning rule below and lands in the same pull request as the code that causes it.

This contract defines what the data track hands to the modeling, API, and frontend tracks. See also [engineering standards](engineering-standards.md), [map architecture](map-architecture.md), and [sources](sources.md).

## Conventions

- **Coordinates:** stored in EPSG:4326. Area and distance maths use UTM zone 32N, then convert back.
- **Grids:** 5 km for the state view and 1 km for the LGA view.
- **Naming:** column names carry units where the value has one, such as `rain_mm` or `slope_deg`.
- **Types:** `float32` for measurements and categorical dtype for labels.
- **Filled values:** gap-filled values are flagged, and flags lower confidence on the strategy cards.
- **Thresholds:** values such as the heat-day limit live in `config/`, with a cited source.

## Feature table

- **Path:** `data/processed/features_v1.parquet`
- **Built by:** `python -m data.build`
- **Grain:** one row per grid cell per year, with `cell_id` and `year` as the unique key

Ranges for rainfall, soil moisture, and NDVI come from the engineering standards. All other ranges are proposed and need checking at first pull.

### Keys and location

| Column | Type | Unit | Valid range | Source |
| --- | --- | --- | --- | --- |
| `cell_id` | string | none | not null, unique per year | Pipeline |
| `lga` | categorical | none | Kaduna LGA names | geoBoundaries |
| `lat_deg` | float32 | degrees | Within Kaduna | Pipeline |
| `lon_deg` | float32 | degrees | Within Kaduna | Pipeline |
| `year` | int16 | year | Within the study period | Pipeline |

### Season and climate

| Column | Type | Unit | Valid range | Source |
| --- | --- | --- | --- | --- |
| `onset_doy` | int16 | day of year | 91 to 366, nullable | GPM, rule in engineering standards |
| `cessation_doy` | int16 | day of year | 91 to 366, nullable | GPM |
| `season_length_days` | int16 | days | 0 to 275 | Derived |
| `rain_season_mm` | float32 | mm | 0 or more | GPM |
| `rain_extreme_days` | int16 | days | 0 to 366 | GPM |
| `dry_spell_max_days` | int16 | days | 0 to 366 | GPM |
| `temp_mean_c` | float32 | °C | -10 to 55 | NASA POWER |
| `temp_max_c` | float32 | °C | -10 to 60 | NASA POWER |
| `temp_min_c` | float32 | °C | -15 to 45 | NASA POWER, used for frost risk |
| `heat_days` | int16 | days | 0 to 366 | NASA POWER |
| `lst_c` | float32 | °C | -10 to 80, nullable | ECOSTRESS |
| `et0_season_mm` | float32 | mm | 0 or more | FAO-56 from NASA POWER |
| `season_type` | categorical | none | Cluster names | K-means output |

### Soil moisture, vegetation, and water

| Column | Type | Unit | Valid range | Source |
| --- | --- | --- | --- | --- |
| `soil_moisture_m3_m3` | float32 | m³/m³ | 0 to 0.6 | SMAP |
| `soil_moisture_anomaly_z` | float32 | standard deviations | -6 to 6 | Derived from SMAP |
| `soil_moisture_filled` | bool | none | true or false | Pipeline |
| `ndvi_peak` | float32 | none | -1 to 1 | MODIS, Landsat |
| `ndvi_mean` | float32 | none | -1 to 1 | MODIS, Landsat |
| `ndvi_filled` | bool | none | true or false | Pipeline |
| `bare_days` | int16 | days | 0 to 366 | Derived from NDVI |
| `water_storage_anomaly_cm` | float32 | cm | -100 to 100, nullable | GRACE-FO |
| `biomass_mg_ha` | float32 | Mg/ha | 0 or more, nullable | GEDI |

### Land and soil

| Column | Type | Unit | Valid range | Source |
| --- | --- | --- | --- | --- |
| `elevation_m` | float32 | m | 0 to 2000 | SRTM |
| `slope_deg` | float32 | degrees | 0 to 90 | SRTM |
| `clay_pct` | float32 | % | 0 to 100 | iSDAsoil |
| `sand_pct` | float32 | % | 0 to 100 | iSDAsoil |
| `organic_carbon_g_kg` | float32 | g/kg | 0 or more | iSDAsoil |
| `soil_ph` | float32 | pH | 3 to 11 | iSDAsoil |

## Map outputs

All are built by `python -m model.build` and stored as static files, except where noted.

| Output | Path | Contents |
| --- | --- | --- |
| State overlay | `static/layers/state/{layer}.png` | Coloured 5 km image |
| LGA overlay | `static/layers/lga/{lga}/{layer}.png` | Coloured 1 km image |
| Overlay bounds | Same path as the PNG, ending `.bounds.json` | `south`, `west`, `north`, and `east` in degrees |
| LGA grid | Served at `/api/layers/{lga}/grid.geojson` | 1 km cells in EPSG:4326 |

- **Layers:** `soil_moisture`, `heat_stress`, `erosion_risk`, and `water_stress`
- **Grid cell properties:** `cell_id`, `lga`, one value per layer, and the filled flags

## Colour scale config

`config/layers.yaml` is the single source for colours, used by both the PNG script and `/api/legend/{layer}`. Each layer defines:

| Field | Meaning |
| --- | --- |
| `label` | Plain-language name for the legend |
| `unit` | Display unit |
| `value_min`, `value_max` | Fixed scale limits |
| `colours` | Ordered colour stops |
| `native_resolution` | The source data's real resolution, shown in the legend |

## Versioning

A changed feature table gets a new file name, such as `features_v2.parquet`, and this document is updated in the same pull request.
