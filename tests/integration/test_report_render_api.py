from app.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def mock_analyze_payload() -> dict:
    return {
        "case_id": "case-report-api-001",
        "project": {
            "project_name": "报告接口测试项目",
            "insured_name": "示例制造企业有限公司",
            "project_entity": (
                "示例新能源有限公司"
            ),
            "project_type": "rooftop",
            "installation_type": (
                "color_steel_roof"
            ),
            "site_address": (
                "广东省示例市示例区"
            ),
            "province": "广东省",
            "city": "示例市",
            "district": "示例区",
            "longitude": 113.25,
            "latitude": 23.12,
            "proposed_start_date": (
                "2026-10-01"
            ),
            "component_model": (
                "PV-MODULE-580W"
            ),
        },
        "materials": [
            {
                "material_id": (
                    "material-nameplate-001"
                ),
                "category": (
                    "component_nameplate"
                ),
                "file_name": (
                    "component-nameplate.jpg"
                ),
                "media_type": "image/jpeg",
                "quality_status": "usable",
                "quality_confidence": 0.95,
                "quality_issues": [],
                "parse_status": "success",
            },
            {
                "material_id": (
                    "material-panorama-001"
                ),
                "category": "panorama",
                "file_name": (
                    "site-panorama.jpg"
                ),
                "media_type": "image/jpeg",
                "quality_status": "usable",
                "quality_confidence": 0.95,
                "quality_issues": [],
                "parse_status": "success",
            },
        ],
    }


def complete_case_payload() -> dict:
    response = client.post(
        "/api/v1/analyze/mock",
        json=mock_analyze_payload(),
    )

    assert response.status_code == 200

    return response.json()


def test_renders_complete_case_as_downloadable_markdown() -> None:
    case = complete_case_payload()

    response = client.post(
        "/api/v1/reports/render",
        json=case,
    )

    assert response.status_code == 200
    assert response.headers[
        "content-type"
    ].startswith("text/markdown")
    assert response.headers[
        "content-disposition"
    ] == (
        "attachment; "
        'filename="underwriting-report.md"'
    )

    assert response.text.startswith(
        "# 分布式光伏财产险 AI 核保报告"
    )
    assert (
        "case-report-api-001"
        in response.text
    )
    assert (
        "报告接口测试项目"
        in response.text
    )
    assert (
        "component-nameplate.jpg"
        in response.text
    )
    assert (
        "site-panorama.jpg"
        in response.text
    )
    assert "临近水体" in response.text


def test_rejects_case_with_invalid_references() -> None:
    case = complete_case_payload()

    case["ocr_fields"][0][
        "material_id"
    ] = "unknown-material"

    response = client.post(
        "/api/v1/reports/render",
        json=case,
    )

    assert response.status_code == 422
