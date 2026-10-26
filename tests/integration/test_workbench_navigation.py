from app.main import app
from fastapi.testclient import TestClient


def test_navigation_assets_are_served_with_correct_types():
    client = TestClient(app)
    for url, mime in [("/workbench.js", "application/javascript"), ("/workbench.css", "text/css")]:
        response = client.get(url)
        assert response.status_code == 200
        assert mime in response.headers["content-type"]
    for url in ["/evaluation", "/vision-lab", "/risk-lab", "/evaluation/report-preview", "/api/v1/weather/test", "/api/v1/components/test"]:
        page = client.get(url)
        assert page.status_code == 200
        assert '/workbench.js?v=' in page.text
        assert ('/workbench.css?v=' in page.text or '/component-model-workbench/static/style.css' in page.text)


def test_api_test_benches_expose_manual_and_image_paths():
    client = TestClient(app)
    weather = client.get("/api/v1/weather/test").text
    component = client.get("/api/v1/components/test").text
    assert 'id="longitude"' in weather and 'id="address"' in weather
    assert 'id="weatherImage"' in weather and '/api/v1/ocr/extract' in weather
    assert '/api/v1/weather/history' in weather
    assert 'id="model"' in component and 'id="file-input"' in component
    assert 'component-api-base' in component

    result = client.get("/api/v1/components/lookup", params={"model": "LR5-54HTB-440M"})
    assert result.status_code == 200
    assert result.json()["profile"]["model_match_status"] == "exact"


def test_launcher_can_identify_the_workspace_without_reading_full_page():
    response = TestClient(app).get("/evaluation")
    assert b"pv-underwriting-workbench" in response.content[:12000]
