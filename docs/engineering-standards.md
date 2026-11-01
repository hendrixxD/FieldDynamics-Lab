# FieldDynamics Lab Engineering Standards

Nov 2, 2026 · Hendrixx

Every line of code in this repo follows these rules, whether a person or an AI wrote it. If code breaks a rule, it does not merge.

## 1. AI-generated code

AI assistants are allowed and encouraged. The person who pastes the code owns it, fully, as if they typed every line. "The AI wrote it" is never an answer in review.

Before you commit AI output:

1. Read every line. If you cannot explain a line in review, delete it or rewrite it.
2. Run it. Never commit code you have not executed at least once, with real inputs.
3. Check every import and function exists. AI invents library functions and arguments. Verify against the official docs (scikit-learn, xarray, FastAPI, Earth Engine), not memory.
4. Check the version. AI often writes code for old library versions (deprecated pandas methods, old Pydantic v1 syntax, old Earth Engine calls). We use current versions; fix anything outdated.
5. Write or run a test for any function containing logic, especially science and maths.

Patterns AI gets wrong; reject on sight:

- Bare `except:` or `except Exception: pass` that hides errors.
- Functions over 40 lines doing several jobs at once.
- Copy-pasted blocks with tiny differences instead of one function with a parameter.
- Hardcoded paths, credentials, thresholds or coefficients in the middle of logic.
- Python for loops over DataFrame rows or array cells (see Performance).
- Comments that restate the code (`# increment i`) instead of explaining why.
- Placeholder or fake data left in (`# TODO: replace with real data`, made-up sample values).
- Over-engineering: abstract base classes, factories and plugin systems for something used once.
- Silent unit mistakes: Kelvin vs Celsius, mm/day vs mm/month, degrees vs radians in solar calculations.
- Random train/test splits on spatial or time-series data.

How to prompt for better code: give the AI our standards file, the data contract, and the exact function signature you want. Ask for one small function at a time with its test, not a whole module.

## 2. Code standards

Tooling (enforced in CI, not optional): Python 3.11, Ruff for lint and format, mypy on `model/` and `api/`, pre-commit hooks running both before every commit.

### Style and naming

- PEP 8, enforced by Ruff. Line length 100.
- `snake_case` for functions and variables, `PascalCase` for classes, `UPPER_CASE` for constants.
- Names carry units when the value has one: `rain_mm`, `temp_c`, `et0_mm_day`, `slope_deg`. This single rule prevents most science bugs.
- No single-letter names except loop indexes and standard maths (x, y in a formula with a cited source).

### Functions

- One function, one job. Aim under 30 lines; over 40 needs a reason in review.
- Type hints on every parameter and return value.
- Science functions are pure: inputs in, value out, no file reads, no globals, no printing.
- Docstring on every public function: what it does, units of inputs and outputs, and the source for any formula (e.g. FAO-56, Eq. 6).

### Errors

- Catch specific exceptions only, and only where you can do something useful.
- Fail loudly on bad data: raise a clear `ValueError` with the field and value, never return a silent default.
- Validate at the boundary (API input, file load) with Pydantic, then trust the data inside.

### Configuration

- All constants, thresholds, crop coefficients and weights live in `config/`, as YAML or Pydantic settings, with a comment citing where each value came from.
- Secrets only in `.env`, read through one Settings class.

### Comments

- Explain why, never what. A good comment: `# SMAP has gaps in heavy rain; fill with 7-day median.`
- Delete commented-out code. Git remembers it.

## 3. Performance and optimization

Rule zero: measure before you optimize. Use `time.perf_counter`, `%timeit` in notebooks, or py-spy for a running process. Never optimize on a hunch.

### Push heavy work to Earth Engine

- Do clipping, reprojection, resampling and temporal aggregation server-side in Earth Engine. Download only the reduced result for Kaduna, never raw global tiles.
- Aggregate in Earth Engine with `reduceRegion` / `reduceRegions` before export.
- Batch exports with `Export.image.toDrive` or `Export.table.toDrive` for large pulls; use `getInfo()` only for small results.

### Vectorize, never loop

