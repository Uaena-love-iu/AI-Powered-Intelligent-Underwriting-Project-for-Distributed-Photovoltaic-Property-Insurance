import copy
import shutil
import time
from html.parser import HTMLParser

import pytest
from app.api.component_dependencies import get_underwriting_component_provider
from app.api.component_workbench_routes import API_BASE
from app.component_workbench_runtime import WORKBENCH_ROOT, runtime
from app.contracts import ComponentProfile
from app.main import app
from app.providers.component_workbench import ComponentWorkbenchProvider
from app.security import AuthRepository
from fastapi.testclient import TestClient


@pytest.fixture
def bench(tmp_path, monkeypatch):
    from workflow import Workbench

    shutil.copytree(WORKBENCH_ROOT / "data", tmp_path / "data")
    bench = Workbench(tmp_path, reader=None, key="")
    monkeypatch.setattr(runtime(), "bench", bench)
    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    monkeypatch.delitem(app.dependency_overrides, get_underwriting_component_provider, raising=False)
    monkeypatch.setenv("PV_COMPONENT_WORKBENCH_AGENT_ENABLED", "false")
    return bench


def test_page_assets_api_and_proxy_prefix(bench):
    class References(HTMLParser):
        def __init__(self):
            super().__init__()
            self.assets = []

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == "script" and attrs.get("src"):
                self.assets.append(attrs["src"])
            if tag == "link" and attrs.get("rel") == "stylesheet":
                self.assets.append(attrs["href"])

    with TestClient(app) as client:
        page = client.get("/component-model-workbench/")
        assert page.status_code == 200
        refs = References()
        refs.feed(page.text)
        assert len(refs.assets) >= 2
        for asset in refs.assets:
            assert client.get(asset).status_code == 200
        assert API_BASE in page.text
        assert 'href="/evaluation"' in page.text
        assert client.get(API_BASE + "/api/status").json()["local_rows"] > 0
        assert client.get(API_BASE + "/api/rules?q=JAM66D42").status_code == 200
        assert client.post(API_BASE + "/api/identify", files={"file": ("x.png", b"")}).status_code == 422
    with TestClient(app, root_path="/pv") as client:
        assert 'content="/pv' + API_BASE + '"' in client.get("/component-model-workbench/").text


def test_local_job_export_feedback_shared_with_underwriting(bench):
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(API_BASE + "/api/lookup", headers={"Origin": "https://testserver"},
                               json={"model": "LR5-54HTB-440M", "strategy": "local", "use_agent": False})
        assert response.status_code == 200
        rid = response.json()["job_id"]
        for _ in range(100):
            job = client.get(API_BASE + "/api/jobs/" + rid).json()
            if job["state"] != "running":
                break
            time.sleep(.01)
        assert job["state"] == "done", job
        exported = client.get(API_BASE + "/api/history/" + rid + "/export")
        assert exported.json() == job["result"]
        feedback = client.post(API_BASE + "/api/history/" + rid + "/feedback",
                               json={"verdict": "verified", "note": "测试核验原文", "fields": ["front_static_load_pa"]})
        assert feedback.status_code == 200
        provider = get_underwriting_component_provider()
        assert provider.workbench is bench
        profile = provider.lookup("LR5-54HTB-440M")
        assert profile.front_static_load_pa == 5400
        assert profile.wind_load_pa is None and profile.snow_load_pa is None
        records = profile.parameter_evidence["front_static_load_pa"]["records"]
        assert records[0]["human_verification"]["run_id"] == rid
        assert ComponentProfile.model_validate_json(profile.model_dump_json()) == profile
        assert client.post(API_BASE + "/api/lookup", headers={"Origin": "https://evil.test"},
                           json={"model": "LR5-54HTB-440M"}).status_code == 403


@pytest.mark.parametrize("mismatch,conflict", [(True, False), (False, True), (False, False)])
def test_adapter_gates_identity_conflicts_and_preserves_evidence(bench, mismatch, conflict):
    result = bench.run("LR5-54HTB-440M", strategy="local", use_agent=False)
    raw = result["profile"]
    if mismatch:
        raw["component_model"] = "OTHER-440M"
    if conflict:
        raw["parameter_evidence"]["front_static_load_pa"][0]["status"] = "conflict"

    class Stub:
        def run(self, *args, **kwargs):
            return copy.deepcopy(result)

    profile = ComponentWorkbenchProvider(workbench=Stub()).lookup("LR5-54HTB-440M")
    assert profile.front_static_load_pa == (None if mismatch or conflict else 5400)
    assert profile.wind_load_pa is None and profile.snow_load_pa is None
    assert profile.workbench_run_id == result["id"]
    assert profile.parameter_evidence["front_static_load_pa"]["records"] == raw["parameter_evidence"]["front_static_load_pa"]


