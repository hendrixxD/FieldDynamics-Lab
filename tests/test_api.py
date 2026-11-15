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


def test_favicon_serves_ico() -> None:
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert "image/x-icon" in response.headers.get("content-type", "")
    assert len(response.content) > 0


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


def test_lgas_returns_23_lgas_with_metadata() -> None:
    response = client.get("/api/lgas")
    assert response.status_code == 200
    lgas = response.json()["lgas"]
    assert len(lgas) == 23
    zaria = next(item for item in lgas if item["lga"] == "Zaria")
    assert zaria["zone"] == "North"
    assert zaria["cropland_pct"] > 0
    assert zaria["onset_label"] != "Unknown"
    assert zaria["soil_default"] in ("Sandy", "Loamy", "Clay")


def test_lga_season_returns_strip_and_patterns() -> None:
    response = client.get("/api/lga/Zaria/season")
    assert response.status_code == 200
    data = response.json()
    assert len(data["strip"]) == 12
    assert len(data["patterns"]) == 9
    assert "soil_moisture_anomaly" in data["terms"]


def test_lga_season_rejects_unknown_lga() -> None:
    response = client.get("/api/lga/UnknownPlace/season")
    assert response.status_code == 404
    assert "Unknown LGA" in response.json()["detail"]


def test_lga_crop_check_returns_fao56_water_balance() -> None:
    response = client.get("/api/lga/Zaria/crop-check?crop=maize")
    assert response.status_code == 200
    data = response.json()
    assert data["crop"] == "maize"
    assert len(data["stages"]) == 4
    assert data["water_need_mm"] > 0
    assert len(data["comparisons"]) >= 4


def test_map_trends_returns_all_23_lgas() -> None:
    response = client.get("/api/map/trends")
    assert response.status_code == 200
    trends = response.json()["trends"]
    assert len(trends) == 23
    assert "Zaria" in trends
    assert trends["Zaria"]["annual_rain_mm"] > 500


def test_legend_all_returns_four_layers() -> None:
    response = client.get("/api/legend")
    assert response.status_code == 200
    layers = response.json()["layers"]
    assert set(layers.keys()) == {"soil_moisture", "heat_stress", "erosion_risk", "water_stress"}
    assert layers["soil_moisture"]["unit"] == "z-score (SD)"


def test_legend_layer_returns_color_stops() -> None:
    response = client.get("/api/legend/heat_stress")
    assert response.status_code == 200
    data = response.json()
    assert data["layer"] == "heat_stress"
    assert len(data["stops"]) >= 4
    assert data["value_max"] == 60.0


def test_legend_layer_rejects_unknown() -> None:
    response = client.get("/api/legend/unknown_layer")
    assert response.status_code == 404
    assert "Unknown layer" in response.json()["detail"]


def test_point_resolves_valid_coordinate() -> None:
    # Zaria LGA centroid (lat=11.085, lon=7.720)
    response = client.get("/api/point?lat=11.085&lon=7.720")
    assert response.status_code == 200
    data = response.json()
    assert data["lga"] == "Zaria"
    assert data["zone"] == "North"
    assert data["layers"]["annual_rain_mm"] > 500
    assert -3.0 <= data["layers"]["soil_moisture_z"] <= 3.0


def test_point_rejects_coordinate_outside_kaduna() -> None:
    # Lagos coordinates (outside Kaduna)
    response = client.get("/api/point?lat=6.524&lon=3.379")
    assert response.status_code == 422
    assert "outside Kaduna State" in response.json()["detail"]


def test_lga_grid_geojson_returns_feature_collection() -> None:
    response = client.get("/api/layers/Zaria/grid.geojson")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "FeatureCollection"
    assert data["lga"] == "Zaria"
    assert data["cell_count"] > 100
    first_cell = data["features"][0]
    assert first_cell["type"] == "Feature"
    assert "cell_id" in first_cell["properties"]
    assert "soil_moisture_z" in first_cell["properties"]


def test_lga_grid_geojson_rejects_unknown_lga() -> None:
    response = client.get("/api/layers/NonExistentLGA/grid.geojson")
    assert response.status_code == 404
    assert "Unknown LGA" in response.json()["detail"]


def test_static_state_layer_pngs_and_jemaa_queries() -> None:
    bounds_res = client.get("/static/layers/bounds.json")
    assert bounds_res.status_code == 200
    bounds_data = bounds_res.json()
    assert len(bounds_data["bounds"]) == 2
    for layer in ("soil_moisture", "heat_stress", "erosion_risk", "water_stress"):
        png_res = client.get(f"/static/layers/state/{layer}.png")
        assert png_res.status_code == 200
        assert png_res.headers["content-type"].startswith("image/png")

    grid_res = client.get("/api/layers/Jema'a/grid.geojson")
    assert grid_res.status_code == 200
    grid_data = grid_res.json()
    assert grid_data["lga"] == "Jema'a"
    assert grid_data["cell_count"] > 50

    pt_res = client.get("/api/point?lat=9.488&lon=8.261")
    assert pt_res.status_code == 200
    assert pt_res.json()["lga"] == "Jema'a"


def test_home_mobile_showcase_assets_and_budget() -> None:
    home_res = client.get("/")
    assert home_res.status_code == 200
    assert "supports farmer judgment" in home_res.text
    assert home_res.text.count("/static/img/logo-mark.png") == 2

    total_bytes = len(home_res.content)
    for asset in ("/static/css/app.css", "/static/img/logo-mark.png"):
        res = client.get(asset)
        assert res.status_code == 200
        total_bytes += len(res.content)

    for screen in ("compare", "season", "map", "farm", "share"):
        path = f"/static/img/mobile/mobile-{screen}.webp"
        assert path in home_res.text
        img_res = client.get(path)
        assert img_res.status_code == 200
        assert len(img_res.content) > 0
        total_bytes += len(img_res.content)

    msg = f"First-load homepage asset budget exceeded: {total_bytes} bytes"
    assert total_bytes < 1_000_000, msg
