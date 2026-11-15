"""FastAPI application. Routes stay thin: validate, call model/, return a template or JSON."""

from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from api.schemas import (
    LayerLegend,
    LegendListResponse,
    LgaListResponse,
    MapTrendsResponse,
    PointQueryResponse,
    ScoreResponse,
)
from model.engine_data import EngineData, load_bundle
from model.rotation_engine import DIMENSIONS, score_request
from model.rotation_engine import Request as ScoreRequest
from model.view_data import (
    DIMENSION_LABELS,
    PRIORITY_META,
    all_legends_view,
    crop_water_view,
    get_i18n,
    layer_legend_view,
    lga_catalog,
    lga_grid_geojson_view,
    map_trends_summary,
    parse_advisor_query,
    point_query_view,
    radio_script_view,
    season_view,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = ROOT_DIR / "web"
STATIC_DIR = ROOT_DIR / "static"

CROP_CHOICES = (
    ("maize", "Maize"),
    ("soybean", "Soybean"),
    ("sorghum", "Sorghum"),
    ("cowpea", "Cowpea"),
    ("groundnut", "Groundnut"),
    ("millet", "Millet"),
    ("sesame", "Sesame"),
    ("rice", "Rice"),
    ("cassava", "Cassava"),
    ("tomato", "Tomato"),
)

SCREENS_MANIFEST = (
    ("landing-page", "Public Project Landing Page", "/", "Portal", "Overview & Judges"),
    ("00-start", "F0. Start & Session Resume", "/start", "Step 0", "Journey Step 0"),
    ("01-farm", "F1. Farm Situation Input", "/farm", "Step 1", "Journey Step 1"),
    ("02-priorities", "F2. Farmer Priorities", "/priorities", "Step 2", "Journey Step 2"),
    ("03-season", "F3. 12-Month Season Strip", "/season", "Step 3", "Journey Step 3"),
    (
        "03a-crop-check",
        "F3d. Crop Water Check (FAO-56)",
        "/season/crop-check",
        "Diag",
        "Water Balance",
    ),
    ("03b-patterns", "F3e. NASA 9-Pattern Diagnostic", "/season/patterns", "Diag", "9 Patterns"),
    (
        "03c-term-explainer",
        "F3f. Tap-For-Meaning Explainer",
        "/season?explain=soil_moisture_anomaly",
        "Diag",
        "Term Sheet",
    ),
    ("04-compare", "F4. Compare Rotation Cards", "/compare", "Step 4", "Journey Step 4"),
    (
        "04a-card-detail",
        "F5. Scientific Dossier (Option A)",
        "/card/a",
        "Step 4",
        "Option A Dossier",
    ),
    ("04b-decide", "F6. Farmer Decision Selection", "/decide", "Step 5", "Journey Step 5"),
    (
        "04c-low-confidence",
        "F5b. Uncertainty Dossier (Option B)",
        "/card/b",
        "Step 4",
        "Uncertainty Check",
    ),
    ("05-share", "F7. Omnichannel Share & Handoff", "/share", "Step 6", "Journey Step 6"),
    ("05a-print-card", "P4. Physical A5 Extension Card", "/share/print", "Print", "A5 Deliverable"),
    (
        "05b-radio-script",
        "P7. Radio Broadcast Studio",
        "/share/radio",
        "Broadcast",
        "Alheri FM 98.7",
    ),
    ("06-learn", "F7. Science & Data Learn Portal", "/learn", "Learn", "Methodology"),
    ("07-trend-map", "F8. Kaduna 23-LGA Trend Map", "/map", "Map", "Geospatial"),
    (
        "08-history",
        "P5. CBA Field Logs & Offline Archive",
        "/history",
        "Records",
        "Field Workstation",
    ),
)

app = FastAPI(title="FieldDynamics-Lab")
app.add_middleware(GZipMiddleware, minimum_size=500)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class HealthResponse(BaseModel):
    """Body of the health check."""

    status: str


@lru_cache(maxsize=1)
def engine_data() -> EngineData:
    """Read the engine bundle once per process (about 0.1 s); every request reuses it."""
    return load_bundle()


@lru_cache(maxsize=1)
def cached_lgas() -> list[dict[str, Any]]:
    """Precompute the 23-LGA enriched catalog once per process."""
    return lga_catalog(engine_data())


@lru_cache(maxsize=1)
def cached_map_trends() -> dict[str, dict[str, Any]]:
    """Precompute the 23-LGA choropleth trend summary once per process."""
    return map_trends_summary(engine_data())


def build_query_string(req: ScoreRequest) -> str:
    """Serialize `ScoreRequest` into clean URL query parameters for cross-screen links."""
    s1 = ",".join(req.past_seasons[-1])
    s2 = ",".join(req.past_seasons[0]) if len(req.past_seasons) > 1 else ""
    params: dict[str, str] = {
        "lga": req.lga,
        "water": req.water,
        "equipment": req.equipment,
        "s1": s1,
        "s2": s2,
        "priorities": ",".join(req.priorities),
        "sell": req.sell,
        "season_type": req.season_type,
    }
    if req.soil:
        params["soil"] = req.soil
    return urlencode(params)


def page_context(request: Request, active_step: str, **extra: Any) -> dict[str, Any]:
    """Build standard template context shared across every screen."""
    data = engine_data()
    req = parse_advisor_query(dict(request.query_params), data)
    lgas = cached_lgas()
    lga_lookup = {item["lga"]: item for item in lgas}
    lang = request.query_params.get("lang", "en")
    t = get_i18n(lang)
    return {
        "request": request,
        "active_step": active_step,
        "advisor_req": req,
        "query_string": build_query_string(req),
        "lgas": lgas,
        "selected_lga_info": lga_lookup.get(req.lga, lgas[-1]),
        "priority_meta": PRIORITY_META,
        "dimension_labels": DIMENSION_LABELS,
        "dimensions_order": DIMENSIONS,
        "crop_choices": CROP_CHOICES,
        "lang": lang,
        "t": t,
        **extra,
    }


def select_card(cards: list[dict[str, Any]], slot: str) -> tuple[str, dict[str, Any]]:
    """Pick `(normalized_slot, card_dict)` from `cards` for `current`, `a` or `b`."""
    mapping = {"current": 0, "a": 1, "b": 2}
    norm = slot.lower() if slot.lower() in mapping else "a"
    idx = min(mapping[norm], len(cards) - 1)
    return norm, cards[idx]


# --------------------------------------------------------------------------- #
# JSON API routes
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> HealthResponse:
    """Report that the app started, so a preview can be confirmed as running."""
    return HealthResponse(status="ok")


@app.get("/api/lgas")
def api_lgas() -> LgaListResponse:
    """Return all 23 Kaduna LGAs with zone, cropland share, soil default, and onset dates."""
    return LgaListResponse.model_validate({"lgas": cached_lgas()})


@app.get("/api/lga/{lga}/season")
def api_lga_season(lga: str, season_type: str = "Typical") -> dict[str, Any]:
    """Return the 12-month season strip, patterns, and trends for one LGA."""
    data = engine_data()
    if lga not in data.zone_of:
        raise HTTPException(status_code=404, detail=f"Unknown LGA: {lga}")
    return season_view(lga, season_type, data)


@app.get("/api/lga/{lga}/crop-check")
def api_crop_check(lga: str, crop: str = "maize", season_type: str = "Typical") -> dict[str, Any]:
    """Return FAO-56 stage water demand vs seasonal rainfall for one LGA and crop."""
    data = engine_data()
    if lga not in data.zone_of:
        raise HTTPException(status_code=404, detail=f"Unknown LGA: {lga}")
    return crop_water_view(lga, crop.lower(), season_type, data)


@app.get("/api/map/trends")
def api_map_trends() -> MapTrendsResponse:
    """Return precomputed trend and risk summary for all 23 Kaduna LGAs."""
    return MapTrendsResponse.model_validate({"trends": cached_map_trends()})


@app.post("/api/score")
def score(body: ScoreRequest) -> ScoreResponse:
    """Score the farmer's current rotation and the two best options for their LGA."""
    data = engine_data()
    if body.lga not in data.zone_of:
        raise HTTPException(status_code=422, detail=f"Unknown LGA: {body.lga}")
    if body.season_type not in data.season_types:
        raise HTTPException(status_code=422, detail=f"Unknown season type: {body.season_type}")
    return ScoreResponse.model_validate(score_request(body, data))


@app.get("/api/point")
def api_point(lat: float, lon: float) -> PointQueryResponse:
    """Resolve a GPS coordinate to its Kaduna LGA, soil profile, and NASA layer indicators."""
    data = engine_data()
    try:
        result = point_query_view(lat, lon, data)
    except ValueError as err:
        raise HTTPException(status_code=422, detail=str(err)) from err
    return PointQueryResponse.model_validate(result)


@app.get("/api/legend")
def api_all_legends() -> LegendListResponse:
    """Return legend scales and metadata for all 4 map layers (docs/map-architecture.md)."""
    return LegendListResponse.model_validate({"layers": all_legends_view()})


@app.get("/api/legend/{layer}")
def api_layer_legend(layer: str) -> LayerLegend:
    """Return color stops, units, and native resolution for one map layer."""
    try:
        result = layer_legend_view(layer.lower())
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    return LayerLegend.model_validate(result)


@app.get("/api/layers/{lga}/grid.geojson")
def api_lga_grid(lga: str) -> dict[str, Any]:
    """Return 1 km clickable GeoJSON grid cells with layer properties for one LGA."""
    data = engine_data()
    if lga not in data.zone_of:
        raise HTTPException(status_code=404, detail=f"Unknown LGA: {lga}")
    try:
        return lga_grid_geojson_view(lga, data)
    except ValueError as err:
        raise HTTPException(status_code=404, detail=str(err)) from err


# --------------------------------------------------------------------------- #
# HTML Partial routes (HTMX swaps)
# --------------------------------------------------------------------------- #
@app.post("/partials/cards", response_class=HTMLResponse)
async def partial_cards(request: Request) -> HTMLResponse:
    """Render the 3 comparison cards fragment for HTMX live updates."""
    data = engine_data()
    is_json = "application/json" in request.headers.get("content-type", "")
    body_json = await request.json() if is_json else {}
    params = {**dict(request.query_params), **{k: str(v) for k, v in body_json.items()}}
    req = parse_advisor_query(params, data)
    scored = score_request(req, data)
    ctx = page_context(request, "compare", scored=scored)
    return templates.TemplateResponse(request, "_cards_grid.html", ctx)


@app.get("/partials/explain/{term}", response_class=HTMLResponse)
def partial_explain(request: Request, term: str, lga: str = "Zaria") -> HTMLResponse:
    """Render the plain-language Tap-for-Meaning bottom sheet / modal fragment."""
    data = engine_data()
    valid_lga = lga if lga in data.zone_of else "Zaria"
    sv = season_view(valid_lga, "Typical", data)
    entry = sv["terms"].get(term)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown term: {term}")
    ctx = page_context(request, "season", term_key=term, term=entry, sv=sv)
    return templates.TemplateResponse(request, "_term_sheet.html", ctx)


# --------------------------------------------------------------------------- #
# Full HTML Screen routes (18 screens + live walkthrough)
# --------------------------------------------------------------------------- #
@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Serve the public project landing page with live Zaria rotation benchmark."""
    data = engine_data()
    ctx = page_context(request, "landing")
    req: ScoreRequest = ctx["advisor_req"]
    benchmark = score_request(req, data)
    sv = season_view(req.lga, req.season_type, data)
    ctx.update({"benchmark": benchmark, "sv": sv})
    return templates.TemplateResponse(request, "index.html", ctx)


@app.get("/start", response_class=HTMLResponse)
def start_screen(request: Request) -> HTMLResponse:
    """F0. Advisor Start & Offline Session Resume."""
    data = engine_data()
    ctx = page_context(request, "start")
    ctx["sv"] = season_view(ctx["advisor_req"].lga, ctx["advisor_req"].season_type, data)
    return templates.TemplateResponse(request, "start.html", ctx)


@app.get("/farm", response_class=HTMLResponse)
def farm_screen(request: Request) -> HTMLResponse:
    """F1. Farm Profile & Past 2 Seasons Input."""
    return templates.TemplateResponse(request, "farm.html", page_context(request, "farm"))


@app.get("/priorities", response_class=HTMLResponse)
def priorities_screen(request: Request) -> HTMLResponse:
    """F2. Farmer Agronomic Priorities & Market Destination."""
    data = engine_data()
    ctx = page_context(request, "priorities")
    ctx["scored"] = score_request(ctx["advisor_req"], data)
    return templates.TemplateResponse(request, "priorities.html", ctx)


@app.get("/season", response_class=HTMLResponse)
def season_screen(request: Request) -> HTMLResponse:
    """F3 & F3f. 12-Month LGA Season Strip, Scenario Simulator & Term Explainer."""
    data = engine_data()
    ctx = page_context(request, "season")
    sv = season_view(ctx["advisor_req"].lga, ctx["advisor_req"].season_type, data)
    explain_key = request.query_params.get("explain", "")
    ctx.update({"sv": sv, "explain_key": explain_key, "explain_term": sv["terms"].get(explain_key)})
    return templates.TemplateResponse(request, "season.html", ctx)


@app.get("/season/crop-check", response_class=HTMLResponse)
def crop_check_screen(request: Request, crop: str | None = None) -> HTMLResponse:
    """F3d. FAO-56 Crop Water Balance Overlay."""
    data = engine_data()
    ctx = page_context(request, "season")
    req: ScoreRequest = ctx["advisor_req"]
    chosen_crop = (crop or req.past_seasons[-1][0]).lower()
    ctx["cw"] = crop_water_view(req.lga, chosen_crop, req.season_type, data)
    return templates.TemplateResponse(request, "crop_check.html", ctx)


@app.get("/season/patterns", response_class=HTMLResponse)
def patterns_screen(request: Request) -> HTMLResponse:
    """F3e. All 9 NASA Challenge Climate Patterns Diagnostic Grid."""
    data = engine_data()
    ctx = page_context(request, "season")
    ctx["sv"] = season_view(ctx["advisor_req"].lga, ctx["advisor_req"].season_type, data)
    return templates.TemplateResponse(request, "patterns.html", ctx)


@app.get("/compare", response_class=HTMLResponse)
def compare_screen(request: Request) -> HTMLResponse:
    """F4. 3-Card Rotation Comparison across 7 Agronomic Dimensions."""
    data = engine_data()
    ctx = page_context(request, "compare")
    req: ScoreRequest = ctx["advisor_req"]
    ctx["scored"] = score_request(req, data)
    ctx["sv"] = season_view(req.lga, req.season_type, data)
    return templates.TemplateResponse(request, "compare.html", ctx)


@app.get("/card/{slot}", response_class=HTMLResponse)
def card_detail_screen(request: Request, slot: str) -> HTMLResponse:
    """F5 & F5b. Scientific Evidence Dossier & Low-Confidence Uncertainty Handling."""
    data = engine_data()
    ctx = page_context(request, "compare")
    req: ScoreRequest = ctx["advisor_req"]
    scored = score_request(req, data)
    norm_slot, card = select_card(scored["cards"], slot)
    sv = season_view(req.lga, req.season_type, data)
    ctx.update({"scored": scored, "slot": norm_slot, "card": card, "sv": sv})
    return templates.TemplateResponse(request, "card_detail.html", ctx)


@app.get("/decide", response_class=HTMLResponse)
def decide_screen(request: Request, pick: str = "a") -> HTMLResponse:
    """F6. Farmer Decision Selection & Deferral Path."""
    data = engine_data()
    ctx = page_context(request, "decide")
    req: ScoreRequest = ctx["advisor_req"]
    scored = score_request(req, data)
    norm_slot, card = select_card(scored["cards"], pick)
    sv = season_view(req.lga, req.season_type, data)
    ctx.update({"scored": scored, "picked_slot": norm_slot, "picked_card": card, "sv": sv})
    return templates.TemplateResponse(request, "decide.html", ctx)


@app.get("/share", response_class=HTMLResponse)
def share_screen(request: Request, pick: str = "a") -> HTMLResponse:
    """F7. Omnichannel Share & Smallholder Handoff (WhatsApp, Print, Audio, Radio)."""
    data = engine_data()
    ctx = page_context(request, "share")
    req: ScoreRequest = ctx["advisor_req"]
    scored = score_request(req, data)
    norm_slot, card = select_card(scored["cards"], pick)
    sv = season_view(req.lga, req.season_type, data)
    ctx.update({"scored": scored, "picked_slot": norm_slot, "picked_card": card, "sv": sv})
    return templates.TemplateResponse(request, "share.html", ctx)


@app.get("/share/print", response_class=HTMLResponse)
def print_card_screen(request: Request, pick: str = "a") -> HTMLResponse:
    """P4. Physical A5 Extension Card Print Studio."""
    data = engine_data()
    ctx = page_context(request, "share")
    req: ScoreRequest = ctx["advisor_req"]
    scored = score_request(req, data)
    norm_slot, card = select_card(scored["cards"], pick)
    sv = season_view(req.lga, req.season_type, data)
    ctx.update({"scored": scored, "picked_slot": norm_slot, "picked_card": card, "sv": sv})
    return templates.TemplateResponse(request, "print_card.html", ctx)


@app.get("/share/radio", response_class=HTMLResponse)
def radio_script_screen(request: Request) -> HTMLResponse:
    """P7. Alheri FM 98.7 Kaduna 60-Second LGA Radio Broadcast Studio."""
    data = engine_data()
    ctx = page_context(request, "share")
    ctx["radio"] = radio_script_view(ctx["advisor_req"].lga, data)
    return templates.TemplateResponse(request, "radio_script.html", ctx)


@app.get("/learn", response_class=HTMLResponse)
def learn_screen(request: Request) -> HTMLResponse:
    """F7. Scientific Framework, Model Evaluation & Data Provenance Portal."""
    data = engine_data()
    ctx = page_context(request, "learn")
    ctx["sv"] = season_view(ctx["advisor_req"].lga, ctx["advisor_req"].season_type, data)
    return templates.TemplateResponse(request, "learn.html", ctx)


@app.get("/map", response_class=HTMLResponse)
def trend_map_screen(request: Request) -> HTMLResponse:
    """F8. Interactive Kaduna 23-LGA Agroclimatic Trend Choropleth Map."""
    ctx = page_context(request, "map")
    ctx["trends_map"] = cached_map_trends()
    return templates.TemplateResponse(request, "trend_map.html", ctx)


@app.get("/history", response_class=HTMLResponse)
def history_screen(request: Request) -> HTMLResponse:
    """P5. Extension Agent (CBA) Local Offline Consultation Records & CSV Export."""
    data = engine_data()
    ctx = page_context(request, "history")
    ctx["scored"] = score_request(ctx["advisor_req"], data)
    return templates.TemplateResponse(request, "history.html", ctx)


@app.get("/walkthrough", response_class=HTMLResponse)
def walkthrough_screen(request: Request) -> HTMLResponse:
    """Interactive live 18-screen switcher (Mobile 360px & Desktop 1280px live preview)."""
    ctx = page_context(request, "walkthrough", screens_manifest=SCREENS_MANIFEST)
    return templates.TemplateResponse(request, "walkthrough.html", ctx)
