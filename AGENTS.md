# AGENTS.md

Instructions for AI agents and contributors working in this repo. This file is the entry point. The linked docs hold the full detail, and if the two ever differ, update this file to match the linked doc.

## Project

FieldDynamics-Lab is our entry for the 2026 NASA Space Apps Challenge, **Field Shift: Adapting Farms with NASA Data** (FieldShift).

We are building a decision-support tool that helps farmers explore crop rotation strategies. It combines NASA Earth observations with local soil information, crop characteristics, and farmer priorities. Our state of interest is **Kaduna, Nigeria**.

The tool supports farmer judgment and local expertise. It never replaces them, and the product must say so.

## Rules that always apply

- **Attribution:** never add a `Co-Authored-By: Claude` trailer, a "Generated with" line, or any other AI attribution to commits, PRs, or repo content.
- **Branches:** never commit directly to `main`, `staging`, `feat`, or `fix`, and never merge a PR. See [Git workflow](#git-workflow).
- **Data:** never use placeholder or made-up data. Leave a value blank until it comes from a real source.
- **Secrets:** keep secrets in `.env` only. Never commit them.
- **Committed files:** never assume `.env` or any other file is not committed. Check and confirm. See [Check before you commit](#check-before-you-commit).
- **Naming:** always call the official challenge statement the **first principle validative measure**.

## How to read the requirements

The [first principle validative measure](docs/first-principle-validative-measure.md) is the official challenge statement. Use it to check that work is not drifting from the core of what is expected.

The [deliverables list](docs/deliverables.md) was built from it and deliberately covers more. That list guides what we build, because something not being mentioned does not mean the judges won't ask about it.

Treat "you may" and "consider" as expected deliverables, not optional extras. Do not downgrade or question a deliverable because it is softly worded or missing from the first principle validative measure.

"SI" is the wording the challenge statement uses. Keep it.

## Documentation map

| Document | Purpose |
| --- | --- |
| [First principle validative measure](docs/first-principle-validative-measure.md) | Official challenge statement, also [as a PDF](docs/first-principle-validative-measure.pdf) |
| [Competition requirements](docs/competition-requirements.md) | Judging criteria, objectives, tiers, and why Kaduna |
| [Deliverables](docs/deliverables.md) | The checklist that guides the build |
| [Tech stack](docs/tech-stack.md) | Tools for each part of the build |
| [Engineering standards](docs/engineering-standards.md) | Rules every line of code must follow to merge |
| [Data contract](docs/data-contract.md) | Draft schema shared between tracks |
| [Data sources](docs/sources.md) | Every dataset, with version and access details |
| [Model evaluation](docs/model-evaluation.md) | How each model is tested |
| [Map architecture](docs/map-architecture.md) | Map layers, formats, and endpoints |
| [Experiment log](docs/experiments.csv) | Every model run and its held-out results |

## Repository structure

```text
FieldDynamics-Lab/
├── data/          # pipeline scripts: extract, harmonize, features
├── model/         # clustering, vulnerability model, water balance, scoring
├── api/           # FastAPI app, routes, schemas
├── web/           # Jinja templates, Alpine snippets, Tailwind source
├── static/        # precomputed PNGs, GeoJSON, built CSS
├── notebooks/     # exploration and evaluation only
├── tests/
├── docs/          # data contract, decisions, judge-facing notes
└── config/        # colour scales, crop rules, settings
```

## Git workflow

Long-lived branches are `main`, `staging`, `feat`, and `fix`.

1. Cut a short-lived branch from `feat` or `fix`, named `feat-<resolution>` or `fix-<resolution>`.
2. Open a PR from it into `feat` or `fix`.
3. Hendrixx reviews it and merges to `staging`.
4. If checks pass, Hendrixx merges to `main`.

Use hyphens, not slashes. Because a branch named `feat` exists, git cannot create `feat/something`.

Keep PRs small enough to review in 10 minutes. The reviewer uses the checklist in section 7 of the [engineering standards](docs/engineering-standards.md).

### Check before you commit

Never assume a file is safe because it is listed in `.gitignore`. A file that was already tracked stays tracked, and an ignore rule can be wrong. Run these checks and read the output every time:

```bash
# Files about to be committed
git diff --cached --name-only

# Whether a file is tracked now
git ls-files -- .env

# Whether a file was ever committed, on any branch
git log --all --oneline -- .env

# Whether a file is really ignored, and by which rule
git check-ignore -v .env
```

Run them for `.env` and for any file that could hold secrets or raw data. Only say a file is not committed after these checks confirm it.

If a secret was ever committed, stop and tell Hendrixx. Treat it as exposed and replace it. Deleting the file does not remove it from git history.

## Working on code

Read the [engineering standards](docs/engineering-standards.md) before writing or reviewing code. If code breaks a rule, it does not merge.

### AI-generated code

The person who pastes AI code owns it fully. Before committing it:

1. Read every line. If you cannot explain a line, delete or rewrite it.
2. Run it with real inputs.
3. Check every import and function against the official docs, not memory.
4. Check it suits the current library versions.
5. Write or run a test for any function containing logic.

Write one small function at a time, with its test, using the standards, the data contract, and the exact function signature.

### Tooling

Python 3.11. Ruff for lint and format, with line length 100. mypy on `model/` and `api/`. pre-commit runs both before every commit, along with the tracked files check.

Set up once after cloning:

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements-dev.txt
.venv/bin/pre-commit install
```

CI on GitHub Actions runs on every PR and on pushes to `main`, `staging`, `feat`, and `fix`. Its four checks are Ruff, mypy, pytest, and Tracked files, which fails if env files or raw data are tracked or anywhere in history. Do not merge a PR with a red check.

### Code rules

- Names carry units where the value has one: `rain_mm`, `temp_c`, `et0_mm_day`, `slope_deg`.
- One function, one job. Aim under 30 lines; over 40 needs a reason in review.
- Type hints on every parameter and return value.
- Science functions are pure, and their docstrings give units and the formula source.
- Catch specific exceptions only. Fail loudly on bad data with a clear `ValueError`.
- Validate at the boundary with Pydantic, then trust the data inside.
- Constants, thresholds, crop coefficients, and weights live in `config/`, each with a comment citing its source.
- Comments explain why, never what. Delete commented-out code.

### Reject on sight

- Bare `except:` or swallowed errors
- Copy-pasted blocks that should be one function with a parameter
- Hardcoded paths, credentials, thresholds, or coefficients in logic
- Python loops over DataFrame rows or array cells, including `df.apply(axis=1)`
- Placeholder or fake data
- Over-engineering, such as factories or plugin systems for something used once
- Unit mistakes: Kelvin vs Celsius, mm/day vs mm/month, degrees vs radians
- Random train/test splits on spatial or time-series data

### Performance

- Measure before optimizing.
- Do clipping, reprojection, resampling, and aggregation in Earth Engine. Download only the reduced result for Kaduna.
- Use `float32`, categorical dtypes, Parquet, and lazy raster loading.
- Load models and data once at startup.
- The live scoring endpoint must respond in under 200 ms.
- First page load stays under 1 MB, excluding map tiles.

## Data pipeline

- `python -m data.build` produces the feature table.
- `python -m model.build` produces model outputs, map PNGs, and GeoJSON.
- If a result cannot be rebuilt from scratch, it does not exist.
- Raw pulls go to `data/raw/`, which is git-ignored, and are never edited. Transformations write new files to `data/processed/`.
- Log every dataset in `docs/sources.md`.
- Store everything in EPSG:4326. Do area and distance maths in UTM zone 32N, then convert back.
- Validate data on load: soil moisture 0 to 0.6, NDVI -1 to 1, rainfall not negative, and no unexpected nulls.
- Flag gap-filled values so they lower confidence on the strategy cards.
- A changed feature table gets a new name, such as `features_v2.parquet`, and the data contract changes in the same PR.
- Notebooks explore and evaluate. Anything the app uses is a tested `.py` module.

## Modeling

The simplest method that is accurate and explainable wins. Compare every model against a simple baseline and report the result honestly. Section 4 of the [engineering standards](docs/engineering-standards.md) has the full table of methods, and [model evaluation](docs/model-evaluation.md) has the metrics.

| Scenario | Use | Baseline |
| --- | --- | --- |
| Rain onset and cessation | Agronomic rule based on rainfall thresholds | None |
| Dry spell and heat-day counts | Threshold counting on daily data | None |
| Season types | K-means, with GMM as a sanity check | None |
| Bare-field and stress periods | Savitzky-Golay smoothed NDVI, then thresholds | Raw NDVI thresholds |
| Vulnerability | LightGBM regressor | Ridge regression |
| Uncertainty | LightGBM quantile regression, 10th and 90th percentiles | Bootstrap intervals |
| Explanations | SHAP TreeExplainer | Permutation importance |
| Tuning | Optuna, 30 to 50 trials, on grouped CV | Library defaults |
| Crop water demand | FAO-56 Penman-Monteith ET0 times Kc | Hargreaves ET0 if inputs are missing |
| Soil water balance | Daily FAO-56 bucket model | None |
| Long-term water trend | Mann-Kendall with Sen's slope on GRACE-FO | Linear regression slope |
| Rotation ranking | Weighted multi-criteria scoring | Equal weights |
| Ranking robustness | Perturb weights by plus or minus 20 percent | None |

For every model:

- Validate with leave-one-year-out plus spatial block GroupKFold, never random splits.
- Fix all random seeds.
- Standardize features for k-means and Ridge.
- Log every experiment in `docs/experiments.csv`.
- Report test scores from the held-out year and region only.

When resampling, use bilinear for continuous data, nearest neighbour for categories, and mean aggregation when coarsening.

## API and frontend

- FastAPI routes are thin: validate, call a function in `model/`, and return a template or JSON.
- Use Pydantic models for every request and response body.
- HTML partial routes go under `/partials/` and JSON routes under `/api/`. Never mix them.
- The map container is never inside an HTMX swap target.
- One Jinja partial per swappable component, named with a leading underscore.
- Alpine holds UI state only.
- Debounce slider requests.
- Use Tailwind utility classes only, with no inline styles.
- Every interactive element works by tap on a phone, has a visible label, and meets basic contrast.

### Map

- The state view uses PNG overlays at 5 km. The LGA view adds a clickable GeoJSON grid at 1 km.
- PNGs and GeoJSON are precomputed. Only `/api/point` and the scoring run live.
- The colour scale lives only in `config/layers.yaml`, used by both the PNG script and the legend endpoint.
- Label each layer with its native resolution. SMAP is about 9 km, so at 1 km it is resampled, not new detail.

## Testing

- Every science function has a pytest test with known inputs and outputs. Use FAO-56 published worked examples for ET0.
- Every endpoint has a TestClient test for status code and response shape.
- One Playwright test covers the core flow: open map, select LGA, move a slider, cards update.
- Data validation tests run on the processed feature table.
- For bugs, write the test first, then the fix.

## Submission requirements

- Demo: a video of up to 30 seconds or a slide deck of up to 7 slides. Going over disqualifies us from global judging.
- All links public and working
- Clear statement of which NASA and partner datasets were used
- Link to code in a public repository
- Every required field on the project page completed
- Terms and submission requirements checkbox ticked

## Open items

- The repo is private and must be made public before submission.
- Branch protection is unavailable while the repo is private on the free plan, so GitHub cannot block a red merge. Enable it when the repo goes public, requiring the four CI checks.
- The data contract is a draft. Confirm it against real data at first pull.
- Choose GADM or geoBoundaries for LGA boundaries. The tech stack lists both, while the map architecture uses geoBoundaries.
- Decide whether `data/processed/` stays in git if the files get large.
- Check the Resources tab of the challenge page, which the first principle validative measure does not cover.
