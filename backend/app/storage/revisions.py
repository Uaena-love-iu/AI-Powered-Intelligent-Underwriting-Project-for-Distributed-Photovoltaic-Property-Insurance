"""Immutable underwriting rounds, source snapshots and human correction audit."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

from app.storage.cases import CaseRepository


class RevisionConflict(ValueError):
    pass


def initialize(connection):
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS case_rounds (
            case_id TEXT PRIMARY KEY, root_id TEXT NOT NULL, round_number INTEGER NOT NULL,
            parent_id TEXT, job_id TEXT, created_at TEXT NOT NULL,
            actor TEXT NOT NULL, reason TEXT NOT NULL, corrections_json TEXT NOT NULL,
            request_json TEXT, UNIQUE(root_id, round_number)
        );
        CREATE INDEX IF NOT EXISTS idx_case_rounds_root ON case_rounds(root_id, round_number);
    """)


def assert_review_current(connection, case_id):
    row = connection.execute("SELECT root_id, round_number FROM case_rounds WHERE case_id=?", (case_id,)).fetchone()
    if row and connection.execute("SELECT 1 FROM case_rounds WHERE root_id=? AND round_number>?", tuple(row)).fetchone():
        raise RevisionConflict("已有新一轮核保，旧轮次仅供历史追溯；请完成最新一轮后再审核。")
    active = connection.execute("SELECT 1 FROM case_rounds r LEFT JOIN analysis_jobs j ON j.job_id=r.job_id WHERE r.case_id=? AND r.job_id IS NOT NULL AND (j.status IS NULL OR j.status IN ('queued','running'))", (case_id,)).fetchone()
    if active:
        raise RevisionConflict('本轮分析尚未完成，请等待任务结束后审核。')


def changes(before, after):
    return [{"field": key, "before": before.get(key), "after": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key) != after.get(key)]


def compare_cases(before, after):
    old = {m['material_id']: m for m in before.get('materials', [])}
    new = {m['material_id']: m for m in after.get('materials', [])}
    materials = []
    for key in sorted(old.keys() | new.keys()):
        a, b = old.get(key), new.get(key)
        action = 'added' if a is None else 'removed' if b is None else 'replaced' if a.get('sha256') != b.get('sha256') else 'reclassified' if any(a.get(f) != b.get(f) for f in ('category', 'additional_categories', 'capture_view')) else None
        if action:
            materials.append(dict(material_id=key, action=action, before=a, after=b))
    def risks(case):
        return sorted({r for s in case.get('stage_reviews', []) for r in s.get('rule_ids', [])}
                      | set((case.get('decision') or {}).get('decisive_rule_ids', [])))
    a, b = set(risks(before)), set(risks(after))
    from app.rules.explanations import explain_rule
    def rules(case):
        return {k:v for k,v in case.get('ruleset_snapshot', {}).items() if k not in ('snapshot_id','created_at')}
    def coverage(case):
        return {r['name']+' / '+r['area_id']:dict(status=r['status'], targets={t['label']:t['status'] for t in r['targets']}) for r in case.get('evidence_coverage',{}).get('rows',[])}
    return dict(evidence_coverage_changes=changes(coverage(before),coverage(after)), strategy_before=before.get('strategy_assessment', {}), strategy_after=after.get('strategy_assessment', {}),
                materials=materials, parameters=changes(before.get('project', {}), after.get('project', {})),
                component_parameters=changes({k:(before.get('component_profile') or {}).get(k) for k in ('rated_power_w','front_static_load_pa','back_static_load_pa')},
                                             {k:(after.get('component_profile') or {}).get(k) for k in ('rated_power_w','front_static_load_pa','back_static_load_pa')}),
                component_evidence_changed=before.get('component_review', {}) != after.get('component_review', {}),
                rules_changed=rules(before) != rules(after),
                risks_added=sorted(b-a), risks_removed=sorted(a-b),
                risk_labels={r:explain_rule(r).title for r in a|b},
                decision_before=before.get('decision'), decision_after=after.get('decision'),
                comparison_note='规则变化为前后输出差异；应结合两轮证据与执行范围核验，不代表已自动确定因果或排除风险。')