def test_authentication_and_role_checks_cover_mounted_api(bench, monkeypatch):
    monkeypatch.setenv("PV_AUTH_ENABLED", "true")
    monkeypatch.setenv("PV_ADMIN_USERNAME", "admin-user")
    monkeypatch.setenv("PV_ADMIN_PASSWORD", "long-test-password-123")
    with TestClient(app) as client:
        assert client.get(API_BASE + "/api/history").status_code == 401
        user = AuthRepository().create_user(username="reader-user", password="long-test-password-123", role="viewer")
        token, _ = AuthRepository().issue_session(user)
        headers = {"Authorization": "Bearer " + token}
        assert client.get(API_BASE + "/api/history", headers=headers).status_code == 200
        assert client.post(API_BASE + "/api/lookup", headers=headers,
                           json={"model": "LR5-54HTB-440M"}).status_code == 403
        response = client.post("/api/v1/auth/login", json={"username": "admin-user", "password": "long-test-password-123"})
        headers = {"Authorization": "Bearer " + response.json()["access_token"]}
        assert client.post(API_BASE + "/api/identify", headers=headers,
                           files={"file": ("x.png", b"")}).status_code == 422


def test_pdf_recognition_uses_workbench_and_blocks_conflicting_model(bench):
    from types import SimpleNamespace

    import pymupdf
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((50, 50), "Module model: LR5-54HTB-440M")
    page.insert_text((50, 80), "Manufacturer: LONGi Solar photovoltaic module equipment inventory.")
    page.insert_text((50, 100), "Literal text document for exact model recognition and source verification.")
    content = document.tobytes()
    document.close()
    item = SimpleNamespace(content=content, material=SimpleNamespace(
        category=SimpleNamespace(value="component_nameplate"), file_name="nameplate.pdf", material_id="module-1"))
    provider = get_underwriting_component_provider()
    profile = provider.lookup_materials(SimpleNamespace(component_model=None), [item])
    assert profile.component_model == "LR5-54HTB-440M"
    assert profile.workbench_result["recognition"][0]["candidates"][0]["auto_query_eligible"]
    assert provider.lookup_materials(SimpleNamespace(component_model="DIFFERENT-600M"), [item]) is None
    assert provider.last_result["status"] == "identity_conflict"


def test_xlsx_inventory_preserves_literal_model_and_cell_evidence(bench):
    from types import SimpleNamespace

    from unit.test_equipment_inventory_xlsx import _workbook_bytes

    item = SimpleNamespace(content=_workbook_bytes(model="LR5-54HTB-440M"), material=SimpleNamespace(
        category=SimpleNamespace(value="equipment_inventory"),
        file_name="equipment.xlsx", material_id="inventory-1"))
    provider = get_underwriting_component_provider()
    result = provider._identify_inventory(item)
    candidates = result["candidates"]
    assert [candidate["lookup_model"] for candidate in candidates] == ["LR5-54HTB-440M"]
    assert candidates[0]["auto_query_eligible"]
    assert candidates[0]["row_number"] == 2
    assert candidates[0]["worksheet_name"] == "设备清单"
    assert "8525" in candidates[0]["quote"]
    assert provider.lookup_materials(SimpleNamespace(component_model="OTHER-600M"), [item]) is None
    assert provider.last_result["status"] == "identity_conflict"


def test_replacement_preserves_original_module_output_in_case_and_report(bench):
    from app.catastrophe import CatastropheAssessmentEngine
    from app.contracts import UnderwritingCase
    from app.reports import generate_markdown_report
    from unit.test_report_generator import make_case
    profile = get_underwriting_component_provider().lookup("LR5-54HTB-440M")
    assessment = CatastropheAssessmentEngine().assess(component=profile)
    case = make_case().model_copy(update={"component_profile": profile, "catastrophe_assessment": assessment})
    restored = UnderwritingCase.model_validate_json(case.model_dump_json())
    fields = restored.catastrophe_assessment.module_result["profile"]["ui_fields"]
    assert fields["wind_load_pa"]["value"] == 2400
    assert fields["wind_load_pa"]["status"] == "mechanical_mapped"
    assert fields["snow_load_pa"]["value"] == 5400
    assert profile.wind_load_pa is None
    report = generate_markdown_report(restored)
    assert "5400" in report and "组件工作台识别结果" in report
    assert "字段证据" in report
    assert "旧版技术筛查的能力标签" not in report
    assert restored.catastrophe_assessment.comparisons == []


def test_old_component_entry_points_do_not_run_old_implementations(bench):
    with TestClient(app) as client:
        for path in ("/component-agent", "/api/v1/components/test"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 307
            assert response.headers["location"] == "/component-model-workbench/"
        assert client.post("/api/v1/components/corrections", json={}).status_code == 410
        assert client.post("/api/v1/components/agent/runs", json={}).status_code == 410
        assert client.post("/api/v1/risk/lab/assess", json={}).status_code == 410
