from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_readiness_reports_configured_components_without_secrets() -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    result = response.json()
    assert result["status"] in {"ready", "degraded"}
    assert result["weather_connectivity_checked"] is False
    assert isinstance(result["component_online_catalog_configured"], bool)
    assert result["component_online_connectivity_checked"] is False
    assert isinstance(result["solar_stack_partner_api_configured"], bool)
    assert result["solar_stack_partner_api_connectivity_checked"] is False
    assert "checks" in result
    assert "api_key" not in response.text.casefold()


def analyze_payload() -> dict:
    return {
        "case_id": "case-api-001",
        "project": {
            "project_name": "示例分布式光伏项目",
            "insured_name": "示例制造企业有限公司",
            "project_type": "rooftop",
            "installation_type": "color_steel_roof",
            "site_address": "广东省示例市示例区",
            "longitude": 113.25,
            "latitude": 23.12,
            "proposed_start_date": "2026-10-01",
            "component_model": "PV-MODULE-580W",
        },
        "materials": [
            {
                "material_id": "material-nameplate-001",
                "category": "component_nameplate",
                "file_name": "component-nameplate.jpg",
                "media_type": "image/jpeg",
                "quality_status": "usable",
                "quality_confidence": 0.95,
                "quality_issues": [],
                "parse_status": "success",
            },
            {
                "material_id": "material-panorama-001",
                "category": "panorama",
                "file_name": "site-panorama.jpg",
                "media_type": "image/jpeg",
                "quality_status": "usable",
                "quality_confidence": 0.95,
                "quality_issues": [],
                "parse_status": "success",
            },
        ],
    }


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mock_analyze_returns_complete_case() -> None:
    response = client.post(
        "/api/v1/analyze/mock",
        json=analyze_payload(),
    )

    assert response.status_code == 200

    result = response.json()

    assert result["schema_version"] == "0.1.0"
    assert result["case_id"] == "case-api-001"
    assert len(result["materials"]) == 2
    assert len(result["ocr_fields"]) == 1
    assert len(result["findings"]) == 1
    assert len(result["material_reviews"]) == 2
    assert any(trace["step"] == "installation_group" for trace in result["processing_trace"])
    assert result["decision"]["decision"] == "request_more"
    assert "ENV-SITE-RELATION-REVIEW-001" in result["decision"]["decisive_rule_ids"]


def test_mock_analyze_rejects_empty_materials() -> None:
    payload = analyze_payload()
    payload["materials"] = []

    response = client.post(
        "/api/v1/analyze/mock",
        json=payload,
    )

    assert response.status_code == 422


def test_mock_report_returns_downloadable_markdown() -> None:
    response = client.post(
        "/api/v1/reports/mock",
        json=analyze_payload(),
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        'attachment; filename="underwriting-report.md"'
    )
    assert response.text.startswith(
        "# 分布式光伏财产险 AI 核保报告"
    )
    assert "建议结论：**建议补充材料**" in response.text
    assert "site-panorama.jpg" in response.text
    assert "临近水体" in response.text
