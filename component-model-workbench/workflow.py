import copy, hashlib, json, re, sqlite3, time, uuid
from datetime import datetime, UTC
from pathlib import Path
from pv_component_lookup import ComponentModelIndex, SolarStackComponentProvider
from pv_component_lookup.contracts import ComponentProfile, ProviderError
from pv_component_lookup.naming import model_key
from agent import ComponentSearchAgent
from source_policy import SourcePolicy

FIELDS=('wind_load_pa','snow_load_pa')
VALUES=FIELDS+('front_static_load_pa','back_static_load_pa','rated_power_w')
ALIASES={'ja solar':['晶澳','晶澳科技','ja solar'], 'longi':['隆基','longi'], 'jinkosolar':['晶科','jinko'], 'trina solar':['天合','trina'], 'canadian solar':['阿特斯','canadian solar'], 'astronergy':['正泰','astronergy'], 'ht-saae':['航天','ht-saae','htsaae'], 'risen':['东方日升','risen']}

def utc():return datetime.now(UTC).isoformat()

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.executescript('CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,created TEXT,model TEXT,status TEXT,payload TEXT);CREATE TABLE IF NOT EXISTS reviewed(model_key TEXT PRIMARY KEY,payload TEXT,reviewed TEXT);CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,run_id TEXT,created TEXT,verdict TEXT,note TEXT,fields TEXT);')
    def connect(self):return sqlite3.connect(self.path,timeout=15)
    def save(self,result):
        with self.connect() as db:db.execute('INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)',(result['id'],result['created_at'],result['input']['raw_model'],result['status'],json.dumps(result,ensure_ascii=False)))
    def history(self):
        with self.connect() as db:rows=db.execute('SELECT id,created,model,status FROM runs ORDER BY created DESC LIMIT 100').fetchall()
        return [dict(id=r[0],created_at=r[1],model=r[2],status=r[3]) for r in rows]
    def get(self,rid):
        with self.connect() as db:row=db.execute('SELECT payload FROM runs WHERE id=?',(rid,)).fetchone()
        return json.loads(row[0]) if row else None
    def cached(self,model):
        with self.connect() as db:row=db.execute('SELECT payload FROM reviewed WHERE model_key=?',(model_key(model),)).fetchone()
        return json.loads(row[0]) if row else None
    def feedback(self,rid,verdict,note,fields):
        result=self.get(rid)
        if not result:raise ValueError('查询记录不存在')
        profile=result.get('profile')
        if verdict=='verified':
            if not profile or profile.get('model_match_status')!='exact':raise ValueError('只有完整型号确认后才能保存核验参数')
            if not fields or any(k not in VALUES or profile.get(k) is None for k in fields):raise ValueError('请选择实际取得数值的字段')
            selected={k:profile[k] for k in fields}
            if any(any(e.get('status')=='conflict' for e in profile.get('parameter_evidence',{}).get(k,[])) for k in fields):raise ValueError('存在冲突的参数不能直接保存为核验通过')
            p=copy.deepcopy(profile)
            for k in VALUES:
                if k not in selected:p[k]=None
            p['parameter_evidence']={k:profile.get('parameter_evidence',{}).get(k,[]) for k in fields}
            if any(not records or all(not e.get('source_url') for e in records) for records in p['parameter_evidence'].values()):raise ValueError('核验入库必须有对应字段来源证据')
            policy=SourcePolicy(profile.get('manufacturer'))
            for recs in p['parameter_evidence'].values():
                for e in recs:
                    if str(e.get('source_kind','')).startswith('agent_') and not policy.classify(e.get('source_url',''))['allowed']:
                        raise ValueError('Agent字段包含个人或未核验来源，请重新查询有效官网／机构证据后核验')
            for recs in p['parameter_evidence'].values():
                for e in recs:e.update(requires_manual_review=False,human_verification={'at':utc(),'note':note,'run_id':rid})
            p['source_name']='人工核验本地缓存';p['lookup_notes']=['已核验所选字段数值与来源；现场安装及版本适用性仍需确认。']
            with self.connect() as db:db.execute('INSERT OR REPLACE INTO reviewed VALUES(?,?,?)',(model_key(p['component_model']),json.dumps(p,ensure_ascii=False),utc()))
        with self.connect() as db:
            fid=uuid.uuid4().hex;db.execute('INSERT INTO feedback VALUES(?,?,?,?,?,?)',(fid,rid,utc(),verdict,note,json.dumps(fields)))
            if verdict=='rejected':db.execute('DELETE FROM reviewed WHERE model_key=?',(model_key(result['input']['raw_model']),))
        return {'feedback_id':fid,'verdict':verdict,'saved_fields':fields if verdict=='verified' else []}

