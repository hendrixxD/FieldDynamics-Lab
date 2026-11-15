"""End-to-end FastAPI TestClient verification of all 18 screens, partials, and JSON endpoints."""

import pytest
from fastapi.testclient import TestClient

from api.main import SCREENS_MANIFEST, app

client = TestClient(app)


@pytest.mark.parametrize(
    ("screen_id", "path", "expected_text"),
    [
        ("landing-page", "/", "supports farmer judgment"),
        ("00-start", "/start", "FieldDynamics"),
        ("01-farm", "/farm", "Zaria"),
        ("02-priorities", "/priorities", "Save water"),
        ("03-season", "/season", "Zaria"),
        ("03a-crop-check", "/season/crop-check?lga=Zaria&crop=maize", "Maize"),
        ("03b-patterns", "/season/patterns?lga=Zaria", "0.0% Risk"),
        ("03c-term-explainer", "/season?lga=Zaria&explain=soil_moisture_anomaly", "Soil Moisture"),
        ("04-compare", "/compare?lga=Zaria", "Option A"),
        ("04a-card-detail", "/card/a?lga=Zaria", "Option A"),
        ("04b-decide", "/decide?lga=Zaria&pick=a", "Option A"),
        ("04c-low-confidence", "/card/b?lga=Chikun", "Confidence"),
        ("05-share", "/share?lga=Zaria&pick=a", "WhatsApp"),
        ("05a-print-card", "/share/print?lga=Zaria&pick=a", "EXTENSION CARD"),
        ("05b-radio-script", "/share/radio?lga=Zaria", "Alheri"),
        ("06-learn", "/learn?lga=Zaria", "NASA POWER"),
        ("07-trend-map", "/map?lga=Zaria", "23 LGAs"),
        ("08-history", "/history?lga=Zaria", "Offline"),
        ("walkthrough", "/walkthrough", "FieldDynamics"),
    ],
)
def test_every_screen_renders_200_with_expected_content(
    screen_id: str, path: str, expected_text: str
) -> None:
    response = client.get(path)
    assert response.status_code == 200, f"{screen_id} ({path}) failed with {response.status_code}"
    assert response.headers["content-type"].startswith("text/html")
    assert expected_text in response.text
    assert "supports farmer judgment" in response.text


def test_screens_manifest_has_all_18_design_screens() -> None:
    assert len(SCREENS_MANIFEST) == 18


def test_share_screen_excludes_sms_channel() -> None:
    response = client.get("/share?lga=Zaria&pick=a")
    assert response.status_code == 200
    assert "Send via SMS" not in response.text


def test_json_endpoints_return_valid_data_for_kaduna() -> None:
    lgas_res = client.get("/api/lgas")
    assert lgas_res.status_code == 200
    assert len(lgas_res.json()["lgas"]) == 23

    season_res = client.get("/api/lga/Zaria/season?season_type=Late%20start")
    assert season_res.status_code == 200
    assert season_res.json()["season_type"] == "Late start"
    assert len(season_res.json()["strip"]) == 12

    crop_res = client.get("/api/lga/Zaria/crop-check?crop=soybean&season_type=Dry%20spell")
    assert crop_res.status_code == 200
    assert crop_res.json()["crop"] == "soybean"

    trends_res = client.get("/api/map/trends")
    assert trends_res.status_code == 200
    assert len(trends_res.json()["trends"]) == 23

    assert client.get("/api/lga/UnknownLga/season").status_code == 404
    assert client.get("/api/lga/UnknownLga/crop-check").status_code == 404


def test_htmx_partials_and_static_geojson_serve_cleanly() -> None:
    cards_res = client.post(
        "/partials/cards",
        json={"lga": "Zaria", "s1": "maize", "s2": "maize", "season_type": "Dry spell"},
    )
    assert cards_res.status_code == 200
    assert "Option A" in cards_res.text

    term_res = client.get("/partials/explain/soil_moisture_anomaly?lga=Zaria")
    assert term_res.status_code == 200
    assert "SMAP" in term_res.text

    assert client.get("/partials/explain/not_a_term?lga=Zaria").status_code == 404

    geo_res = client.get("/static/geo/kaduna_lgas.geojson")
    assert geo_res.status_code == 200
    assert len(geo_res.json()["features"]) == 23
