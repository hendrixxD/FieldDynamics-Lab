"""FastAPI application. Routes stay thin: validate, call model/, return a template or JSON."""

from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from api.schemas import ScoreResponse
from model.engine_data import EngineData, load_bundle
from model.rotation_engine import Request as ScoreRequest
from model.rotation_engine import score_request

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="FieldDynamics-Lab")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class HealthResponse(BaseModel):
    """Body of the health check."""

    status: str


@lru_cache(maxsize=1)
def engine_data() -> EngineData:
    """Read the engine bundle once per process (about 0.1 s); every request reuses it."""
    return load_bundle()


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Serve the landing page."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
def health() -> HealthResponse:
    """Report that the app started, so a preview can be confirmed as running."""
    return HealthResponse(status="ok")


@app.post("/api/score")
def score(body: ScoreRequest) -> ScoreResponse:
    """Score the farmer's current rotation and the two best options for their LGA."""
    data = engine_data()
    if body.lga not in data.zone_of:
        raise HTTPException(status_code=422, detail=f"Unknown LGA: {body.lga}")
    return ScoreResponse.model_validate(score_request(body, data))
