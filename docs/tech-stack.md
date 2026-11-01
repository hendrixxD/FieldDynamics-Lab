# Tech Stack

## 1. Data acquisition

| Need | Tool |
| --- | --- |
| Satellite data (GPM, MODIS, SMAP, Landsat, SRTM, GRACE-FO) | Google Earth Engine, Python API (earthengine-api, geemap) |
| Climate and agroclimate | NASA POWER API (requests) |
| ECOSTRESS and awkward extracts | NASA AppEEARS |
| Soil | iSDAsoil (via Earth Engine) |
| Study area boundaries | GADM or geoBoundaries (Kaduna LGAs) |
| Accounts | NASA Earthdata login, Earth Engine noncommercial |

## 2. Data processing and harmonization

| Need | Tool |
| --- | --- |
| Tables | pandas |
| Gridded data | xarray, rioxarray |
| Vectors and boundaries | geopandas, shapely |
| Resampling to a common grid | Earth Engine reduceResolution / reproject, or rioxarray |
| Storage of outputs | Parquet (features), GeoJSON (map layers) |

## 3. Science and modeling

| Need | Tool |
| --- | --- |
| Season clustering, baseline vs stronger model | scikit-learn |
| Stronger model | LightGBM |
| Explainability for strategy cards | SHAP |
| Water balance | Plain Python implementing FAO-56 |
| Crop knowledge base | FAO ECOCROP plus a hand-built YAML or JSON rules file |
| Notebooks (also proof for judges) | Jupyter |

## 4. Backend

| Need | Tool |
| --- | --- |
| API and page rendering | FastAPI, Uvicorn |
| Templates | Jinja2 |
| Request and data validation | Pydantic |
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
| Backend tests | pytest, FastAPI TestClient |
| End to end and mobile checks | Playwright |
| Data checks (no nulls, ranges sane) | pandera or plain pytest asserts |
| Linting and formatting | Ruff |
