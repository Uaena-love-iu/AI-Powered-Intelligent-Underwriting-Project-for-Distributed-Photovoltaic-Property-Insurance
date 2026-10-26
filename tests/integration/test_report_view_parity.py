"""The web report and export must agree on decision-facing counts."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from app.contracts import UnderwritingCase
from app.main import app
from app.reports.view import build_report_view
from fastapi.testclient import TestClient

from tests.integration.test_report_render_api import mock_analyze_payload

NODE = os.environ.get("NODE_BINARY") or shutil.which("node")
ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(not NODE, reason="Node.js is needed for cross-language report parity")
@pytest.mark.parametrize("decision", [
    "recommend_reject", "request_more", "manual_review", "surcharge",
    "conditional_accept", "accept",
])
def test_web_and_export_report_view_agree_on_business_summary(decision: str) -> None:
    response = TestClient(app).post("/api/v1/analyze/mock", json=mock_analyze_payload())
    assert response.status_code == 200
    case = response.json()
    case["decision"]["decision"] = decision
    server = build_report_view(UnderwritingCase.model_validate(case))
    script = (
        "const fs=require('fs');const report=require('./frontend/underwriting-report.js');"
        "process.stdout.write(JSON.stringify(report.buildReportView("
        "JSON.parse(fs.readFileSync(0,'utf8')))));"
    )
    process = subprocess.run(
        [str(NODE), "-e", script], input=json.dumps(case), text=True,
        capture_output=True, check=True, cwd=ROOT,
    )
    web = json.loads(process.stdout)
    assert web["counts"] == server["coverage_counts"]
    assert [item["text"] for item in web["basis"]] == [item["text"] for item in server["basis"]]
    assert [(item["owner"], item["text"]) for item in web["actions"]] == [
        (item["owner"], item["text"]) for item in server["actions"]
    ]


@pytest.mark.skipif(not NODE, reason="Node.js is needed for cross-language report parity")
@pytest.mark.parametrize("decision", ["accept", "conditional_accept", "request_more"])
def test_web_and_export_agree_when_pricing_is_separate(decision: str) -> None:
    response = TestClient(app).post("/api/v1/analyze/mock", json=mock_analyze_payload())
    assert response.status_code == 200
    case = response.json()
    case["decision"].update({"decision": decision, "pricing_recommendation": "surcharge"})
    server = build_report_view(UnderwritingCase.model_validate(case))
    script = (
        "const fs=require('fs');const report=require('./frontend/underwriting-report.js');"
        "process.stdout.write(JSON.stringify(report.buildReportView("
        "JSON.parse(fs.readFileSync(0,'utf8')))));"
    )
    process = subprocess.run(
        [str(NODE), "-e", script], input=json.dumps(case), text=True,
        capture_output=True, check=True, cwd=ROOT,
    )
    web = json.loads(process.stdout)
    assert [item["text"] for item in web["basis"]] == [item["text"] for item in server["basis"]]
    assert [(item["owner"], item["text"]) for item in web["actions"]] == [
        (item["owner"], item["text"]) for item in server["actions"]
    ]