- No Python for loops over pixels or DataFrame rows. Use NumPy, pandas and xarray operations on whole arrays.
- `df.apply(axis=1)` is a loop in disguise; replace it with vectorized column maths.
- For rolling and seasonal stats use xarray `.rolling()`, `.resample()` and `.groupby('time.month')`.
- If a loop truly cannot be avoided in the water balance (it is sequential by day), vectorize across pixels and loop only over days.

### Memory

- Store features as Parquet, not CSV: smaller and typed.
- Use `float32` for rasters and features unless precision demands `float64`.
- Use categorical dtype for LGA names, crop names and cluster labels.
- Open large rasters lazily with rioxarray / xarray and `chunks=`; load only what you compute on.

### Model training

- LightGBM over random forest for tabular data: faster and usually more accurate.
- Set `n_jobs=-1` to use all cores.
- Use early stopping on a validation fold instead of guessing the number of trees.
- Cache expensive intermediate results to disk with clear names; never recompute them in the app.

### API speed

- Load models, feature tables and lookup data once at startup into memory.
- Precompute everything that does not depend on user input (map PNGs, GeoJSON, season profiles).
- The live scoring endpoint must respond in under 200 ms. If it does not, precompute more.
- Gzip responses (FastAPI `GZipMiddleware`) and set cache headers on static files.

### Frontend weight

- Target total page load under 1 MB on first visit, excluding map tiles.
- Tailwind built and minified. No full JS frameworks beyond HTMX and Alpine.
- Compress PNG overlays (pngquant or Pillow `optimize=True`).

## 4. Algorithm choices per scenario

One principle governs every choice: the simplest method that is accurate and explainable wins. Judges reward models they can understand and that hold up to scrutiny. Every model is compared against a simple baseline, and the baseline result is reported honestly.

| Scenario | Use | Baseline to beat | Avoid | Why |
| --- | --- | --- | --- | --- |
| Rain onset and cessation dates | Agronomic rule: first date after 1 April with at least 20 mm over 3 days and no 10-day dry spell in the next 30 days | None needed; it is the standard definition | ML for this | Rule-based onset is the accepted West African method; cite it |
| Dry spell and heat-day counts | Threshold counting on daily data, vectorized in xarray | None | Smoothing that hides extremes | Simple, exact, explainable |
| Season type clustering | K-means on standardized seasonal features, k chosen by silhouette score | None (unsupervised) | DBSCAN, deep clustering | Few features, clear centroids you can name (late onset, mid-season drought) |
| Cluster sanity check | Gaussian Mixture Model as a second opinion | K-means result | Trusting one method blindly | GMM gives soft membership, useful for uncertainty |
| Bare-field and crop stress periods | NDVI time series smoothed with Savitzky-Golay, then thresholds on the smoothed curve | Raw NDVI thresholds | Black-box change detection | Standard in remote sensing; handles cloud noise |
| Vulnerability model (predict stress or peak NDVI) | LightGBM regressor | Ridge regression | Deep neural networks, random splits | Best performance on small tabular data; Ridge proves the gain is real |
| Uncertainty on predictions | LightGBM quantile regression (10th and 90th percentiles) | Bootstrap intervals | Point predictions with no interval | The brief explicitly asks how the model handles uncertainty |
| Model explanations | SHAP TreeExplainer | Permutation importance | Built-in split-count importance | SHAP gives per-prediction reasons for each strategy card |
| Validation splits | Leave-one-year-out plus spatial block GroupKFold | None | Random train/test split | Random splits leak neighbours and seasons; scores become meaningless |
| Hyperparameter tuning | Optuna, 30 to 50 trials, on the grouped CV | Library defaults | Exhaustive grid search | Faster and better than grid search in limited time |
| Crop water demand | FAO-56 Penman-Monteith ET0 times crop coefficient Kc | Hargreaves ET0 if inputs are missing | Inventing a learned ET model | Globally accepted standard; judges recognize it |
| Soil water balance | Daily FAO-56 bucket model, vectorized across pixels | None | Full crop simulators (DSSAT, APSIM) in 48 hours | Credible, fast, explainable |
| Long-term water trend | Mann-Kendall test with Sen's slope on GRACE-FO | Linear regression slope | Plain trend lines with no significance test | Robust to outliers and non-normal data |
| Rotation ranking | Weighted multi-criteria scoring, weights set by farmer priorities | Equal weights | A trained model with no rotation labels | No ground-truth rotation outcomes exist, so rules beat fake learning |
| Ranking robustness | Sensitivity analysis: perturb weights by plus or minus 20 percent and check rank stability | None | Reporting one ranking as final | Shows the recommendation is not fragile |
| Resampling to the common grid | Bilinear for continuous data (temperature, soil moisture); nearest neighbour for categories (soil class, land cover); mean aggregation when coarsening | None | Bilinear on categorical data | Wrong resampling silently corrupts values |

