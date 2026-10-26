import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from app.contracts import Material, ProjectInfo
from app.pipeline.recovery import (RecoveryStore, JobExecution, RecoverableProvider,
    JobStopped, JobDeadline, classify_error, unpack)
from app.providers.common import MaterialInput, ProviderError


def material():
    return MaterialInput(Material(material_id="m1", category="component_nameplate", file_name="x.png",
        media_type="image/png", quality_status="usable", quality_issues=[], parse_status="success"), b"image", "business")


class Provider:
    name, model_name = "test-ocr", "test-model"
    def __init__(self, error=None):
        self.calls, self.error = 0, error
    def extract(self, item):
        self.calls += 1
        if self.error:
            raise ProviderError("secret must not be shown") from self.error
        return []


def execution(tmp_path):
    store = RecoveryStore(tmp_path / "recovery.sqlite3")
    store.create("j1", {"materials": [material()], "project": ProjectInfo(project_name=None, insured_name=None, project_type="unknown", installation_type="unknown", site_address=None, longitude=None, latitude=None, proposed_start_date=None, component_model=None)})
    return store, JobExecution("j1", store)


def test_success_is_durable_and_failed_call_only_is_retried(tmp_path):
    store, run = execution(tmp_path)
    good, bad = Provider(), Provider(httpx.ReadTimeout("private URL"))
    run.call(good, "extract", (material(),), {})
    failed_material = replace(material(), content=b"different file")
    with pytest.raises(ProviderError):
        run.call(bad, "extract", (failed_material,), {})
    assert bad.calls == 2
    assert len(store.describe("j1")["issues"]) == 1
    store.claim_retry("j1", "j2")
    resumed = JobExecution("j2", RecoveryStore(store.path))
    resumed.call(good, "extract", (material(),), {})
    bad.error = None
    resumed.call(bad, "extract", (failed_material,), {})
    assert good.calls == 1
    assert bad.calls == 3
    assert [s["status"] for s in store.describe("j2")["steps"]] == ["reused", "success"]
    assert store.describe("j1")["issues"][0]["code"] == "SERVICE_TIMEOUT"
    assert "private URL" not in json.dumps(store.describe("j1"))
    assert "secret" not in json.dumps(store.describe("j1"))


@pytest.mark.parametrize("status,retries,code", [(401,1,"SERVICE_AUTH"),(403,1,"SERVICE_AUTH"),(400,1,"SERVICE_CONFIG"),(429,2,"SERVICE_BUSY"),(503,2,"SERVICE_BUSY")])
def test_retry_policy(tmp_path,status,retries,code):
    store, run = execution(tmp_path)
    error = httpx.HTTPStatusError("secret",request=httpx.Request("POST","https://example.test"),response=httpx.Response(status))
    provider = Provider(error)
    with pytest.raises(ProviderError):
        RecoverableProvider(provider,run).extract(material())
    assert provider.calls == retries
    assert store.describe("j1")["issues"][0]["code"] == code


def test_inputs_rules_and_model_changes_invalidate_cache(tmp_path):
    store, run = execution(tmp_path)
    provider = Provider()
    run.call(provider,"extract",(material(),),{})
    provider.model_name = "different-model"
    run.call(provider,"extract",(material(),),{})
    run.call(provider,"extract",(replace(material(),content=b"replacement"),),{})
    assert provider.calls == 3


def test_retry_claim_is_atomic_idempotent_and_bounded(tmp_path):
    store, _ = execution(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda child: store.claim_retry("j1",child), ["a","b"]))
    assert claims[0][0] == claims[1][0]
    assert sum(created for _,created in claims) == 1
    store.claim_retry(claims[0][0],"third")
    store.claim_retry("third","fourth")
    with pytest.raises(ValueError,match="上限"):
        store.claim_retry("fourth","fifth")


