# Model results: A3, A4, A5, A6, A7, A9, A10 and A12

Rebuild with `python -m model.build`, which reads `data/processed/` and writes `data/processed/models/a5/`, `a6/` and `a9/`. Settings, with their sources, are in `config/models.yaml` and `config/crops.yaml`. Each run is logged in [the experiment log](experiments.csv). The evaluation notebooks are `notebooks/a5_season_types.ipynb`, `notebooks/a6_stress_model.ipynb` and `notebooks/a9_water_balance.ipynb`.

## A5. Season types

**What it does.** It groups every LGA-year from 2000 to 2024 into one of four season types. These drive the season selector (PRD I8, F3c).

**Inputs.** Seven weather features, each as an anomaly against the LGA's own average, so "late" means late for that LGA:

- onset day;
- cessation day;
- season length;
- April to October rain;
- longest dry spell;
- count of 10+ day dry spells;
- heat days.

Peak cropland greenness is not an input. It is kept aside as an independent check. Kaduna North and South are left out of the fit (little cropland) and assigned afterwards.

**Model.** K-means with k = 4, standardized inputs and a fixed seed.

- The standards say to choose k by silhouette, which would give k = 2.
- But silhouette sits between 0.19 and 0.21 for every k from 2 to 6. That is below the 0.25 Kaufman and Rousseeuw give as the floor for real structure: Kaduna seasons form a continuum, not natural groups.
- At k = 4 the score is within 0.015 of the best, and it gives the selector its four scenarios.

**Stability and the GMM check.**

- Seed agreement: adjusted Rand index of at least 0.94 across 20 seeds.
- Gaussian mixture: BIC picks 3 components, and at k = 4 it agrees with K-means on 59% of rows.

| Season type | Share of years | Rain starts | Rain ends | Season rain | Longest dry spell | Heat days | Stressed share |
|---|---|---|---|---|---|---|---|
| Typical | 29% | about 14 May | about 28 Oct | 1,245 mm | 6 days | 26 | 24% |
| Dry spell | 28% | about 15 May | about 2 Nov | 1,275 mm | 11 days | 27 | 30% |
| Short and dry | 24% | about 10 Jun | about 23 Oct | 1,155 mm | 8 days | 19 | 38% |
| Late start | 18% | about 21 Jun | about 28 Oct | 1,188 mm | 6 days | 42 | 23% |

"Stressed" means peak greenness more than 0.5 sd below the LGA's normal.

**No Wet type emerges.** The fourth type is Short and dry: rain starts late, ends early, and the season is about 7% drier than Typical.

**Check against greenness.**

- The four worst greenness years statewide all fall in the two dry types: 2014 is a Dry spell year, and 2007, 2008 and 2017 are Short and dry.
- But the gap between types (38% against 23% stressed) could arise by chance with 25 years (year-block permutation p = 0.38).
- Treat it as consistent with the types, not proof of them.

## A6. Crop stress model

**Target.** The August-September cropland NDVI anomaly of an LGA-year.

**Data.** 525 LGA-years (21 LGAs, 2000 to 2024). Kaduna North and South are excluded, as are 2025 onward (Terra drift).

**Features.**

- 13 weather anomalies, plus soil water holding, infiltration and cropland share.
- NDVI inputs are excluded, because on the card the season is a scenario and its greenness is unknown.

**Validation.** Leave-one-year-out and senatorial-zone folds. No random splits.

### Results: no model beats predicting "normal"

