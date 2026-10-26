import json
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.api.ocr_routes import get_ocr_provider
from app.api.routes import get_vision_provider
from app.api.component_dependencies import get_underwriting_component_provider
from app.main import app
from app.providers import MockOcrProvider, MockVisionProvider, ProviderError
from app.pipeline.recovery import RecoveryStore
from app.storage import AnalysisJobRepository


class CountingOcr(MockOcrProvider):
    def __init__(self):
        self.calls = 0
    def extract(self, item):
        self.calls += 1
        return super().extract(item)


class FaultVision(MockVisionProvider):
    def __init__(self):
        self.calls, self.fail = 0, True
    def analyze_with_watermark(self, item):
        self.calls += 1
        if self.fail:
            raise ProviderError("do not expose service secret") from httpx.HTTPStatusError(
                "unauthorized", request=httpx.Request("POST","https://example.test/private"), response=httpx.Response(401))
        return super().analyze_with_watermark(item)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("PV_AUTH_ENABLED", "false")
    monkeypatch.delenv("PV_AMAP_WEB_SERVICE_KEY", raising=False)
    ocr, vision = CountingOcr(), FaultVision()
    original = dict(app.dependency_overrides)
    app.dependency_overrides[get_ocr_provider] = lambda: ocr
    app.dependency_overrides[get_vision_provider] = lambda: vision
    app.dependency_overrides[get_underwriting_component_provider] = lambda: None
    with TestClient(app) as client:
        yield client, ocr, vision
    app.dependency_overrides.clear()
    app.dependency_overrides.update(original)


def submit(client):
    image = BytesIO()
    Image.new("RGB", (80,60), "white").save(image,format="PNG")
    manifest = {"case_id":"recovery-test", "project":dict(project_name=None, insured_name=None,
        project_type="unknown", installation_type="unknown", site_address=None, longitude=None,
        latitude=None, proposed_start_date=None, component_model=None),
        "materials":[{"material_id":"m1","category":"component_nameplate"}]}
    response = client.post("/api/v1/underwriting/jobs", data={"manifest":json.dumps(manifest)},
        files=[("files",("test.png",image.getvalue(),"image/png"))])
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def test_failure_recovery_preserves_success_original_run_and_deduplicates(setup):
    client, ocr, vision = setup
    first = submit(client)
    job = client.get('/api/v1/underwriting/jobs/'+first).json()
    assert job['status'] == 'completed'
    assert job['can_retry']
    assert job['recovery']['issues'][0]['code'] == 'SERVICE_AUTH'
    assert vision.calls == 1  # Authentication errors must never auto-retry.
    assert ocr.calls == 2
    assert 'do not expose' not in json.dumps(job)
    vision.fail = False
    response = client.post('/api/v1/underwriting/jobs/'+first+'/retry')
    assert response.status_code == 202, response.text
    second = response.json()['job_id']
    resumed = client.get('/api/v1/underwriting/jobs/'+second).json()
    assert resumed['status'] == 'completed'
    assert not resumed['recovery']['issues']
    assert ocr.calls == 2  # Successful OCR is restored from the durable checkpoint.
    assert vision.calls == 2
    assert any(s['status']=='reused' for s in resumed['recovery']['steps'])
    duplicate = client.post('/api/v1/underwriting/jobs/'+first+'/retry')
    assert duplicate.json()['job_id'] == second
    assert vision.calls == 2
    assert client.get('/api/v1/underwriting/jobs/'+first).json()['recovery']['issues']
    events = client.get('/api/v1/underwriting/jobs/'+second+'/events').json()['events']
    assert len([e for e in events if e['category']=='service_attention']) == 1
    assert client.post('/api/v1/underwriting/jobs/'+second+'/retry').status_code == 409


def test_restart_recovery_and_manual_attention(setup):
    client, ocr, vision = setup
    first = submit(client)
    # Model an interrupted next run with request/checkpoints already persisted.
    store = RecoveryStore()
    store.claim_retry(first,'interrupted')
    jobs = AnalysisJobRepository()
    jobs.create_job('interrupted','interrupted-case')
    jobs.update_job('interrupted',status='running',progress_percent=40,current_step='图片识别')
    jobs.fail_incomplete_jobs()
    assert client.get('/api/v1/underwriting/jobs/interrupted').json()['can_retry']
    vision.fail = False
    result = client.post('/api/v1/underwriting/jobs/interrupted/retry')
    assert result.status_code == 202, result.text
    assert ocr.calls == 2
    assert client.post('/api/v1/underwriting/jobs/'+first+'/manual-attention').status_code == 200
    client.post('/api/v1/underwriting/jobs/'+first+'/manual-attention')
    events = client.get('/api/v1/underwriting/jobs/'+first+'/events').json()['events']
    assert len([e for e in events if e['category']=='manual_attention']) == 1


def test_stop_is_cooperative_and_terminal_task_is_not_relabelled(setup):
    client, _, _ = setup
    first = submit(client)
    assert client.post('/api/v1/underwriting/jobs/'+first+'/stop').status_code == 200
    assert client.get('/api/v1/underwriting/jobs/'+first).json()['status'] == 'completed'
    RecoveryStore().claim_retry(first,'queued-stop')
    AnalysisJobRepository().create_job('queued-stop','queued-stop-case')
    client.post('/api/v1/underwriting/jobs/queued-stop/stop')
    assert RecoveryStore().get('queued-stop')['cancel_requested'] == 1