class RevisionRepository:
    def __init__(self):
        self.path = CaseRepository().database_path

    @contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        CaseRepository._initialize(con)
        initialize(con)
        try:
            yield con
        finally:
            con.close()

    def reserve(self, case_id, job_id, request, context=None):
        from app.pipeline.recovery import pack
        context = context or {}
        parent_id = context.get('parent_case_id')
        now = datetime.now(UTC).isoformat()
        with self.db() as con:
            with con:
                con.execute('BEGIN IMMEDIATE')
                if con.execute('SELECT 1 FROM cases WHERE case_id=? UNION SELECT 1 FROM case_rounds WHERE case_id=?', (case_id, case_id)).fetchone():
                    raise RevisionConflict('本轮编号已存在，请刷新任务状态。')
                root, number = case_id, 1
                corrections = []
                if parent_id:
                    parent = con.execute('SELECT case_json, stored_at FROM cases WHERE case_id=?', (parent_id,)).fetchone()
                    if parent is None:
                        raise RevisionConflict('上一轮案件不存在或尚未完成。')
                    prior = json.loads(parent['case_json'])
                    previous = con.execute('SELECT * FROM case_rounds WHERE case_id=?', (parent_id,)).fetchone()
                    if not previous:
                        con.execute('INSERT INTO case_rounds VALUES (?,?,?,?,?,?,?,?,?,?)',
                                    (parent_id, parent_id, 1, None, None, parent['stored_at'], '', '历史案件', '[]', None))
                        previous = con.execute('SELECT * FROM case_rounds WHERE case_id=?', (parent_id,)).fetchone()
                    root = previous['root_id']
                    latest = con.execute('SELECT r.case_id FROM case_rounds r JOIN cases c ON c.case_id=r.case_id WHERE r.root_id=? ORDER BY round_number DESC LIMIT 1', (root,)).fetchone()
                    if latest['case_id'] != parent_id:
                        raise RevisionConflict('当前为历史轮次，请打开最新一轮补材，避免覆盖后续修改。')
                    active = con.execute("SELECT 1 FROM case_rounds r LEFT JOIN analysis_jobs j ON j.job_id=r.job_id WHERE r.root_id=? AND r.job_id IS NOT NULL AND (j.status IS NULL OR j.status IN ('queued','running'))", (root,)).fetchone()
                    if active:
                        raise RevisionConflict('本案件已有任务执行中，请先查看原任务。')
                    number = con.execute('SELECT MAX(round_number)+1 FROM case_rounds WHERE root_id=?', (root,)).fetchone()[0]
                    for correction in context.get('corrections', []):
                        key = correction['field']
                        material_id = correction['material_id']
                        source = next((m for m in request['materials'] if m.material.material_id==material_id), None)
                        if not source or source.material.category.value not in ('component_nameplate', 'equipment_inventory'):
                            raise RevisionConflict('请选择本轮的组件铭牌或设备清单作为型号修正依据。')
                        after = getattr(request['project'], key)
                        if after is None or isinstance(after, str) and not after.strip():
                            raise RevisionConflict('人工修正值不能为空；删除项目输入请使用普通资料修改。')
                        before = prior.get('project', {}).get(key)
                        if key == 'component_model':
                            before = (prior.get('component_profile') or {}).get('component_model') or before
                            source_field = next((f for f in prior.get('ocr_fields', []) if f['material_id']==material_id and f['field_name']==key), None)
                            if source_field:
                                before = source_field.get('normalized_value') or source_field.get('raw_value')
                        original = next((c.get('ai_original') for c in prior.get('revision', {}).get('corrections', []) if c['field']==key), before)
                        if context.get('reuse_corrections'):
                            inherited = next((c for c in prior.get('revision', {}).get('corrections', [])
                                              if c['field']==key and c.get('material_id')==material_id and c.get('after')==after), None)
                            if inherited:
                                corrections.append({**inherited, 'reused_from_case_id': parent_id, 'reused_at': now})
                                continue
                        corrections.append({**correction, 'before': before, 'after': after,
                                            'actor': context['actor'], 'at': now,
                                            'ai_original': original, 'source_case_id': parent_id})
                elif context.get('corrections'):
                    raise RevisionConflict('人工修正必须关联上一轮结果。')
                con.execute('INSERT INTO case_rounds VALUES (?,?,?,?,?,?,?,?,?,?)',
                            (case_id, root, number, parent_id, job_id, now, context.get('actor',''),
                             context.get('reason','首次分析'), json.dumps(corrections, ensure_ascii=False),
                             json.dumps(pack(request), ensure_ascii=False)))
        return self.get(case_id)

    def abandon(self, case_id):
        with self.db() as con, con:
            con.execute('DELETE FROM case_rounds WHERE case_id=? AND NOT EXISTS (SELECT 1 FROM analysis_jobs WHERE case_id=?)', (case_id, case_id))

    def reconcile_orphans(self):
        """A crash between round reservation and job creation must not lock the case."""
        with self.db() as con, con:
            con.execute("""INSERT OR IGNORE INTO analysis_jobs
                (job_id,case_id,status,progress_percent,current_step,created_at,updated_at,error_code)
                SELECT job_id,case_id,'failed',0,'提交过程中服务中断，请重新提交本轮材料',created_at,?, 'SERVER_RESTARTED'
                FROM case_rounds WHERE job_id IS NOT NULL""", (datetime.now(UTC).isoformat(),))

    def get(self, case_id):
        with self.db() as con:
            row = con.execute('SELECT * FROM case_rounds WHERE case_id=?', (case_id,)).fetchone()
            if not row:
                return None
            data = dict(row)
            data['corrections'] = json.loads(data.pop('corrections_json'))
            data['source_available'] = bool(data.pop('request_json'))
            latest = con.execute('SELECT case_id FROM case_rounds WHERE root_id=? ORDER BY round_number DESC LIMIT 1', (data['root_id'],)).fetchone()
            data['is_current'] = latest['case_id'] == case_id
            data['latest_case_id'] = latest['case_id']
            return data

    def history(self, case_id):
        item = self.get(case_id)
        if not item:
            return []
        with self.db() as con:
            ids = [r[0] for r in con.execute('SELECT case_id FROM case_rounds WHERE root_id=? ORDER BY round_number', (item['root_id'],))]
            result = []
            for id_ in ids:
                row = con.execute('SELECT case_json, final_decision, reviewer_name, review_comment, reviewed_at FROM cases WHERE case_id=?', (id_,)).fetchone()
                entry = self.get(id_)
                job = con.execute('SELECT status, current_step FROM analysis_jobs WHERE job_id=?', (entry['job_id'],)).fetchone()
                entry['status'] = job['status'] if job else 'completed' if row else 'unknown'
                if row:
                    case = json.loads(row['case_json'])
                    entry.update(ai_decision=case.get('decision'), human_decision=row['final_decision'], reviewer_name=row['reviewer_name'], review_comment=row['review_comment'], reviewed_at=row['reviewed_at'])
                    parent = con.execute('SELECT case_json FROM cases WHERE case_id=?', (entry['parent_id'],)).fetchone()
                    entry['comparison'] = compare_cases(json.loads(parent[0]), case) if parent else None
                    # Compare submitted parameter overrides as well as derived project facts.
                    request = self.request(id_)
                    old_request = self.request(entry['parent_id']) if entry['parent_id'] else None
                    if entry['comparison'] and request and old_request:
                        entry['comparison']['screening_changes'] = changes(old_request.get('pv_load_screening_input') or {}, request.get('pv_load_screening_input') or {})
                result.append(entry)
            return result

    def request(self, case_id):
        from app.pipeline.recovery import unpack
        with self.db() as con:
            row = con.execute('SELECT request_json FROM case_rounds WHERE case_id=?', (case_id,)).fetchone()
            return unpack(json.loads(row[0])) if row and row[0] else None