def canonical_brand(value,brands):
    if not value:return None
    key=model_key(value)
    for brand in brands:
        bk=model_key(brand)
        if key==bk:return brand
        for canonical,aliases in ALIASES.items():
            if bk==canonical and any(a in key for a in aliases):return brand
    return None

def merge_profiles(primary,other):
    if primary is None:return other
    if other is None or other.model_match_status!='exact':return primary
    if primary.model_match_status!='exact':return other
    if model_key(primary.component_model)!=model_key(other.component_model):return primary
    for key in VALUES:
        a,b=getattr(primary,key),getattr(other,key)
        recs=other.parameter_evidence.get(key,[])
        previous=primary.parameter_evidence.get(key,[])
        if any(e.get('status')=='conflict' for e in previous+recs):
            setattr(primary,key,None)
            primary.parameter_evidence[key]=previous+recs
            for e in primary.parameter_evidence[key]:e['status']='conflict'
            continue
        # An existing conflict is never overwritten by a later source.
        if a is not None and b is not None and abs(a-b)>max(.01,abs(a)*.001):
            setattr(primary,key,None);primary.parameter_evidence[key]=previous+recs
            for e in primary.parameter_evidence[key]:e['status']='conflict'
            primary.lookup_notes.append(key+'在不同来源存在差异，保留原文并等待复核。')
        elif a is None and b is not None:
            setattr(primary,key,b);primary.parameter_evidence[key]=previous+recs
            if key in other.parameter_sources:primary.parameter_sources[key]=other.parameter_sources[key]
        elif recs:primary.parameter_evidence[key]=previous+recs
    primary.lookup_notes.extend(other.lookup_notes)
    return primary

