from app.api.schemas import HumanReviewSubmission
from app.contracts import DecisionType, UnderwritingCase
from app.main import app
from app.storage import CaseRepository
from fastapi.testclient import TestClient

from tests.integration.test_report_render_api import mock_analyze_payload


def test_human_review_records_acceptance_and_price_independently(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PV_CASE_DB_PATH", str(tmp_path / "cases.sqlite3"))
    client = TestClient(app)
    analyzed = client.post("/api/v1/analyze/mock", json=mock_analyze_payload())
    assert analyzed.status_code == 200
    CaseRepository().save_case(UnderwritingCase.model_validate(analyzed.json()))
    assert CaseRepository().list_cases()[0]["system_pricing_recommendation"] == "not_proposed"

    response = client.post(
        "/api/v1/cases/case-report-api-001/review",
        json={
            "reviewer_name": "核保员", "final_decision": "conditional_accept",
            "pricing_recommendation": "surcharge", "comment": "附加条件和加费依据已核对",
        },
    )
    assert response.status_code == 200
    record = response.json()
    assert record["final_decision"] == "conditional_accept"
    assert record["pricing_recommendation"] == "surcharge"
    assert record["review_history"][0]["pricing_recommendation"] == "surcharge"
    report = client.get("/api/v1/cases/case-report-api-001/report")
    assert report.status_code == 200
    assert "建议附条件承保（加费）" in report.text


def test_human_review_rejects_price_on_rejection() -> None:
    response = TestClient(app).post(
        "/api/v1/cases/unknown/review",
        json={
            "reviewer_name": "核保员", "final_decision": "recommend_reject",
            "pricing_recommendation": "surcharge", "comment": "拒保",
        },
    )
    assert response.status_code == 422


def test_legacy_surcharge_submission_maps_to_acceptance_with_price() -> None:
    submission = HumanReviewSubmission.model_validate({
        "reviewer_name": "核保员", "final_decision": "surcharge", "comment": "已核对加费依据",
    })
    assert submission.final_decision is DecisionType.ACCEPT
    assert submission.pricing_recommendation == "surcharge"
