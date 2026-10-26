"""Shared city-table front-load quantification for the lab and underwriting."""
import math
from copy import deepcopy
from datetime import UTC, datetime

from app.rules.config import BusinessRulesConfig

from .city_engine import RULES, InputError, snow_calc, wind_calc
from .city_lookup import lookup

VERSION='city-wind-snow-3.1.0'
RISK_LABELS={'low':'低','medium':'中','high':'高','critical':'极高','unknown':'待评估'}

def model_key(value): return ''.join(str(value or '').split()).casefold()

def capacities(component):
    result={}
    for hazard in ['wind','snow']:
        item={'value_pa':None,'field':None,'source':'','direction':'front','reason':'未取得精确型号的可用正面参数','evidence':[]}
        if component and component.model_match_status=='exact':
            # The front value is applicable to this agreed front-positive screen.
            # Never reuse back-static wind-suction values as front-positive data.
            fields=['front_static_load_pa'] if hazard=='wind' else ['snow_load_pa','front_static_load_pa']
            for field in fields:
                value=getattr(component,field,None)
                records=component.parameter_evidence.get(field,[])
                if isinstance(records,dict): records=records.get('records',[])
                if any(x.get('status')=='conflict' for x in records): continue
                if value is None or isinstance(value,bool) or not math.isfinite(value) or value<=0: continue
                found=[x for x in records if x.get('status')=='found' and x.get('source_url')]
                source=component.parameter_sources.get(field) or component.source_url
                if not found and not source: continue
                # Directional direct snow values are permitted; incompatible back
                # labels in records are excluded from the front-only comparison.
                if field=='snow_load_pa' and any(str(x.get('load_direction','')).lower() in ['back','rear','negative'] for x in found): continue
                source='；'.join(dict.fromkeys(str(x) for row in found for x in [row.get('document_title'),row.get('source_url'),row.get('quote')] if x)) or str(source)
                item.update(value_pa=value,field=field,source=source,evidence=found,reason='',
                            basis_label='正面标称机械载荷映射值' if field=='front_static_load_pa' else '厂家雪荷载参数')
                break
        result[hazard]=item
    return result

def grade(ratio,adequate,critical):
    if ratio<=1/adequate: return 'low'
    if ratio<=1: return 'medium'
    if ratio<=1/critical: return 'high'
    return 'critical'

def assess_unified(*,case_id,model,address,component,screening_input=None,province=None,city=None,district=None,location_conflict=False,simulated=False):
    supplied=deepcopy(screening_input or {})
    if not isinstance(supplied,dict): raise InputError('风雪安装参数必须为对象')
    raw_site=supplied.get('site')
    site=dict(raw_site) if isinstance(raw_site,dict) else {}
    # Project address is authoritative, including any source-station selection.
    conflicts=[]
    if site.get('address') and (not isinstance(site['address'],str) or ''.join(site['address'].split())!=''.join((address or '').split())): conflicts.append('风雪输入地址与案件地址不一致')
    site['address']=address or ''
    if province: site['province']=province
    # Address matching owns city selection; project city text may use modern names.
    if district: site['district']=district
    location=lookup(site)
    if location_conflict: conflicts.append('案件位置证据冲突，请先核实地点')
    if component and model_key(component.component_model)!=model_key(model): conflicts.append('组件查询型号与案件完整型号不一致')
    caps=capacities(component)
    rules=BusinessRulesConfig.load(); adequate=rules.catastrophe_adequate_margin_ratio; critical=rules.catastrophe_critical_shortfall_ratio
    data={'_location_match':location,'ai_geometry':supplied.get('ai_geometry'),'snow_conditions':supplied.get('snow_conditions',{})}
    r={'model_version':VERSION,'project_id':case_id,'created_at':datetime.now(UTC).isoformat(),'assessment_type':'10年重现期风雪量化初筛','mode':'simulated' if simulated else 'real',
       'location_match':location,'manufacturer':{'model':model,'source':component.source_name if component else '未取得'},
       'component_capacities':caps,'component_profile':component.model_dump(mode='json') if component else None,
       'assumptions':RULES['assumptions'],'limitations':RULES['limitations']+['所示最大值是10年重现期场景及参数区间内的不利估计，不是未来绝对最大值。','等级为可配置初筛分档，沿用现有0.75/1.25能力比阈值，待业务确认，不是出险概率。','本轮仅评价正面风雪，不纳入冰雹。'],
       'thresholds':{'adequate_capacity_demand_ratio':adequate,'critical_capacity_demand_ratio':critical,'business_confirmed':False},
       'actions':[],'can_auto_approve':False,'input_snapshot':{'model':model,'address':address,'screening_input':supplied},'integration_issue':'；'.join(conflicts) or None}
    for hazard,fn in [('wind',wind_calc),('snow',snow_calc)]:
        try:
            if conflicts: raise InputError('；'.join(conflicts))
            if caps[hazard]['value_pa'] is None: raise InputError(('风' if hazard=='wind' else '雪')+'项：'+caps[hazard]['reason'])
            row=fn(data,caps[hazard]['value_pa'])
            if not all(math.isfinite(v) for v in row['load_pa'].values()): raise InputError('荷载计算超出数值范围，请检查输入')
            row['capacity_pa']=caps[hazard]['value_pa']; row['capacity_field']=caps[hazard]['field']
            row['risk_level']=grade(row['comparison_ratio']['max'],adequate,critical)
            row['risk_label']=RISK_LABELS[row['risk_level']]
            row['risk_range']=[grade(row['comparison_ratio'][x],adequate,critical) for x in ['min','max']]
            row['uncertainty_crosses_grade']=row['risk_range'][0]!=row['risk_range'][1]
            r[hazard]=row
        except (InputError,OverflowError) as exc:
            r[hazard]={'status':'INSUFFICIENT_DATA','label':'暂无法判断','reason':str(exc),'risk_level':'unknown','risk_label':'待评估'}
            r['actions'].append(str(exc))
    available=[r[h] for h in ['wind','snow'] if 'load_pa' in r[h]]
    r['data_complete']=len(available)==2
    order=['unknown','low','medium','high','critical']; r['risk_level']=max((row['risk_level'] for row in available),key=order.index) if available else 'unknown'
    r['known_max_risk_level']=r['risk_level']
    if not r['data_complete'] and r['risk_level'] not in ['high','critical']:
        r['risk_level']='unknown'
    r['risk_label']=RISK_LABELS[r['risk_level']]
    if not available: state='INSUFFICIENT_DATA'
    elif len(available)==1: state='PARTIAL'
    else: state=max((row['status'] for row in available),key=lambda v:{'NOT_EXCEEDED':0,'BOUNDARY':1,'UNCERTAIN':2,'EXCEEDED':3}[v])
    r['status']=state
    r['label']=('风雪初筛等级：'+r['risk_label'])+('；另有项目待评估' if len(available)==1 else '')
    r['requires_manual_review']=not (
        r['data_complete'] and state=='NOT_EXCEEDED' and r['risk_level']=='low'
        and not conflicts and not any(row.get('uncertainty_crosses_grade') for row in available)
    )
    if component:
        manual = component.workbench_result.get('case_parameter_review', {})
        unconfirmed = [k for k,p in manual.get('parameters', {}).items()
                       if k != 'rated_power_w' and (not p.get('installation_confirmed') or p.get('load_basis') == 'unspecified')]
        if unconfirmed:
            r['requires_manual_review'] = True
            r['actions'].append('本案补充载荷的安装适用条件或测试／设计口径尚未确认，初筛数值不能代替适用性核验')
    if available and r['requires_manual_review']:
        r['actions'].append('复核组件完整型号、参数来源及现场安装条件；本轮等级不能单独决定承保或加费')
    r['actions']=list(dict.fromkeys(r['actions']))
    return r

