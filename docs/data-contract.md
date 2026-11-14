# Data Contract

**Status: draft v1.** The iSDA soil grid below has been pulled and checked against real data. Everything else is still the agreed target: confirm each column at its first pull. Any schema change follows the versioning rule below and lands in the same pull request as the code that causes it.

This contract defines what the data track hands to the modeling, API, and frontend tracks. See also [engineering standards](engineering-standards.md), [map architecture](map-architecture.md), and [sources](sources.md).

## Conventions

- **Coordinates:** stored in EPSG:4326. Area and distance maths use UTM zone 32N, then convert back.
- **Grids:** 5 km for the state view and 1 km for the LGA view. The grid is defined in UTM zone 32N with edges on whole kilometres (`config/grid.yaml`), so every dataset lands on the same cells.
- **Cell ids:** `cell_id` is the cell's south-west corner in UTM 32N metres, such as `E302000_N1150000`. It stays the same whatever area a pull covers, so tables join on it.
- **LGAs:** from geoBoundaries (`config/boundaries.yaml`). A cell belongs to the LGA holding its centre, and cells whose centre is outside Kaduna are dropped.
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
| `clay_pct_0_20cm` | float32 | % | 0 to 100 | iSDAsoil grid |
| `sand_pct_0_20cm` | float32 | % | 0 to 100 | iSDAsoil grid |
| `organic_carbon_g_kg_0_20cm` | float32 | g/kg | 0 or more | iSDAsoil grid |
| `soil_ph_0_20cm` | float32 | pH | 3 to 11 | iSDAsoil grid |

The feature table can take any other column from the iSDA soil grid below as the models need it.

## iSDA soil grid

- **Path:** `data/processed/isda_kaduna_1000m.parquet`, plus `data/processed/kaduna_lgas.geojson`
- **Built by:** `python -m data.isda`, which keeps its raw tiles in `data/raw/isda/grid_1000m/`
- **Grain:** one row per 1 km cell, with `cell_id` unique. iSDA has no time dimension, so there is no `year`.
- **Method:** Earth Engine back-transforms each 30 m layer to real units, then averages it into the cell. Texture class and FCC take the most common class instead. Ranges are set in `config/isda.yaml`.

| Column | Type | Unit | Valid range | Notes |
| --- | --- | --- | --- | --- |
| `cell_id`, `lga`, `lat_deg`, `lon_deg` | as above | | not null | Keys, as in the feature table |
| `{property}_{depth}` | float32 | in the name | per property | 18 properties at `0_20cm` and `20_50cm`, listed below |
| `{property}_{depth}_lower`, `_upper` | float32 | in the name | lower ≤ value ≤ upper | iSDA's ±1 standard deviation, back-transformed |
| `bedrock_depth_cm`, `_lower`, `_upper` | float32 | cm | 0 to 200 | Depth to bedrock, capped at 200 |
| `texture_class_{depth}` | Int8 | none | 1 to 12 | USDA texture class code |
| `texture_name_{depth}` | categorical | none | 12 USDA names | Such as Sandy Loam |
| `fcc_code` | Int16 | none | 0 to 2999 | Fertility Capability Classification code |
| `cropland_fraction` | float32 | 0 to 1 | 0 to 1 | Share of the cell that ESA WorldCover 2021 maps as cropland |

The 18 properties, with units in the name: `clay_pct`, `sand_pct`, `silt_pct`, `stone_pct`, `bulk_density_g_cm3`, `soil_ph`, `organic_carbon_g_kg`, `total_carbon_g_kg`, `total_nitrogen_g_kg`, `cec_cmolc_kg`, and extractable `phosphorus_ppm`, `potassium_ppm`, `calcium_ppm`, `magnesium_ppm`, `sulphur_ppm`, `zinc_ppm`, `iron_ppm`, `aluminium_ppm`.

Soil values can be null where iSDA has no data, such as water and some built-up areas. Keys are never null.

iSDA is modelled at 30 m from sparse soil samples. Averaging to 1 km does not add detail, and the map legend should say so.

## Model outputs (LGA level)

Built by `python -m model.build` into `data/processed/models/`. All are LGA-level: one row per LGA (or LGA and season type, or LGA and crop), not per grid cell. Method and results: [model results](model-results.md).

| Output | Path | Grain | Columns the app reads |
| --- | --- | --- | --- |
| Season types per year | `a5/a5_lga_year_types.csv` | LGA, year (2000 to 2024) | `season_type`, `p_<type>` membership probabilities |
| Season type shares | `a5/a5_lga_type_share.csv` | LGA | Share of years in each type |
| Season strip profiles | `a5/a5_monthly_profiles.csv` | LGA, season type, month | `rain_mm`, `ndvi_crop_sg`, `power_tmax_mean_c`, `power_heat_days`, `water_balance_mm`, `n_years`, `source` |
| Stress by season type | `a6/a6_lga_season_type_stress.csv` | LGA, season type | `observed_share_stressed`, `observed_years`, `card_source` (`weather_risk_a2_a9` while A6 has no skill) |
| Stress drivers | `a6/a6_lga_drivers.csv` | LGA | `driver_1` to `driver_3` with shares (Learn only while A6 has no skill) |
| Crop water by LGA | `a9/a9_lga_crop_summary.csv` | LGA, crop | `water_need_mm`, `short_share`, `fits_season_share`, `riskiest_stage_label`, `et0_filled_share` |
| Crop water by season type | `a9/a9_lga_crop_season_type.csv` | LGA, crop, season type | As above, plus `years` (years of that type behind each share) |

- Season type names: `Typical`, `Dry spell`, `Late start`, `Short and dry`.
- Crops: `maize`, `sorghum`, `millet`, `soybean`, `cowpea`, `groundnut`, `sesame`.
- LGA names follow the feature tables (`Jema'a`).

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
