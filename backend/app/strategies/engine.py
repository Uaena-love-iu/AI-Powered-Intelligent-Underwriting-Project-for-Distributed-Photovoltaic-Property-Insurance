"""Versioned underwriting treatment; original risk evidence is never mutated."""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4
import json
import sqlite3
from contextlib import contextmanager
from pydantic import AwareDatetime, Field, model_validator
from app.contracts.common import ContractModel
from app.storage.cases import CaseRepository

ACTIONS = {'notify':'风险提示','request_more':'补材建议','manual_review':'人工复核','recommend_reject':'拒保建议','surcharge':'加费建议（比例待业务确认）'}
SOURCES = {'decision':'原 AI 核保建议','wind':'风灾风险评级','snow':'雪灾风险评级','evidence':'判断依据完整性'}
VALUES = {'decision':['accept','conditional_accept','request_more','manual_review','recommend_reject','surcharge'],
          'wind':['low','medium','high','critical','unknown'], 'snow':['low','medium','high','critical','unknown'],
          'evidence':['sufficient','incomplete','unknown']}

class Conditions(ContractModel):
    provinces: list[str] = Field(default_factory=list,max_length=30)
    cities: list[str] = Field(default_factory=list,max_length=30)
    models: list[str] = Field(default_factory=list,max_length=100)
    start_months: list[int] = Field(default_factory=list,max_length=12)
    min_capacity_kw: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    max_capacity_kw: float | None = Field(default=None,ge=0,allow_inf_nan=False)
    @model_validator(mode='after')
    def valid(self):
        for key in ('provinces','cities','models'):
            vals=getattr(self,key)
            if any(not v.strip() or any(c in v for c in '*?\n\r') for v in vals):raise ValueError('地区和型号必须填写完整名称，不支持空值或通配符')
            setattr(self,key,list(dict.fromkeys(v.strip() for v in vals)))
        if any(isinstance(m,bool) or m<1 or m>12 for m in self.start_months):raise ValueError('月份应为 1 至 12')
        if self.min_capacity_kw is not None and self.max_capacity_kw is not None and self.min_capacity_kw>self.max_capacity_kw:raise ValueError('规模下限不能大于上限')
        return self

class Rule(ContractModel):
    id: str = Field(min_length=1,max_length=60,pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1,max_length=100)
    conditions: Conditions = Field(default_factory=Conditions)
    source: Literal['decision','wind','snow','evidence']
    values: list[str] = Field(min_length=1,max_length=10)
    action: Literal['notify','request_more','manual_review','recommend_reject','surcharge']
    priority: int = Field(default=100,ge=0,le=999)
    attention_level: Literal['low','medium','high','critical'] = 'medium'
    reason: str = Field(min_length=2,max_length=1000)
    @model_validator(mode='after')
    def valid(self):
        if set(self.values)-set(VALUES[self.source]):raise ValueError('风险来源与匹配值不一致')
        if self.action in ('recommend_reject','surcharge') and ('unknown' in self.values or self.source=='evidence' and 'incomplete' in self.values or self.source=='decision' and bool(set(self.values)&{'manual_review','request_more'})):raise ValueError('未知或证据不足只能提示、补材或复核，不能据此直接建议拒保／加费')
        return self

class Scheme(ContractModel):
    base_id: str | None = None
    name: str = Field(min_length=2,max_length=100)
    company: str = Field(min_length=2,max_length=100)
    basis: str = Field(min_length=2,max_length=2000)
    reviewer_name: str = Field(min_length=1,max_length=100)
    valid_from: AwareDatetime
    expires_at: AwareDatetime
    demo: bool = True
    confirmed: Literal[True]
    rules: list[Rule] = Field(min_length=1,max_length=50)
    @model_validator(mode='after')
    def valid(self):
        if self.expires_at<=self.valid_from:raise ValueError('有效期结束必须晚于开始')
        if len({r.id for r in self.rules})!=len(self.rules):raise ValueError('策略编号不能重复')
        return self

