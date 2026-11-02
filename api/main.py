"""FastAPI application. Routes stay thin: validate, call model/, return a template or JSON."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="FieldDynamics-Lab")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


class HealthResponse(BaseModel):
    """Body of the health check."""

    status: str


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    """Serve the landing page."""
    return templates.TemplateResponse(request, "index.html")


@app.get("/api/health")
def health() -> HealthResponse:
    """Report that the app started, so a preview can be confirmed as running."""
    return HealthResponse(status="ok")