Rules that apply to every model:

- Fix all random seeds.
- Standardize features for k-means and Ridge; tree models do not need it.
- Log every experiment (model, features, split, metrics) in `docs/experiments.csv`.
- Report test scores from the held-out year and region only, never training scores.

## 5. Data pipeline and reproducibility

- One command rebuilds everything. `python -m data.build` produces the feature table; `python -m model.build` produces model outputs, map PNGs and GeoJSON. If a result cannot be rebuilt from scratch, it does not exist.
- Raw data is never edited. Raw pulls go to `data/raw/` (git-ignored); every transformation writes a new file to `data/processed/`.
- Every dataset is logged in `docs/sources.md`: name, product version, resolution, date range, access date, and link.
- One coordinate system. Store everything in EPSG:4326 for the app; do area and distance maths in a projected CRS (UTM zone 32N for Kaduna), then convert back.
- Validate data on load with pandera or explicit checks: no unexpected nulls, values in physical ranges (soil moisture between 0 and 0.6, NDVI between -1 and 1, rainfall not negative).
- Handle gaps explicitly. Document how cloud gaps in NDVI and missing SMAP days are filled, and flag filled values so they lower confidence on the strategy cards.
- Schema changes are versioned. A changed feature table gets a new name (`features_v2.parquet`) and the data contract is updated in the same pull request.
- No notebook in the production path. Notebooks explore and evaluate; anything the app uses is a tested `.py` module.

## 6. API, frontend and testing

### FastAPI

- Routes are thin: validate, call a function in `model/`, return a template or JSON. No business logic in routes.
- Pydantic models for every request and response body.
- HTML partial routes under `/partials/`, JSON routes under `/api/`. Never mix them.
- Load data and models once in the app lifespan handler, not per request.
- Return readable errors with correct status codes (400 for bad input, 404 for unknown LGA).

### HTMX, Alpine and Tailwind

- The map container is never inside an HTMX swap target.
- One Jinja partial per swappable component, named with a leading underscore (`_strategy_card.html`).
- Alpine holds UI state only. Logic longer than a few lines moves to the server.
- Debounce slider requests (`hx-trigger="input changed delay:300ms"`) so dragging does not flood the API.
- Tailwind utility classes only; no inline styles; build with the CLI so unused classes are stripped.
- Every interactive element works by tap on a phone, has a visible label, and meets basic contrast.

### Testing

- Every science function has a pytest test with known inputs and outputs. Use FAO-56 published worked examples for ET0.
- Every endpoint has a TestClient test for status code and response shape.
- One Playwright end-to-end test of the core flow: open map, select LGA, move a slider, cards update.
- Data validation tests run on the processed feature table.
- CI (GitHub Actions) runs Ruff, mypy and pytest on every pull request. A red check blocks the merge.
- Bugs found get a test first, then the fix.

## 7. Pull request review checklist

The reviewer ticks every box before approving. One unticked box means changes requested.

- [ ] CI is green: Ruff, mypy, pytest all pass
- [ ] The author can explain every line, including AI-written ones
- [ ] Every imported function and argument exists in the library version we use
- [ ] No loops over rows or pixels where vectorized code would work
- [ ] Variable names carry units where values have them
- [ ] No hardcoded constants, paths or secrets; values live in `config/` or `.env`
- [ ] No bare except or swallowed errors
- [ ] New logic has a test; science functions cite their formula source
- [ ] Validation uses grouped splits by year or region, never random
- [ ] Data contract and `docs/sources.md` updated if data or schema changed
- [ ] Map container is outside every HTMX swap target
- [ ] No placeholder data, debug prints or commented-out code
- [ ] Pull request is small enough to review in 10 minutes