| Model | RMSE, year folds | RMSE, zone folds | Skill vs normal (year / zone) |
|---|---|---|---|
| Climatology (predict the LGA's normal) | 0.980 | 0.980 | 0 / 0 |
| Ridge (baseline) | 1.019 | 1.032 | -4.0% / -5.4% |
| LightGBM (8 leaves, 20 rows per leaf, early stopping) | 1.042 | 1.004 | -6.4% / -2.5% |
| LightGBM tuned by Optuna (40 trials)* | 0.988 | 0.976 | -0.9% / +0.4% |

\* Tuned on the same years it is scored on, so this is an optimistic upper bound. It reached the climatology level by shrinking toward flat predictions (21 trees at a learning rate of 0.011).

### Intervals (should cover 80%)

| Interval | Year folds | Zone folds |
|---|---|---|
| LightGBM quantile (10th to 90th percentile) | 70% | 66% |
| Ridge bootstrap (200 refits with resampled residuals) | 72% | 72% |
| Ridge leave-one-year-out residuals | 80% | 80% |

Only the residual ranges are calibrated, so those are the ranges written out.

### Why it fails

**Where the signal is** (`a6_diagnostics.csv`):

- Every weather-greenness link runs between years:
  - July-August heat r = 0.41;
  - very heavy rain days r = -0.39;
  - July-August rain r = 0.33.
- There are only 25 years.
- Within a year, differences between LGAs carry no signal (|r| ≤ 0.11).
- No single feature has skill out of sample (best: +0.5%).

**What the target measures:**

- The heat link runs the wrong way for crop stress (sunnier Julys read greener). That points to cloud contamination in MODIS composites, not crop response.
- MODIS and VIIRS agree on the target only at r = 0.27, so at LGA scale it is mostly sensor noise.

### Decision (honesty rule)

- **Shipped model:** LightGBM did not beat Ridge, so Ridge ships as the documented baseline. Ridge has no skill either, so `card_source` is `weather_risk_a2_a9`: card stress lines come from dry spells (A2) and water deficit (A9) under the chosen season type, not from A6.
- **Learn:** shows this comparison and the greenness-by-type record as weak evidence.
- **A12:** scores vegetation stress as Low confidence.

**Explanations.** SHAP is shown for both models: TreeExplainer for LightGBM, exact linear SHAP for Ridge. Permutation importance is the baseline. All three rank July-August heat first (Ridge SHAP 22%, LightGBM SHAP 25%, permutation 35%). With no skill behind them, the driver lists describe the fitted model, not crop physiology.

## A9. Crop water balance (FAO-56)

**What it does.** For seven crops in every LGA and year from 1981 to 2025, it tracks daily whether the crop gets the water it needs. That gives the crop's water need, how often the season ran short, and its riskiest growth stage, for all years and for each A5 season type.

**Method.** A daily FAO-56 root-zone bucket.

- **Planting:** each crop is planted on that LGA-year's A2 onset date and runs for its FAO-56 Table 11 stage lengths.
- **Rain and evaporation:** CHIRPS daily rain, and POWER daily Penman-Monteith ET0.
- **Crop coefficients:** Table 12 values, with Kc mid and Kc end adjusted for each season's wind and humidity (Eq. 62 and 65).
- **Root zone:** roots grow from 0.2 m to the Table 22 rainfed depth by the end of development. Available water comes from the iSDA layers through Saxton-Rawls (A8).
- **Stress:** the depletion fraction p is adjusted daily, and Ks follows Eq. 84.
- **Drainage:** rain beyond field capacity drains.
- **Thresholds:** a season "runs short" when actual ET falls more than 10% below the crop's need. Crop parameters and their sources are in `config/crops.yaml`.

**Crops.**

- **Included:** maize, sorghum, millet, soybean, cowpea, groundnut and sesame.
- **Left out, with reasons:**
  - rice, because FAO-56 values assume a flooded paddy;
  - cassava, yam and ginger, because FAO-56 has no rainfed West African stage lengths for them.

### Results

| Crop | Season (days) | Water need (median) | Rain over the season (median) | Seasons short, statewide | Fits the A2 rainy season |
|---|---|---|---|---|---|
| Maize | 125 | 377 mm | 961 mm | 0.3% | 86% |
| Sorghum | 125 | 343 mm | 961 mm | 0.2% | 86% |
| Groundnut | 130 | 414 mm | 993 mm | 5.3% | 83% |
| Sesame | 110 | 317 mm | 848 mm | 0% | 93% |
| Millet | 105 | 280 mm | 801 mm | 0% | 95% |
| Cowpea | 100 | 283 mm | 752 mm | 0% | 98% |
| Soybean | 85 | 289 mm | 608 mm | 0.3% | 100% |

**What this means for the cards.**

- **Total rain is rarely the problem.** Planted at onset, every crop gets two to three times its season's need in rain, and whole-season shortfalls are rare. This holds even with FAO-56's shallow rooting depths: under 2% of seasons for every crop except groundnut (11%).
- **Timing is the risk.** Whether the crop finishes before the rains end is what separates crops, places and season types:
  - **Groundnut in the north:** in Ikara, Makarfi, Kudan and Sabon Gari it runs short in 20 to 29% of years, with the loss concentrated in ripening, and fits the rainy season in only 40 to 62% of years.
  - **Late-start and short-and-dry years:** groundnut runs short in 17% and 13% of them, against 0% in typical years. Maize and sorghum fit the season in only 61 to 66% of these years, against 97 to 100% in typical and dry-spell years.
  - **Short crops:** cowpea and soybean fit in at least 84% of seasons in every LGA, millet in at least 76%.

**Water use row and season-length rule.** The card's water use row should come from `fits_season_share` and `short_share` under the chosen season type, plus the riskiest stage. The PRD season-length rule uses `fits_season_share` directly.

### Checks and baselines

| Check | Result |
|---|---|
| Hargreaves ET0 instead of Penman-Monteith | Same picture: groundnut 4.5% short statewide (5.3% with Penman-Monteith), every other crop under 0.5% |
| Shallow roots (Table 22 lower bound) | Shortfalls stay under 2% except groundnut (11%) |
| SMAP L4 root zone, 2015 to 2025, in-season days (docs/model-evaluation.md) | Weak agreement: Pearson r from 0.04 to 0.21 across LGAs, median 0.11 |

**Why SMAP agrees weakly.** In the rainy season the bucket refills to field capacity after most rains, so its wetness barely varies, while SMAP's 0 to 100 cm layer keeps varying. The check confirms the bucket is not tracking soil moisture day to day. It is a crop-need model, and its outputs are frequencies, not daily soil moisture.

### Limits

- **Runoff:** set to zero, which overstates the rain that reaches roots on heavy-storm days.
- **Subsoil:** iSDA stops at 50 cm, so subsoil water holding is extended below that.
- **Weather and planting:** the bucket uses LGA-average weather, and every crop is planted on the onset date.
- **Missing solar data:** 1981 to 1983 use POWER solar radiation filled with FAO-56 Eq. 50, and these years are flagged in `et0_filled_share`.

### Outputs

| File | Contents |
|---|---|
| `a9/a9_lga_year_crop.csv` | Per crop and LGA-year: plant and harvest dates, need, use, shortfall per stage, stress days, fits season, season type |
| `a9/a9_lga_crop_summary.csv` | Per LGA and crop over 1981 to 2025: medians, share of seasons short, share that fit, riskiest stage, Hargreaves baseline |
| `a9/a9_lga_crop_season_type.csv` | The same per LGA, crop and A5 season type (2000 to 2024) |
| `a9/a9_smap_check.csv`, `a9_summary.json` | SMAP agreement per LGA; run summary |

## A5 and A6 outputs

| File | Contents |
|---|---|
| `a5/a5_lga_year_types.csv` | Type per LGA-year, raw and anomaly features, centroid distance, GMM probabilities |
| `a5/a5_lga_type_share.csv` | Share of years each LGA spent in each type |
| `a5/a5_monthly_profiles.csv` | Monthly rain, greenness, Tmax, heat days and water balance per LGA and type, for the season strip |
| `a5/a5_centroids.csv` | Each type in anomaly units and real values, with its greenness |
| `a5/a5_model_selection.csv`, `a5_year_summary.csv`, `a5_summary.json` | Scores for k = 2 to 6, the statewide type per year, the run summary |
| `a6/a6_metrics.csv`, `a6_interval_coverage.csv`, `a6_cv_predictions.csv` | Model comparison, interval coverage, every held-out prediction |
| `a6/a6_diagnostics.csv` | Between-year, within-year and out-of-sample results per feature |
| `a6/a6_feature_importance.csv`, `a6_lga_drivers.csv` | SHAP and permutation shares; top three drivers per LGA |
| `a6/a6_lga_season_type_stress.csv` | Per LGA and type: expected stress with an 80% range, the observed record, and `card_source` |
| `a6/a6_summary.json` | Run summary |

## A3. Climate indicators per LGA and month

Notebook `notebooks/a3_climate_indicators.ipynb`, formulas `model/indicators.py`. For each LGA and calendar month: the long-term mean of each monthly value, and the share of years in which that month held at least one event day (heat day: Tmax at or above 35 C; frost day: Tmin at or below 2 C; very heavy rain: 50 mm or more; extreme rain: above the local 95th percentile; dry soil: SMAP anomaly at or below -1). Years: 1981 to 2025 (IMERG from 2000, SMAP from 2015).

- Heat stress is a dry-season risk, February to May. In Zaria, March has at least one heat day in every year.
- Extreme rain is concentrated in July to September.
- Frost has never occurred in the record. The card shows it as very low rather than leaving it out.

Output: `a3/a3_lga_month_indicators.csv`.

## A4. Bare fields

Notebook `notebooks/a4_bare_fields.ipynb`, formulas `model/bare_fields.py`. Four ways to flag a bare 16-day cropland NDVI composite, scored on bare-to-green flips per year (fewer is better) and agreement with the November to April dry season (higher is better), 2000 to 2024:

| Method | Flips per year | Dry-season agreement |
|---|---|---|
| Raw, per-LGA threshold | 2.02 | 0.857 |
| Smoothed (Savitzky-Golay), per-LGA threshold | 1.99 | 0.859 |
| Raw, fixed 0.25 | 1.24 | 0.620 |
| Smoothed, fixed 0.25 | 1.08 | 0.620 |

Smoothing barely matters, because NDVI averaged over a whole LGA's cropland is already smooth. The threshold is what matters: the fixed 0.25 from the PRD never fires in the southern LGAs, where trees in the farm mosaic keep dry-season NDVI near 0.30. The fixed threshold flips less only because it often never marks anything bare. The smoothed, per-LGA method is kept.

Outputs: `a4/a4_bare_probability.csv` (share of years each LGA's cropland is bare in each month), `a4/a4_method_scores.csv`.

## A7. Long-term trends

Notebook `notebooks/a7_trends.ipynb`, formulas `model/trends.py`. Mann-Kendall with Sen's slope per LGA and series, 1981 to 2025, against an OLS baseline. The Hamed-Rao autocorrelation correction is used only when it makes the test stricter (`trend_p` is the larger of the two p values), and Benjamini-Hochberg controls false discoveries across all 92 tests (23 LGAs x 4 series).

| Series | Median per decade | LGAs significant after FDR |
|---|---|---|
| Heat days (POWER, Tmax at or above 35 C) | +11.7 days (range 9.2 to 17.5) | 5: Jaba, Jema'a, Kudan, Makarfi, Sanga |
| Rain start day (A2) | +2.9 days later | 0 (later in 20 of 23 LGAs, none distinguishable from chance) |
| April to October rain (CHIRPS) | +2.9 mm | 0 |
| 10+ day dry spells (A2) | 0 | 0 |

Check: POWER Tmax warms +0.50 C per decade against ERA5-Land +0.22 C per decade (medians). The direction of the heat-day trend is solid; its size is probably overstated by POWER, so the card says "more heat days" without a number. For rain start the card says "no clear trend", not a shift.

Outputs: `a7/a7_lga_trends.csv` (with `card_line`), `a7/a7_tmax_power_vs_era5.csv`.

## A10. Erosion risk and infiltration

Notebook `notebooks/a10_land_risk.ipynb`, formulas `model/land_risk.py`. Erosion index: cropland slope (SRTM) x IMERG 95th percentile wet-day rain x Williams K relative to the Kaduna mean, scaled 0 to 1 across LGAs and banded at thirds (High 6, Medium 7, Low 10 LGAs). It ranks LGAs against each other; it is not a soil loss in tonnes.

- The raw product varies only about 1.4 times between the highest and lowest LGA, so the bands overstate real differences.
- Kaduna North (rank 1) and Kaduna South (rank 4) rank high partly because IMERG overstates city rain (`imerg_suspect`). Both are low-cropland LGAs.
- Every LGA's cropland is hydrologic soil group B (moderate infiltration), so infiltration does not separate LGAs.

Output: `a10/a10_lga_land_risk.csv` (with each term's share of the index).

## A12. Confidence per LGA

Notebook `notebooks/a12_confidence.ipynb`, formulas `model/confidence.py`, cut-offs in `config/models.yaml` (`confidence`). Four inputs score 0 to 2: cropland share, share of NDVI composites that were cloud-filled, iSDA relative uncertainty width, and vegetation stress evidence (0 everywhere while A6 has no skill). Total out of 8: High at 6 or more, Medium at 4 or more.

- High: Ikara, Kubau, Lere, Makarfi.
- Low: Chikun, Jaba, Kachia, Kaduna North, Kaduna South.
- Medium: the other 14.
- The soil input scores 2 in every LGA: iSDA's uncertainty is about the same everywhere, so it cannot separate LGAs. Differences come from cropland share and cloud-filled vegetation data, both worst in the wetter, more wooded south and the city LGAs.

Output: `a12/a12_lga_confidence.csv` (scores, level and the weakest input as the reason).

## A6 follow-ups, 14 Nov 2026

**The SMAP-missing indicator was dropped.** Ridge used to get a 0/1 flag for years without SMAP (all years before 2015), so the flag worked as an era marker. Tested before dropping (previous run archived in `data/processed/models/archive/a6_2026-11-14_with_smap_missing/`):

| Target | With flag | Flag alone | Year alone | Without flag |
|---|---|---|---|---|
| Aug to Sep peak NDVI (A6 target) | -0.040 | -0.026 | -0.019 | -0.034 |
| Jun to Jul mean NDVI | +0.210 | +0.203 | +0.189 | +0.010 |
| Jun to Oct mean NDVI | +0.113 | +0.112 | +0.099 | -0.005 |

The flag alone reproduces all of the apparent skill on the early-season targets, and a plain year trend does almost as well, so the skill was trend, not weather. A6 stays without skill (Ridge -0.034 leave-one-year-out after the change).

**Falling early-season greenness: real land change, not sensor drift.** June to July cropland NDVI falls by about 0.03 per decade (2000 to 2024, r = -0.79 with year), in all 23 LGAs. Evidence:

- An independent sensor shows it too: VIIRS (a different satellite) falls 0.068 per decade over 2012 to 2024, against MODIS 0.050 over the same years. Drift in MODIS Terra cannot explain a VIIRS trend.
- It is concentrated in the green-up months. Peak (Aug to Sep) and Oct to Nov change little, and dry-season change is small. A sensor calibration drift would shift every month.
- Rain does not explain it. Controlling for onset date, June to July rain and the cloud-filled share leaves the trend unchanged (-0.032 to -0.035 per decade, p < 0.001).
- It is stronger over cropland than over all land in 20 of 23 LGAs, and steepest where cropland is a smaller share of the LGA (Kauru, Kagarko, Kajuru, Igabi, Birnin Gwari, Chikun). The cropland mask is ESA WorldCover 2021 applied to every year, so land cleared for farming after 2000 counts as cropland throughout: its early-season greenness drops when bush or fallow becomes a freshly planted field. This reading fits but is not proven; a land-cover time series (ESA CCI or Dynamic World) would confirm it.

One data-quality note: the share of cloud-filled June to July composites rises over time (+0.13 per decade), but controlling for it does not remove the trend.

Decision: the early-season greenness trend does not go on any card. It describes land conversion, not crop condition.
