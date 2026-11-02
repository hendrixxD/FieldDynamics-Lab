"""Checks the status code and response shape of every endpoint."""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def test_home_serves_page_with_farmer_judgment_statement() -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "supports farmer judgment" in response.text


def test_health_reports_ok() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
