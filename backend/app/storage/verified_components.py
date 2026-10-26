"""Versioned, revocable source-parameter reuse; never an underwriting exemption."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import uuid4

from app.contracts import ComponentProfile
from app.storage.cases import CaseRepository

SCOPE = "exact_model_source_parameters"
FIELDS = {"rated_power_w": ("额定功率", "W"),
          "front_static_load_pa": ("正面机械载荷", "Pa"),
          "back_static_load_pa": ("背面机械载荷", "Pa")}


def now():
    return datetime.now(UTC)


def model_key(value):
    # Do not collapse punctuation, suffixes, power bins, or internal whitespace.
    return value.strip().casefold()


class VerifiedComponentConflict(ValueError):
    pass


class VerifiedComponentRepository:
    def __init__(self, path=None):
        self.path = path or CaseRepository().database_path

    @contextmanager
    def db(self):
        from pathlib import Path
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("""CREATE TABLE IF NOT EXISTS verified_components (
                id TEXT PRIMARY KEY, model_key TEXT NOT NULL, version INTEGER NOT NULL,
                payload TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, revoke_reason TEXT,
                UNIQUE(model_key, version))""")
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def decode(row, instant):
        data = json.loads(row["payload"])
        data.update(revoked_at=row["revoked_at"], revoked_by=row["revoked_by"], revoke_reason=row["revoke_reason"])
        data["status"] = ("revoked" if row["revoked_at"] else
                          "expired" if datetime.fromisoformat(data["expires_at"]) <= instant else
                          "scheduled" if datetime.fromisoformat(data["valid_from"]) > instant else "active")
        return data

    def list(self, model=None):
        with self.db() as con:
            rows = con.execute("SELECT * FROM verified_components" + (" WHERE model_key=?" if model else "") +
                               " ORDER BY rowid DESC", (model_key(model),) if model else ()).fetchall()
        instant = now()
        return [self.decode(row, instant) for row in rows]

    def create(self, submission, actor):
        data = submission.model_dump(mode="json")
        key, instant = model_key(data["component_model"]), now()
        if datetime.fromisoformat(data["expires_at"]) <= instant:
            raise VerifiedComponentConflict("有效期已经结束，请重新设置")
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            rows = con.execute("SELECT * FROM verified_components WHERE model_key=?", (key,)).fetchall()
            for row in rows:
                old = self.decode(row, instant)
                if not old["revoked_at"] and (datetime.fromisoformat(old["valid_from"]) < submission.expires_at and
                                              submission.valid_from < datetime.fromisoformat(old["expires_at"])):
                    raise VerifiedComponentConflict("该完整型号已有重叠有效期的记录，请先撤销旧记录或调整有效期")
            data.update(id="VC-" + uuid4().hex[:16], version=max([r["version"] for r in rows], default=0)+1,
                        scope=SCOPE, approved_by=actor, approved_at=instant.isoformat())
            con.execute("INSERT INTO verified_components(id,model_key,version,payload) VALUES(?,?,?,?)",
                        (data["id"], key, data["version"], json.dumps(data, ensure_ascii=False)))
        return next(item for item in self.list(data["component_model"]) if item["id"] == data["id"])

    def revoke(self, id_, actor, reason):
        with self.db() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM verified_components WHERE id=?", (id_,)).fetchone()
            if not row:
                raise KeyError(id_)
            if row["revoked_at"]:
                raise VerifiedComponentConflict("该记录已撤销，原撤销信息已保留")
            con.execute("UPDATE verified_components SET revoked_at=?,revoked_by=?,revoke_reason=? WHERE id=?",
                        (now().isoformat(), actor, reason, id_))
        return next(item for item in self.list() if item["id"] == id_)

    def cache_token(self):
        # Active -> expired and scheduled -> active must invalidate successful retry checkpoints too.
        return [(r["id"], r["status"]) for r in self.list()]

    def lookup(self, model):
        active = [r for r in self.list(model) if r["status"] == "active"]
        return self.profile(active[0]) if len(active) == 1 else None

    @staticmethod
    def profile(record):
        values, evidence, ui, mechanical = {}, {}, {}, {}
        for field, item in record["parameters"].items():
            label, unit = FIELDS[field]
            values[field] = item["value"]
            evidence[field] = [dict(status="found", value=item["value"], unit=unit,
                quote=item["quote"], source_url=item["source_url"], document_title=record["document_title"],
                load_basis=item["load_basis"], applicability=record["applicability"],
                human_verification=dict(record_id=record["id"], version=record["version"],
                                        reviewer=record["approved_by"], reviewed_at=record["approved_at"]))]
            (ui if field == "rated_power_w" else mechanical)[field] = dict(label=label, value=item["value"],
                unit=unit, status="found", load_basis=item["load_basis"], value_origin="verified_source")
        note = "仅复用完整型号的来源参数；安装条件、位置、风雪及其他规则仍需独立核验，不代表核保通过。"
        profile = ComponentProfile(component_model=record["component_model"], manufacturer=record["manufacturer"],
            model_match_status="exact", source_name=record["document_title"],
            source_url=next(iter(record["parameters"].values()))["source_url"], source_document_type="人工核验规格书",
            source_note=record["applicability"], retrieved_at=now(), match_confidence=1,
            parameter_sources={f: p["source_url"] for f, p in record["parameters"].items()},
            parameter_evidence={f: dict(status="found", value=values[f], records=es) for f, es in evidence.items()},
            lookup_notes=[note, "来源适用条件：" + record["applicability"]], **values)
        raw = {**profile.model_dump(mode="json"), "parameter_evidence": evidence,
               "ui_fields": ui, "mechanical_fields": mechanical}
        raw.pop("workbench_result", None)
        profile.workbench_result = dict(status="verified_reuse", profile=raw,
            verified_reuse={**record, "used_at": now().isoformat(), "notice": note})
        return profile