class CorrectedComponentProvider:
    """Use an explicitly reviewed model for a fresh lookup, with audit kept on the round."""
    def __init__(self, delegate, correction):
        self.delegate, self.correction = delegate, correction
        self.last_result = {}

    def __getattr__(self, key):
        return getattr(self.delegate, key)

    def lookup_materials(self, project, materials):
        profile = self.delegate.lookup(project.component_model)
        self.last_result = {**getattr(self.delegate, 'last_result', {}), 'human_model_correction': self.correction}
        if profile:
            profile.workbench_result = self.last_result
        return profile


class CorrectedOcrProvider:
    """Apply a reviewed field only to its named source; keep raw model fields separate."""
    def __init__(self, delegate, corrections, project):
        object.__setattr__(self, 'delegate', delegate)
        object.__setattr__(self, 'corrections', corrections)
        object.__setattr__(self, 'project', project)
        object.__setattr__(self, 'originals', {})
        from threading import Lock
        object.__setattr__(self, 'lock', Lock())

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def __setattr__(self, name, value):
        setattr(self.delegate, name, value)

    def extract(self, item):
        fields = self.delegate.extract(item)
        if item.ocr_task == 'watermark':
            return fields
        from app.contracts import OcrField
        result = list(fields)
        for c in self.corrections:
            if c['material_id'] != item.material.material_id:
                continue
            selected = [f for f in fields if f.field_name==c['field']]
            with self.lock:
                for f in selected:
                    self.originals[f.field_id] = f.model_copy(deep=True)
            result = [f for f in result if f.field_name!=c['field']]
            value = str(getattr(self.project,c['field']))
            result.append(OcrField(field_id=(selected[0].field_id if selected else 'human-'+item.material.material_id+'-'+c['field']),
                material_id=item.material.material_id, extraction_task='business', field_name=c['field'],
                raw_value=value, normalized_value=value, value_status='extracted', confidence=1,
                provider='human-review', model='human-correction-v1', evidence_text='人工核验依据：'+c['evidence']))
        return result