def report(r):
    loc=r['location_match']; lines=['# 风雪风险量化报告','',f"**{r['label']}**",'',f"- 案件：{r.get('project_id') or '未关联'}；组件：{r['manufacturer']['model'] or '未取得'}",'- 口径：城市表10年重现期、固定支架、正面均匀荷载；区间较大值作为本轮不利需求。','']
    if loc['status']=='MATCHED':
        fmt=lambda v:'无统计资料' if v is None else f'{v:.2f}'
        lines += [f"- 取值城市/站点：{loc['province']}·{loc['city_source']}；基本风压{fmt(loc['wind_pressure_kn_m2'])}、基本雪压{fmt(loc['snow_pressure_kn_m2'])} kN/m²。",f"- 来源：{loc['source_url']}；记录{loc['record_id']}。",'']
    else: lines += ['- 地点匹配：'+loc['message'],'']
    lines += ['| 灾种 | 组件对比能力 Pa | 场景荷载范围 Pa | 不利对比比例 | 初筛等级 |','|---|---:|---:|---:|---|']
    for h,name in [('wind','风'),('snow','雪')]:
        row=r[h]
        if 'load_pa' in row:
            lines.append(f"| {name} | {row['capacity_pa']:.1f} | {row['load_pa']['min']:.1f}～{row['load_pa']['max']:.1f} | {row['comparison_percent']['max']:.1f}% | {row['risk_label']} |")
        else: lines.append(f"| {name} | — | — | — | 待评估：{row['reason']} |")
    lines += ['','**计算依据**','']
    for h in ['wind','snow']:
        if 'trace' not in r[h]: continue
        t=r[h]['trace']
        if h=='wind':
            worst=max(t['evaluated_combinations'],key=lambda x:x['load_pa'])
            lines.append(f"- 风：{t['q_pa']:.1f} Pa × βgz {worst['beta_gz']} × μz {worst['mu_z']} × μsw {worst['mu_sw']:.3f}；高度{worst['height_m']}m、倾角{worst['tilt_deg']}°、粗糙{worst['terrain']}类。")
        else: lines.append(f"- 雪：基本雪压 × 1000 × 均匀积雪系数1.0 = {r[h]['load_pa']['max']:.1f} Pa。")
        cap=r['component_capacities'][h]
        lines.append(f"- {'风' if h=='wind' else '雪'}项能力字段：{cap['field']}，{cap.get('basis_label','')}；来源：{cap['source']}。")
    a=r['thresholds']['adequate_capacity_demand_ratio']; c=r['thresholds']['critical_capacity_demand_ratio']
    lines += ['',f'分档（荷载需求÷标称能力）：≤{1/a:.0%}低；>{1/a:.0%}至100%中；>100%至{1/c:.1%}高；>{1/c:.1%}极高。阈值待业务确认。','', '**补充与复核建议**','']+['- '+v for v in r['actions']]
    lines += ['', '**适用范围**','']+['- '+v for v in r['limitations']]
    return '\n'.join(lines)+'\n'
