# Tech Stack

All Python code runs on Python 3.12.

## 1. Data acquisition

| Need | Tool |
| --- | --- |
| Satellite data (GPM, MODIS, SMAP, Landsat, SRTM, GRACE-FO) | Google Earth Engine, Python API (earthengine-api, geemap) |
| Climate and agroclimate | NASA POWER API (requests) |
| ECOSTRESS and awkward extracts | NASA AppEEARS |
| Soil | iSDAsoil (via Earth Engine) |
| Study area boundaries | geoBoundaries gbOpen (Kaduna LGAs, GRID3 2022) |
| SMAP L4 soil moisture | earthaccess opens SPL4SMGP granules remotely, h5py reads the Kaduna window |
| Accounts | NASA Earthdata login, Earth Engine noncommercial |

## 2. Data processing and harmonization

| Need | Tool |
| --- | --- |
| Tables | pandas |
| Gridded data | xarray, rioxarray, dask for lazy loading |
| Vectors and boundaries | geopandas, shapely |
| Resampling to a common grid | Earth Engine reduceResolution / reproject, or rioxarray |
| Storage of outputs | Parquet via pyarrow (features), GeoJSON (map layers) |
| Map overlay compression | pngquant or Pillow |

## 3. Science and modeling

| Need | Tool |
| --- | --- |
| Season clustering, baseline vs stronger model | scikit-learn |
| Stronger model | LightGBM |
| Hyperparameter tuning | Optuna |
| Explainability for strategy cards | SHAP |
| NDVI smoothing | SciPy (Savitzky-Golay filter) |
| Long-term water trend | pyMannKendall |
| Sensitivity analysis | SALib, or a simple loop in Python |
| Water balance | Plain Python implementing FAO-56 |
| Crop knowledge base | FAO ECOCROP plus a hand-built YAML or JSON rules file |
| Notebooks (also proof for judges) | Jupyter |
| Notebook charts | matplotlib |

## 4. Backend

| Need | Tool |
| --- | --- |
| API and page rendering | FastAPI, Uvicorn |
| Preview and production hosting | Vercel Python runtime, deployed through the Vercel REST API by `.github/workflows/deploy.yml` |
| Templates | Jinja2 |
| Request and data validation | Pydantic |
| Settings and `.env` | pydantic-settings, python-dotenv, PyYAML for `config/` |
| Hausa and English text | JSON dictionaries passed into Jinja |

## 5. Frontend

| Need | Tool |
| --- | --- |
| Dynamic updates (sliders, cards) | HTMX |
| Small client state (tabs, toggles, language) | Alpine.js |
| Styling | Tailwind CSS via the standalone CLI |
| Map | Leaflet |
| Charts (timeline, backtest) | Chart.js |
| Icons | Heroicons or Lucide (inline SVG) |

## 6. QA and testing

| Need | Tool |
| --- | --- |
| Backend tests | pytest, FastAPI TestClient (needs httpx) |
| End to end and mobile checks | Playwright, pytest-playwright |
| Data checks (no nulls, ranges sane) | pandera or plain pytest asserts |
| Linting and formatting | Ruff |
| Type checking | mypy on `model/` and `api/` |
| Pre-commit checks | pre-commit, running Ruff |
| Dependency pinning | uv, `requirements.in` compiled to `requirements.txt` |
| CI | GitHub Actions, running Ruff, mypy, pytest, Dependency versions, and Tracked files |
| Profiling | py-spy, `time.perf_counter`, `%timeit` |