def test_stop_prevents_new_calls_and_preserves_success(tmp_path):
    store, run = execution(tmp_path)
    provider = Provider()
    run.call(provider,"extract",(material(),),{})
    store.cancel("j1")
    with pytest.raises(JobStopped):
        run.call(provider,"extract",(material(),),{})
    assert provider.calls == 1
    assert store.describe("j1")["retained"] == 1


def test_deadline_prevents_new_calls(tmp_path):
    store, run = execution(tmp_path)
    run.deadline = 0
    with pytest.raises(JobDeadline):
        run.call(Provider(),"extract",(material(),),{})


def test_notification_deduplication_and_request_restoration(tmp_path):
    store, _ = execution(tmp_path)
    store.event("j1","service_attention","需要处理")
    store.claim_retry("j1","j2")
    store.event("j2","service_attention","再次失败")
    assert len(store.describe("j2")["events"]) == 1
    request = unpack(json.loads(store.get("j2")["request_json"]))
    assert request["materials"][0].content == b"image"
    assert isinstance(request["project"],ProjectInfo)


def test_prune_removes_source_materials_and_checkpoints(tmp_path):
    store, run = execution(tmp_path)
    run.call(Provider(),"extract",(material(),),{})
    with store.db() as db:
        db.execute("UPDATE recovery_runs SET created_at='2000-01-01'")
    assert store.purge_expired() == 1
    assert store.get("j1") is None
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM recovery_calls").fetchone()[0] == 0


def test_unknown_and_invalid_output_are_distinct():
    assert not classify_error(RuntimeError("secret"))["auto_retry"]
    assert classify_error(json.JSONDecodeError("bad","",0))["code"] == "MODEL_OUTPUT_INVALID"


def test_conflicting_quality_output_is_not_cached_or_sent_as_customer_retake(tmp_path):
    from app.contracts import RiskFinding
    from app.providers.vision.base import VisionAnalysis
    store, run = execution(tmp_path)
    class Vision(Provider):
        def analyze_with_watermark(self, item):
            self.calls += 1
            return VisionAnalysis(findings=[RiskFinding(finding_id="quality", material_id="m1",
                category="image_quality", label="图片质量", detection_status="detected", severity="high",
                confidence=.9, evidence_text="照片清晰，满足检查要求", provider=self.name, model=self.model_name,
                requires_manual_review=False)])
    vision = Vision()
    with pytest.raises(ProviderError):
        run.call(vision,"analyze_with_watermark",(material(),),{})
    assert vision.calls == 2
    issue = store.describe("j1")["issues"][0]
    assert issue["code"] == "MODEL_OUTPUT_INVALID"
    assert "不据此要求客户补拍" in issue["action"]
    assert store.describe("j1")["retained"] == 0


def test_routed_model_change_invalidates_checkpoint(tmp_path):
    from app.providers.routing import RoutedOcrProvider
    store, run = execution(tmp_path)
    underlying = Provider()
    routed = RoutedOcrProvider(underlying)
    run.call(routed,"extract",(material(),),{})
    underlying.model_name = "new-version"
    run.call(routed,"extract",(material(),),{})
    assert underlying.calls == 2


def test_geocode_success_reuses_typed_candidates(tmp_path):
    from app.providers.geocoding.amap import AmapGeocodingProvider
    from app.location.geocoding import AmapGeocoder
    store, run = execution(tmp_path)
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={"status":"1","geocodes":[{"formatted_address":"测试园区一号楼",
            "level":"门牌号","location":"113.25,23.12"}]})
    geocoder = AmapGeocoder("test-only",transport=httpx.MockTransport(respond))
    geocoder._provider = RecoverableProvider(geocoder._provider,run)
    first = geocoder.lookup_detailed("测试园区一号楼")
    second = geocoder.lookup_detailed("测试园区一号楼")
    assert first == second
    assert first.status == "resolved"
    assert len(calls) == 1
    assert store.describe("j1")["steps"][0]["name"] == "项目地址解析"
