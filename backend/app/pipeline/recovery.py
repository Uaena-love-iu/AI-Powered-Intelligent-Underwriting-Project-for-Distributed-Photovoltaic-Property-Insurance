"""Durable, job-scoped provider checkpoints and actionable, sanitised incidents.

Only successful calls are reused. Keys cover inputs, rules, runtime prompts and
provider settings. Retry recomputes downstream rules; it never reuses a decision.
"""
import base64
import hashlib
import json
import sqlite3
from dataclasses import asdict, is_dataclass
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep

import httpx
from pydantic import BaseModel, ValidationError

from app import contracts
from app.config.llm_runtime import read_runtime
from app.providers.common import MaterialInput, ProviderError
from app.providers.vision.base import VisionAnalysis
from app.providers.geocoding.amap import GeocodeCandidate
from app.storage import CaseRepository


class JobStopped(Exception):
    pass


class JobDeadline(Exception):
    pass


class InconsistentOutput(ProviderError):
    pass


def classify_error(error):
    chain, current = [], error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    code, message, action, owner, retry = (
        "SERVICE_ERROR", "服务未能完成处理", "检查服务处理记录后重试，或交由人工核验", "技术支持", False)
    for exc in chain:
        if getattr(exc, "code", None) == "provider_rejected":
            code, message, action, owner, retry = "SERVICE_CONFIG", "地址解析服务拒绝请求", "检查地图服务密钥、权限或配额后重试", "配置管理员", False
            break
        if isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
            if status in (401, 403):
                code, message, action, owner, retry = "SERVICE_AUTH", "服务密钥或访问权限异常", "修正服务密钥或权限后重试", "配置管理员", False
            elif status == 429 or status >= 500:
                code, message, action, owner, retry = "SERVICE_BUSY", "服务限流或暂时不可用", "稍后重试失败步骤", "系统／技术支持", True
            else:
                code, message, action, owner, retry = "SERVICE_CONFIG", "服务请求或配置不兼容", "检查模型名称、接口地址和请求配置后重试", "配置管理员", False
            break
        if isinstance(exc, (httpx.TimeoutException, TimeoutError, JobDeadline)):
            code, message, action, owner, retry = "SERVICE_TIMEOUT", "执行超时", "重试未完成步骤；已成功的有效结果保留", "系统／技术支持", True
            break
        if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError, ConnectionError)):
            code, message, action, owner, retry = "NETWORK_ERROR", "服务连接中断", "检查网络后重试失败步骤", "系统／技术支持", True
            break
        if isinstance(exc, (ValidationError, json.JSONDecodeError, InconsistentOutput)):
            code, message, action, owner, retry = "MODEL_OUTPUT_INVALID", "模型输出缺失、格式异常或前后矛盾", "有限重试仍失败时，人工核验原材料；不据此要求客户补拍", "核保复核人员", True
            break
    return dict(code=code, message=message, action=action, owner=owner, auto_retry=retry)


def pack(value):
    if isinstance(value, GeocodeCandidate):
        return {"$geocode": asdict(value)}
    if isinstance(value, MaterialInput):
        return {"$material": value.material.model_dump(mode="json"),
                "content": base64.b64encode(value.content).decode(), "ocr_task": value.ocr_task}
    if isinstance(value, BaseModel):
        return {"$model": type(value).__name__, "data": value.model_dump(mode="json")}
    if isinstance(value, (list, tuple)):
        return [pack(item) for item in value]
    if isinstance(value, dict):
        return {str(key): pack(item) for key, item in value.items()}
    return value


def unpack(value):
    if isinstance(value, list):
        return [unpack(item) for item in value]
    if isinstance(value, dict):
        if "$geocode" in value:
            return GeocodeCandidate(**value["$geocode"])
        if "$material" in value:
            return MaterialInput(contracts.Material.model_validate(value["$material"]),
                                 base64.b64decode(value["content"]), value["ocr_task"])
        if "$model" in value:
            # A fixed local contract registry, never import a module from saved data.
            cls = VisionAnalysis if value["$model"] == "VisionAnalysis" else getattr(contracts, value["$model"], None)
            if not isinstance(cls, type) or not issubclass(cls, BaseModel):
                raise ValueError("Unknown checkpoint contract")
            return cls.model_validate(value["data"])
        return {key: unpack(item) for key, item in value.items()}
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def provider_config(provider):
    """Include routing delegates and stage settings, without persisting secrets."""
    settings = getattr(provider, "_settings", None)
    settings = settings.model_dump(mode="json") if isinstance(settings, BaseModel) else asdict(settings) if is_dataclass(settings) else settings
    return {"name": getattr(provider, "name", type(provider).__name__), "model": getattr(provider, "model_name", None),
            "evidence_cache_token": getattr(provider, "evidence_cache_token", None),
            "settings": settings, "prompt": getattr(provider, "_system_prompt", None),
            "endpoint": getattr(provider, "_api_key", None),
            "delegates": {key: provider_config(value) for key, value in vars(provider).items()
                          if key in ("_delegate", "_filing_delegate", "_installation_delegate", "_geometry_delegate")}}