class Workbench:
    def __init__(self,root,reader=None,key='',provider_factory=None,agent_factory=None):
        self.root=Path(root);self.reader=reader;self.key=key
        self.rules_payload=json.loads((self.root/'data/naming_rules.json').read_text('utf-8'))
        self.rules_index=ComponentModelIndex(self.rules_payload)
        private=self.root/'private/api_model_index.json'
        self.index=ComponentModelIndex.from_file(private) if private.exists() else self.rules_index
        self.catalog=json.loads((self.root/'data/local_catalog.json').read_text('utf-8'))
        self.brands=[b['manufacturer'] for b in self.rules_payload['manufacturers']]
        self.source_policy=SourcePolicy(None).payload
        self.store=Store(self.root/'private/workbench.sqlite3')
        self.provider_factory=provider_factory;self.agent_factory=agent_factory

    def status(self):
        return {'local_rows':len(self.catalog['records']),'local_auto_match_rows':sum(r['auto_match_eligible'] for r in self.catalog['records']),'rule_count':len(self.rules_payload['rules']),'manufacturer_count':len(self.brands),'api_snapshot_models':self.rules_payload['model_count'],'series_count':self.rules_payload['api_series_count'],'solar_stack_configured':bool(self.key),'ocr_configured':bool(self.reader),'agent_configured':bool(self.reader),'ai_model':self.reader.model if self.reader else None,'rule_snapshot':self.rules_payload.get('retrieved_at_utc'),'rule_nature':'型号结构观察规则；并非所有字母的官方释义','local_source':self.catalog['source_file'],'brands':self.brands,'source_policy':{'version':self.source_policy['version'],'official_manufacturers':len(self.source_policy['manufacturer_official']),'institutions':len(self.source_policy['institutions']),'personal_sources_allowed':False,'order':['manufacturer','institution']}}

    def rules(self,query='',brand=''):
        query=query.casefold();rows=self.rules_payload['rules']
        if brand:rows=[r for r in rows if r['manufacturer']==brand]
        if query:rows=[r for r in rows if query in (r['manufacturer']+' '+r['template']+' '+ ' '.join(r['series_names'])).casefold()]
        return {'total':len(rows),'rules':rows[:80]}

    def local_profile(self,row):
        vals={k:row.get(k) for k in VALUES if row.get(k) is not None};ev={}
        for k,v in vals.items():
            ev[k]=[{'status':'found','value':v,'unit':'mm' if k=='hail_resistance_mm' else 'm/s' if k=='hail_impact_velocity_m_s' else 'W' if k=='rated_power_w' else 'Pa','raw_label':k,'raw_value':str(v),'source_kind':'local_workbook','source_url':row['source_url'],'workbook_row':row['workbook_row'],'load_basis':'test' if k in ('front_static_load_pa','back_static_load_pa') and re.search(r'测试|试验|test',row.get('source_note',''),re.I) else 'unspecified','requires_manual_review':True,'publisher':'本地组件目录（原始资料厂商：'+row['manufacturer']+'）','source_tier':'local_catalog','document_title':Path(self.catalog['source_file']).name,'quote':f"本地表第{row['workbook_row']}行，{k}={v}；{row.get('source_note','')}"}]
        return ComponentProfile(component_model=row['component_model'],manufacturer=canonical_brand(row['manufacturer'],self.brands) or row['manufacturer'],source_url=row['source_url'],source_name='本地组件目录',source_document_type=row['source_document_type'],market_version=row.get('market_version'),retrieved_at=datetime.now(UTC),match_confidence=.9,parameter_evidence=ev,parameter_sources={k:row['source_url'] for k in vals},lookup_notes=[row.get('source_note',''),'正、背面机械载荷已按原值保存；风雪对应、测试/设计口径和安装条件需按型号资料核验。'],**vals)

    def run(self,model,manufacturer='',strategy='auto',use_agent=True,emit=None,rid=None,material=None):
        rid=rid or uuid.uuid4().hex;started=time.monotonic();events=[]
        def event(stage,state,message):
            item={'at':utc(),'stage':stage,'state':state,'message':message,'elapsed_s':round(time.monotonic()-started,2)};events.append(item)
            if emit:emit(item)
        raw=model;model=model_key(model).upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9 ()/_.+\-]{2,99}',model):raise ValueError('请输入完整组件型号，不要输入单块功率或一段说明')
        brand=canonical_brand(manufacturer,self.brands)
        fs=self.rules_index.families(model,brand) if brand else self.rules_index.families(model)
        if not brand:
            bs={f['manufacturer'] for f in fs}
            if len(bs)==1:brand=next(iter(bs))
        event('identity','done',f'保留完整后缀；命中{len(fs)}条结构规则'+('，厂商候选 '+brand if brand else ''))
        profile=None;local_rows=[];errors=[];agent_result=None
        cached=self.store.cached(model) if strategy!='agent' else None
        if cached and (not brand or canonical_brand(cached.get('manufacturer'),self.brands)==brand):
            profile=ComponentProfile.model_validate({k:v for k,v in cached.items() if k in ComponentProfile.model_fields})
            event('local','done','命中人工核验缓存')
        elif strategy!='agent':
            local_rows=[r for r in self.catalog['records'] if model_key(r['component_model'])==model_key(model) and (not brand or canonical_brand(r['manufacturer'],self.brands)==brand)]
            eligible=[r for r in local_rows if r['auto_match_eligible']]
            if len(eligible)==1:profile=self.local_profile(eligible[0]);event('local','done','本地完整型号命中，继续检查参数缺口')
            else:event('local','candidate' if local_rows else 'not_found','本地记录需核对展开型号/市场版本，未直接回填参数' if local_rows else '本地没有完整型号记录')
        else:event('local','skipped','直接Agent测试模式，跳过本地')
        missing=profile.workbench_payload()['parameter_tasks']['missing_values'] if profile else list(FIELDS)
        if strategy=='auto' and missing:
            if self.key or self.provider_factory:
                event('api','running','SolarStack完整型号查询；缺参数时读取对应datasheet')
                try:
                    index=self.index
                    if brand:index=ComponentModelIndex({**self.index.payload,'models':[r for r in self.index.rows if r['manufacturer']==brand],'rules':[r for r in self.index.rules if r['manufacturer']==brand]})
                    provider=self.provider_factory(index) if self.provider_factory else SolarStackComponentProvider(self.key,index=index,cache_dir=self.root/'private/api_cache',page_ocr=self.reader.page_ocr if self.reader else None,semantic_extractor=self.reader,max_datasheets=2,timeout=25)
                    api=provider.lookup(model)
                    if api and brand and api.manufacturer!=brand:
                        errors.append('API厂商与输入不一致，未合并');event('api','conflict','厂商身份冲突，未合并API参数')
                    elif api:
                        profile=merge_profiles(profile,api)
                        if profile is None:profile=api
                        event('api','done' if api.model_match_status=='exact' else 'candidate','API完整型号已确认，资料读取完成' if api.model_match_status=='exact' else '仅有系列候选；没有借用邻近功率的参数')
                    else:event('api','not_found','API未找到完整型号')
                except ProviderError as e:errors.append(str(e));event('api','unavailable','来源不可用，不能当作产品不存在')
            else:event('api','unavailable','SolarStack密钥未配置')
        else:event('api','skipped','仅本地/直接Agent模式，或三项参数已取得')
        tasks=profile.workbench_payload()['parameter_tasks'] if profile else {'missing_values':list(FIELDS),'verification_targets':[]}
        missing=tasks['missing_values']
        if use_agent and strategy!='local' and missing:
            names={'wind_load_pa':'风载荷','snow_load_pa':'雪载荷','hail_resistance_mm':'抗冰雹直径'}
            event('agent','running','待补数值：'+ '、'.join(names[k] for k in missing)+ ('；已有风雪机械映射值，另核对对应关系和口径' if tasks['verification_targets'] else ''))
            seeds=[r['source_url'] for r in local_rows]
            if not seeds and fs:
                templates={f['template'].split('{power}')[0] for f in fs}
                seeds=list(dict.fromkeys(r['source_url'] for r in self.catalog['records'] if any(r['component_model'].startswith(t) for t in templates)))[:3]
            agent=self.agent_factory() if self.agent_factory else ComponentSearchAgent(self.rules_index,self.reader)
            def agent_emit(stage,state,msg):event(stage,state,msg)
            agent_result=agent.run(model,brand,missing,agent_emit,seeds=seeds,deadline=started+240,verification_targets=tasks['verification_targets'])
            proposal=agent_result.pop('profile',None)
            if proposal:profile=merge_profiles(profile,proposal)
            event('agent','done' if proposal else agent_result['status'],'已取得覆盖型号的参数证据，待人工核验' if proposal else agent_result.get('reason','未补足参数'))
        else:event('agent','skipped','三项数值已取得（含机械映射值）；对应及口径核验项保留' if not missing and tasks['verification_targets'] else '未启用Agent、仅本地模式，或参数已取得')
        payload=profile.workbench_payload() if profile else None
        if payload:
            for f in payload['mechanical_fields'].values():
                if payload['model_match_status']!='exact':f.update(value=None,status='identity_unconfirmed')
            for f in payload['ui_fields'].values():
                if payload['model_match_status']!='exact':
                    f.update(value=None,reference_value=None,status='identity_unconfirmed',can_use_as_design_capacity=False)
                f['requires_manual_review']=any(e.get('requires_manual_review') for e in f.get('evidence',[]))
                # Site installation suitability is outside this component testing workbench.
                f['can_use_as_design_capacity']=False
                f['applicability_status']='待核验现场安装与适用条件'
        field_statuses=[f['status'] for f in payload['ui_fields'].values()] if payload else []
        status='conflict' if 'conflict' in field_statuses else 'identity_unconfirmed' if payload and payload['model_match_status']!='exact' else 'parameters_found' if field_statuses and all(x=='found' for x in field_statuses) else 'parameters_mapped' if field_statuses and all(x in ('found','mechanical_mapped') for x in field_statuses) else 'partial' if payload and payload['model_match_status']=='exact' else 'source_unavailable' if errors or (agent_result and agent_result.get('status') in ('source_unavailable','unavailable')) else 'not_found'
        event('result','done','已保留取得的参数、原始来源及待补缺口' if payload else '查询已完成，未取得可回填参数；保留来源状态与缺口')
        result={'id':rid,'created_at':utc(),'status':status,'input':{'raw_model':raw,'normalized_model':model,'manufacturer_input':manufacturer,'manufacturer_candidate':brand,'strategy':strategy,'use_agent':use_agent,'material':material},'identity':{'rules':fs[:8],'rule_count':len(fs),'full_suffix_preserved':True,'rule_nature':'API样本结构观察，不证明输入型号存在'},'local_candidates':local_rows,'profile':payload,'agent':agent_result,'errors':errors,'steps':events,'elapsed_s':round(time.monotonic()-started,2),'feedback_next':'核对完整型号、原文、版本和条件后，可选择字段人工核验入库；未经核验的Agent值不自动写入本地核验库。'}
        self.store.save(result);return result
