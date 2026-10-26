"""Atomic revisions, immutable source bytes, loop checkpoints and audit events."""
from contextlib import contextmanager, nullcontext
import json
import sqlite3
from copy import deepcopy
from app.storage import CaseRepository
from app.storage.revisions import assert_review_current, initialize
from app.contracts import UnderwritingCase
from app.pipeline.recovery import pack, unpack
from .engine import now


class Conflict(ValueError):
    pass


class Store:
    def __init__(self, path=None):
        self.repo = CaseRepository(path)

    @contextmanager
    def db(self):
        self.repo.database_path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.repo.database_path, timeout=30)
        con.row_factory = sqlite3.Row
        self.repo._initialize(con)
        initialize(con)
        con.executescript('''
            CREATE TABLE IF NOT EXISTS verification_checkpoints(case_id TEXT PRIMARY KEY, fingerprint TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS verification_sources(case_id TEXT, material_id TEXT, payload TEXT, PRIMARY KEY(case_id,material_id));
            CREATE TABLE IF NOT EXISTS verification_history(id INTEGER PRIMARY KEY, case_id TEXT, version INTEGER, event TEXT, actor TEXT, at TEXT, payload TEXT);
            CREATE TABLE IF NOT EXISTS verification_jobs(id TEXT PRIMARY KEY, case_id TEXT, status TEXT, payload TEXT, error TEXT, created_at TEXT);
        ''')
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def archive_sources(self, case_id, inputs, connection=None):
        with (nullcontext(connection) if connection is not None else self.db()) as con:
            for item in inputs:
                payload = json.dumps(pack(item), ensure_ascii=False)
                existing = con.execute('SELECT payload FROM verification_sources WHERE case_id=? AND material_id=?', (case_id,item.material.material_id)).fetchone()
                if existing and unpack(json.loads(existing[0])).content != item.content:
                    raise Conflict('材料编号已存在；替换材料必须使用新编号，原件不能覆盖。')
                con.execute('INSERT OR IGNORE INTO verification_sources VALUES (?,?,?)', (case_id,item.material.material_id,payload))

    def sources(self, case_id):
        with self.db() as con:
            return [unpack(json.loads(r[0])) for r in con.execute('SELECT payload FROM verification_sources WHERE case_id=? ORDER BY material_id',(case_id,))]

    def checkpoint(self, case, fingerprint=''):
        with self.db() as con:
            con.execute('INSERT OR REPLACE INTO verification_checkpoints VALUES (?,?,?)', (case.case_id,fingerprint,case.model_dump_json()))

    def load_checkpoint(self, case_id, fingerprint=None):
        with self.db() as con:
            row = con.execute('SELECT * FROM verification_checkpoints WHERE case_id=?',(case_id,)).fetchone()
        if row and (fingerprint is None or row['fingerprint']==fingerprint):
            return UnderwritingCase.model_validate_json(row['payload'])

    def current(self, con, case_id, version, *, allow_busy=False):
        assert_review_current(con, case_id)
        row = con.execute('SELECT case_json FROM cases WHERE case_id=?',(case_id,)).fetchone()
        if not row:
            raise Conflict('案件不存在')
        case = UnderwritingCase.model_validate_json(row[0])
        if not case.verification or case.verification['version'] != version:
            raise Conflict('报告版本已变化，请刷新后操作。')
        if not allow_busy and con.execute("SELECT 1 FROM verification_jobs WHERE case_id=? AND status IN ('queued','running')",(case_id,)).fetchone():
            raise Conflict('本案正在补材核验，请等待完成。')
        return case

    def write(self, con, case, event, actor, detail=None):
        old = con.execute('SELECT case_json FROM cases WHERE case_id=?',(case.case_id,)).fetchone()
        con.execute('INSERT INTO verification_history(case_id,version,event,actor,at,payload) VALUES (?,?,?,?,?,?)',
            (case.case_id,case.verification['version'],event,actor,now(),json.dumps({'before':json.loads(old[0]) if old else None,'after':case.model_dump(mode='json'),'detail':detail},ensure_ascii=False)))
        con.execute("UPDATE cases SET case_json=?, decision=?, system_pricing_recommendation=?, review_status='pending', final_decision=NULL, reviewer_name=NULL, review_comment=NULL, reviewed_at=NULL WHERE case_id=?",
            (case.model_dump_json(),case.decision.decision.value,case.decision.pricing_recommendation,case.case_id))

    def history(self, case_id):
        with self.db() as con:
            return [dict(r) for r in con.execute('SELECT id,version,event,actor,at,payload FROM verification_history WHERE case_id=? ORDER BY id',(case_id,))]

    def job(self, id_):
        with self.db() as con:
            row = con.execute('SELECT id,case_id,status,error FROM verification_jobs WHERE id=?',(id_,)).fetchone()
        return dict(row) if row else None

    def recover_interrupted(self):
        with self.db() as con:
            con.execute("UPDATE verification_jobs SET status='failed',error='服务已重启；原件与检查点已保存，可恢复本次补材任务。' WHERE status IN ('queued','running')")