class RecoveryStore:
    def __init__(self, path=None):
        case_path = CaseRepository().database_path
        self.path = Path(path) if path else case_path.with_name(case_path.stem + "-recovery.sqlite3")

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript('''
              CREATE TABLE IF NOT EXISTS recovery_runs (
                job_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                request_json TEXT NOT NULL, cancel_requested INTEGER DEFAULT 0,
                child_id TEXT, created_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS recovery_calls (
                root_id TEXT, call_key TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(root_id, call_key));
              CREATE TABLE IF NOT EXISTS recovery_steps (
                job_id TEXT, call_key TEXT, info TEXT NOT NULL,
                PRIMARY KEY(job_id, call_key));
              CREATE TABLE IF NOT EXISTS recovery_events (
                root_id TEXT, event_key TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(root_id, event_key));
            ''')
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, job_id, request):
        with self.db() as db:
            db.execute("INSERT INTO recovery_runs(job_id,root_id,attempt,request_json,created_at) VALUES(?,?,0,?,?)",
                       (job_id, job_id, json.dumps(pack(request), ensure_ascii=False), datetime.now(UTC).isoformat()))

    def get(self, job_id):
        with self.db() as db:
            row = db.execute("SELECT * FROM recovery_runs WHERE job_id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def claim_retry(self, parent_id, child_id):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM recovery_runs WHERE job_id=?", (parent_id,)).fetchone()
            if not row:
                raise ValueError("此历史任务没有恢复数据，请重新提交材料")
            if row["child_id"]:
                return row["child_id"], False
            if row["attempt"] >= 3:
                raise ValueError("已达到本轮三次人工重试上限，请人工核验或修正材料后新建一轮")
            db.execute("INSERT INTO recovery_runs(job_id,root_id,attempt,request_json,created_at) VALUES(?,?,?,?,?)",
                       (child_id, row["root_id"], row["attempt"] + 1, row["request_json"], datetime.now(UTC).isoformat()))
            db.execute("UPDATE recovery_runs SET child_id=? WHERE job_id=?", (child_id, parent_id))
            return child_id, True

    def cancel(self, job_id):
        with self.db() as db:
            db.execute("UPDATE recovery_runs SET cancel_requested=1 WHERE job_id=?", (job_id,))

    def abandon_claim(self, parent_id, child_id):
        with self.db() as db:
            db.execute("UPDATE recovery_runs SET child_id=NULL WHERE job_id=? AND child_id=?", (parent_id, child_id))
            db.execute("DELETE FROM recovery_runs WHERE job_id=?", (child_id,))

    def purge_expired(self, days=7):
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        with self.db() as db:
            roots = [row[0] for row in db.execute("SELECT root_id FROM recovery_runs GROUP BY root_id HAVING MAX(created_at) < ?", (cutoff,))]
            for root in roots:
                db.execute("DELETE FROM recovery_steps WHERE job_id IN (SELECT job_id FROM recovery_runs WHERE root_id=?)", (root,))
                db.execute("DELETE FROM recovery_calls WHERE root_id=?", (root,))
                db.execute("DELETE FROM recovery_events WHERE root_id=?", (root,))
                db.execute("DELETE FROM recovery_runs WHERE root_id=?", (root,))
        return len(roots)

    def cached(self, root_id, key):
        with self.db() as db:
            row = db.execute("SELECT payload FROM recovery_calls WHERE root_id=? AND call_key=?", (root_id, key)).fetchone()
            return unpack(json.loads(row[0])) if row else None

    def checkpoint(self, root_id, key, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO recovery_calls VALUES(?,?,?)", (root_id, key, json.dumps(pack(value), ensure_ascii=False)))

    def step(self, job_id, key, info):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO recovery_steps VALUES(?,?,?)", (job_id, key, json.dumps(info, ensure_ascii=False)))

    def event(self, job_id, category, message):
        run = self.get(job_id)
        if not run:
            return
        event = dict(event_id=run["root_id"] + ":" + category, job_id=job_id, category=category,
                     message=message, created_at=datetime.now(UTC).isoformat(),
                     url="/api/v1/underwriting/jobs/" + job_id)
        with self.db() as db:
            # One actionable notification per category per recovery lineage.
            db.execute("INSERT OR IGNORE INTO recovery_events VALUES(?,?,?)", (run["root_id"], category, json.dumps(event, ensure_ascii=False)))

    def describe(self, job_id):
        run = self.get(job_id)
        if not run:
            return None
        with self.db() as db:
            steps = [json.loads(row[0]) for row in db.execute("SELECT info FROM recovery_steps WHERE job_id=? ORDER BY rowid", (job_id,))]
            events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM recovery_events WHERE root_id=? ORDER BY rowid", (run["root_id"],))]
        return dict(attempt=run["attempt"], child_id=run["child_id"], cancel_requested=bool(run["cancel_requested"]),
                    steps=steps, events=events, retained=sum(item["status"] in ("success", "reused") for item in steps),
                    issues=[item for item in steps if item["status"] == "failed"], retry_limit=3)


LABELS = {"extract": "文字提取", "analyze_with_watermark": "图片风险识别",
          "analyze_installation": "安装类型组图识别", "estimate_geometry": "安装几何估计",
          "lookup_materials": "组件型号与参数查询", "lookup": "组件参数查询"}


class JobExecution:
    def __init__(self, job_id, store=None, deadline_seconds=1800):
        self.store = store or RecoveryStore()
        self.job_id = job_id
        self.root_id = self.store.get(job_id)["root_id"]
        self.deadline = monotonic() + deadline_seconds

    def check(self):
        if self.store.get(self.job_id)["cancel_requested"]:
            raise JobStopped()
        if monotonic() > self.deadline:
            raise JobDeadline()

    def call(self, provider, method, args, kwargs):
        self.check()
        settings = getattr(provider, "_settings", None)
        if isinstance(settings, BaseModel):
            settings = settings.model_dump(mode="json")
        elif is_dataclass(settings):
            settings = asdict(settings)
        prompt_root = Path(__file__).resolve().parents[1] / "prompts"
        prompt_version = digest({str(path.relative_to(prompt_root)): path.read_text(encoding="utf-8")
                                 for path in sorted(prompt_root.rglob("*.txt"))})
        key = digest(dict(revision="recovery-v1", provider=getattr(provider, "name", type(provider).__name__), method=method,
                          config=provider_config(provider),
                          model=getattr(provider, "model_name", None), settings=settings, prompt_version=prompt_version,
                          system_prompt=getattr(provider, "_system_prompt", None), runtime=read_runtime(),
                          rules=getattr(getattr(provider, "rule_policy", None), "snapshot", None),
                          args=pack(args), kwargs=pack(kwargs)))
        ids = []
        def collect(items):
            for item in items:
                if isinstance(item, MaterialInput):
                    ids.append(item.material.material_id)
                elif isinstance(item, (list, tuple)):
                    collect(item)
        collect(args)
        label = LABELS[method]
        if type(provider).__name__ == "AmapGeocodingProvider":
            label = "项目地址解析"
        if method == "extract" and args:
            label += " · " + {"watermark": "拍摄水印", "business": "业务文字"}.get(args[0].ocr_task, "通用")
        info = dict(key=key, name=label, material_ids=ids, status="running", attempts=0)
        cached = self.store.cached(self.root_id, key)
        if cached is not None:
            if "last_result" in cached:
                provider.last_result = cached["last_result"]
            self.store.step(self.job_id, key, {**info, "status": "reused"})
            return cached["output"]
        for attempt in range(2):
            self.check()
            info.update(status="running", attempts=attempt + 1)
            self.store.step(self.job_id, key, info)
            try:
                output = getattr(provider, method)(*args, **kwargs)
                if isinstance(output, VisionAnalysis):
                    from app.rules.quality_evidence import quality_evidence_conflicts
                    if any(quality_evidence_conflicts(finding) for finding in output.findings):
                        raise InconsistentOutput()
                component_result = getattr(provider, "last_result", {})
                if method in ("lookup", "lookup_materials") and (component_result.get("status") == "source_unavailable" or
                        any(item.get("recognition_status") == "source_unavailable"
                        for item in component_result.get("recognition", []))):
                    raise ProviderError("Component recognition service unavailable")
                payload = {"output": output}
                if hasattr(provider, "last_result"):
                    payload["last_result"] = provider.last_result
                self.store.checkpoint(self.root_id, key, payload)
                self.store.step(self.job_id, key, {**info, "status": "success"})
                self.check()
                return output
            except (JobStopped, JobDeadline):
                raise
            except Exception as exc:
                issue = classify_error(exc)
                if attempt == 0 and issue["auto_retry"]:
                    self.store.step(self.job_id, key, {**info, "status": "retrying", **issue})
                    sleep(0.4)
                    continue
                self.store.step(self.job_id, key, {**info, "status": "failed", **issue})
                wrapped = ProviderError(issue["message"])
                wrapped.code = getattr(exc, "code", issue["code"])
                wrapped.public_message = issue["message"] + "；" + issue["action"]
                raise wrapped from exc


class RecoverableProvider:
    manages_retries = True

    def __init__(self, provider, execution):
        object.__setattr__(self, "_provider", provider)
        object.__setattr__(self, "_execution", execution)

    def __getattr__(self, name):
        value = getattr(self._provider, name)
        if name in LABELS and callable(value):
            return lambda *args, **kwargs: self._execution.call(self._provider, name, args, kwargs)
        return value

    def __setattr__(self, name, value):
        setattr(self._provider, name, value)
