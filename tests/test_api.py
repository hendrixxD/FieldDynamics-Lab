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


def test_score_returns_current_and_two_options() -> None:
    body = {"lga": "Zaria", "past_seasons": [["maize"], ["maize"]], "season_type": "Dry spell"}
    response = client.post("/api/score", json=body)
    assert response.status_code == 200
    result = response.json()
    assert [c["badge"] for c in result["cards"]] == ["Current", "Option A", "Option B"]
    assert result["zone"] == "North"
    assert set(result["cards"][1]["rows"]) == set(result["weights"])


def test_score_rejects_unknown_lga() -> None:
    response = client.post("/api/score", json={"lga": "Lagos", "past_seasons": [["maize"]]})
    assert response.status_code == 422
    assert "Unknown LGA" in response.json()["detail"]


def test_score_rejects_unknown_priority() -> None:
    body = {"lga": "Zaria", "past_seasons": [["maize"]], "priorities": ["get_rich"]}
    assert client.post("/api/score", json=body).status_code == 422