class Selection(ContractModel):
    record_id: str = Field(min_length=1,max_length=60)

class Repository:
    @contextmanager
    def db(self):
        path=CaseRepository().database_path;path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path,timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS underwriting_strategies (id TEXT PRIMARY KEY, family TEXT NOT NULL, version INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(family,version))')
            yield db
    def list(self):
        with self.db() as db:return [self.status(json.loads(r[0])) for r in db.execute('SELECT payload FROM underwriting_strategies ORDER BY rowid DESC')]
    @staticmethod
    def status(data):
        now=datetime.now(UTC)
        data['status']='revoked' if data.get('revocation') else 'pending' if now<datetime.fromisoformat(data['valid_from']) else 'expired' if now>=datetime.fromisoformat(data['expires_at']) else 'active'
        return data
    def get(self,id_):
        with self.db() as db:r=db.execute('SELECT payload FROM underwriting_strategies WHERE id=?',(id_,)).fetchone()
        if not r:raise ValueError('承保策略版本不存在')
        return self.status(json.loads(r[0]))
    def create(self,body,actor):
        data=body.model_dump(mode='json');now=datetime.now(UTC).isoformat()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            family=uuid4().hex;version=1
            if body.base_id:
                row=db.execute('SELECT payload FROM underwriting_strategies WHERE id=?',(body.base_id,)).fetchone()
                if not row:raise ValueError('原策略版本不存在')
                base=json.loads(row[0]);family=base['family'];latest=db.execute('SELECT MAX(version) FROM underwriting_strategies WHERE family=?',(family,)).fetchone()[0]
                if base['version']!=latest:raise ValueError('已有更新版本，请从最新版本编辑')
                version=latest+1
            data.update(id=uuid4().hex,family=family,version=version,approved_by=actor,approved_at=now,revocation=None,date_basis='proposed_start_date',conflict_policy='highest_priority_tie_to_manual',pricing_mode='suggestion_only')
            db.execute('INSERT INTO underwriting_strategies VALUES (?,?,?,?)',(data['id'],family,version,json.dumps(data,ensure_ascii=False)))
        return self.status(data)
    def revoke(self,id_,actor,reason):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT payload FROM underwriting_strategies WHERE id=?',(id_,)).fetchone()
            if not row:raise ValueError('承保策略版本不存在')
            data=json.loads(row[0])
            if data.get('revocation'):raise ValueError('该版本已撤销')
            data['revocation']=dict(actor=actor,reason=reason,at=datetime.now(UTC).isoformat())
            db.execute('UPDATE underwriting_strategies SET payload=? WHERE id=?',(json.dumps(data,ensure_ascii=False),id_))
        return self.status(data)

def snapshot(selection):
    if selection is None:return {}
    record=Repository().get(selection.record_id)
    if record['status']!='active':raise ValueError('所选承保策略未生效、已到期或已撤销，请重新选择')
    return {**deepcopy(record),'selected_at':datetime.now(UTC).isoformat()}

def facts(case):
    data=case.model_dump(mode='json') if hasattr(case,'model_dump') else case
    project=data.get('project') or {};assessment=data.get('pv_load_screening_assessment') or {}
    decision=(data.get('decision') or {}).get('decision','unknown')
    profile=data.get('component_profile') or {}
    risks={k:(assessment.get(k) or {}).get('risk_level','unknown') for k in ('wind','snow')}
    risks={k:v if v in VALUES[k] else 'unknown' for k,v in risks.items()}
    # This is judgment completeness, not a statement that all site materials exist.
    evidence='incomplete' if decision=='request_more' or (data.get('decision') or {}).get('missing_requirements') else 'unknown' if decision in ('unknown','manual_review') or assessment.get('requires_manual_review') else 'sufficient'
    return dict(province=project.get('province'),city=project.get('city'),
        model=profile.get('component_model') if profile.get('model_match_status')=='exact' else None,
        capacity_kw=project.get('installed_capacity_kw'),start_date=project.get('proposed_start_date'),
        decision=decision,evidence=evidence,**risks)

def evaluate(case,scheme):
    if not scheme:return {}
    f=facts(case);results=[];matches=[];unknown=[]
    for rule in scheme['rules']:
        c=rule['conditions'];checks=[];missing=[]
        for key,actual in [('provinces',f['province']),('cities',f['city']),('models',f['model'])]:
            if c[key]:
                if actual is None:missing.append(key)
                else:checks.append(actual.casefold() in [s.casefold() for s in c[key]])
        if c['start_months']:
            if not f['start_date']:missing.append('proposed_start_date')
            else:checks.append(int(str(f['start_date'])[5:7]) in c['start_months'])
        for key,op in [('min_capacity_kw',lambda a,b:a>=b),('max_capacity_kw',lambda a,b:a<=b)]:
            if c[key] is not None:
                if f['capacity_kw'] is None:missing.append('installed_capacity_kw')
                else:checks.append(op(f['capacity_kw'],c[key]))
        risk=f[rule['source']]
        if risk=='unknown' and 'unknown' not in rule['values']:missing.append(rule['source'])
        else:checks.append(risk in rule['values'])
        state='not_matched' if False in checks else 'insufficient' if missing else 'matched'
        entry=dict(rule_id=rule['id'],name=rule['name'],status=state,priority=rule['priority'],action=rule['action'],attention_level=rule['attention_level'],reason=rule['reason'],source=rule['source'],observed_value=risk,missing=missing,conditions=deepcopy(c),evidence_ref={'decision':'decision','wind':'pv_load_screening_assessment.wind','snow':'pv_load_screening_assessment.snow','evidence':'decision / pv_load_screening_assessment'}[rule['source']])
        results.append(entry)
        if state=='matched':matches.append(entry)
        if state=='insufficient':unknown.append(entry)
    top=max([r['priority'] for r in matches],default=None)
    winners=[r for r in matches if r['priority']==top]
    actions={r['action'] for r in winners};conflict=len(actions)>1
    action='manual_review' if conflict else next(iter(actions),'none')
    reasons=[]
    if conflict:reasons.append('最高优先级策略动作冲突，转人工核验；不叠加加费建议。')
    if unknown:
        reasons.append('部分策略缺少匹配依据，不能视为不适用或检查通过。')
        if action not in ('request_more','recommend_reject'):action='manual_review'
    baseline=f['decision'];effective=action
    if baseline=='recommend_reject':effective='recommend_reject';reasons.append('保留原风险评估中的拒保建议。')
    elif baseline=='request_more':effective='request_more';reasons.append('原结果仍需补材，不能因策略匹配而继续承保或定价。')
    elif baseline=='manual_review' and action not in ('request_more','recommend_reject'):effective='manual_review';reasons.append('原风险评估待人工复核，加费建议须待事实核实。')
    elif action=='surcharge' and f['evidence']!='sufficient':effective='manual_review';reasons.append('判断依据未充分确认，暂不形成可执行的加费处理。')
    elif action in ('none','notify'):effective=baseline
    return dict(schema_version='1',scheme_snapshot=deepcopy(scheme),facts=f,rule_results=results,
        matched_ids=[r['rule_id'] for r in matches],selected_ids=[r['rule_id'] for r in winners],conflict=conflict,
        strategy_action=action,effective_suggestion=effective,original_ai_decision=baseline,
        attention_level=max((r['attention_level'] for r in winners),key=['low','medium','high','critical'].index,default=None),
        reasons=reasons,requires_human_review=True,pricing=dict(mode='suggestion_only',rate=None,notice='未配置费率，不计算加费比例或保费；须由业务方确认定价依据。'),
        evaluated_at=datetime.now(UTC).isoformat())
