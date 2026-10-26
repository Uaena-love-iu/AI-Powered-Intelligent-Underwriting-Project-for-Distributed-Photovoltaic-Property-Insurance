"""PV front-load screening. Python 3.11+, standard library only."""
import json
import math
from itertools import pairwise
from pathlib import Path

from .city_lookup import lookup

ROOT = Path(__file__).resolve().parent
RULES = json.loads((ROOT / 'city_rules.json').read_text(encoding='utf-8'))
LABELS = {'NOT_EXCEEDED': '本轮未发现数值超标', 'EXCEEDED': '重点复核：超过标称值',
          'BOUNDARY': '边界复核：达到标称值', 'UNCERTAIN': '估计范围跨越标称值，需复核',
          'INSUFFICIENT_DATA': '暂无法判断', 'PARTIAL': '部分项目可计算，整体待补充资料'}

class InputError(ValueError):
    pass

def number(value, name, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InputError(f'{name}必须是有限数值')
    if positive and value <= 0:
        raise InputError(f'{name}必须大于0')
    return value

def evidence(obj, name):
    if not isinstance(obj, dict):
        raise InputError(f'缺少{name}')
    if not isinstance(obj.get('source'), str) or not obj['source'].strip():
        raise InputError(f'{name}缺少source来源')
    return obj

def interval(obj, name):
    evidence(obj, name)
    lo, hi = number(obj.get('min'), name), number(obj.get('max'), name)
    if lo > hi:
        raise InputError(f'{name}范围上下限颠倒')
    return lo, hi

def height_product(z, terrain):
    # The supplied tables do not authorize height interpolation/extrapolation.
    if z <= 0:
        raise InputError('计算高度必须大于0')
    key = '5' if z <= 5 else str(int(z)) if z in (10, 15) else None
    if key is None:
        raise InputError('高度不在已验证取值范围：仅支持0<z≤5、10、15m；请补测或接入有依据的高度规则')
    row = RULES['height'][key][terrain]
    return row[0], row[1]

def shape(angle):
    table = RULES['front_shape']
    if not 0 <= angle <= 55:
        raise InputError('倾角超出0～55°已验证范围，不允许外推')
    if angle <= 5:
        return table[0][1]
    for (a, x), (b, y) in pairwise(table):
        if a <= angle <= b:
            return x + (y-x)*(angle-a)/(b-a)
    raise InputError('无法查询倾角系数')

def status(lo, hi):
    if lo < 1 < hi:
        return 'UNCERTAIN'
    if hi > 1:
        return 'EXCEEDED'
    if math.isclose(hi, 1, rel_tol=0, abs_tol=1e-12):
        return 'BOUNDARY'
    return 'NOT_EXCEEDED'

def metric(lo, hi, capacity, trace):
    ratios = [lo / capacity, hi / capacity]
    s = status(*ratios)
    return {'status': s, 'label': LABELS[s], 'load_pa': {'min': lo, 'max': hi},
            'comparison_ratio': {'min': ratios[0], 'max': ratios[1]},
            'comparison_percent': {'min': 100*ratios[0], 'max': 100*ratios[1]}, 'trace': trace}

def wind_calc(data, capacity):
    location = data['_location_match']
    if location['status'] != 'MATCHED':
        raise InputError(location['message'])
    q = location['wind_pressure_kn_m2']
    if q is None:
        raise InputError('所选城市10年重现期基本风压无统计资料，不能按0计算')
    q = number(q, '表列基本风压') * 1000
    ai = data.get('ai_geometry')
    if not isinstance(ai, dict):
        raise InputError('缺少AI安装信息')
    hlo, hhi = interval(ai.get('height_m'), '组件高度')
    alo, ahi = interval(ai.get('tilt_deg'), '组件倾角')
    # A continuous height interval containing unlisted heights cannot be resolved
    # by merely checking its endpoints.
    if hlo <= 0 or (hlo != hhi and hhi > 5):
        raise InputError('高度估计区间包含表外高度，不能擅自插值；请补充确认或有依据的高度规则')
    terrain = evidence(ai.get('terrain'), '粗糙度')
    candidates = terrain.get('candidates')
    if not isinstance(candidates, list) or not candidates or any(x not in ['A','B','C','D'] for x in candidates):
        raise InputError('粗糙度候选必须为A/B/C/D中的非空列表')
    shapes = [shape(alo), shape(ahi)]
    combinations = []
    for category in sorted(set(candidates)):
        beta, mu = height_product(hhi, category)
        for angle, coeff in zip([alo, ahi], shapes):
            combinations.append({'terrain': category, 'height_m': hhi, 'tilt_deg': angle,
                                 'beta_gz': beta, 'mu_z': mu, 'mu_sw': coeff,
                                 'load_pa': q * beta * mu * coeff})
    loads = [x['load_pa'] for x in combinations]
    return metric(min(loads), max(loads), capacity, {'formula': '1000*w0*beta_gz*mu_z*mu_sw',
                  'return_period_years': 10, 'q_pa': q, 'location_match': location, 'coefficient_source': '5.1.3表1 / 5.1.4表2 / 5.1.6.3表3',
                  'evaluated_combinations': combinations})

def snow_calc(data, capacity):
    location = data['_location_match']
    if location['status'] != 'MATCHED':
        raise InputError(location['message'])
    value = location['snow_pressure_kn_m2']
    if value is None:
        raise InputError('所选城市10年重现期基本雪压无统计资料，不能按0计算')
    conditions = data.get('snow_conditions', {})
    if not isinstance(conditions, dict) or not isinstance(conditions.get('special_accumulation_flag', False), bool):
        raise InputError('snow_conditions及堆雪标记类型错误')
    if conditions.get('special_accumulation_flag') is True:
        raise InputError('发现局部堆雪等异常积雪线索，均匀积雪模型不适用，需人工复核')
    return metric(1000*value, 1000*value, capacity,
                  {'formula': '1000*S0', 'mu_r': 1.0, 'return_period_years': 10,
                   'source': location['source_url'], 'location_match': location,
                   'basis': '水平投影面雪荷载数值作为正面标称值初筛输入；不作倾角减载'})


def evaluate(data):
    if not isinstance(data, dict):
        raise InputError('请求必须是JSON对象')
    snapshot = dict(data)
    data = dict(data)
    data['_location_match'] = lookup(data.get('site'))
    result = {'model_version': RULES['version'], 'project_id': data.get('project_id'),
              'assessment_type': '10年重现期组件正面风雪荷载数值初筛',
              'location_match': data['_location_match'],
              'ignored_legacy_fields': [k for k in ['wind', 'snow', 'air_density'] if k in snapshot], 'assumptions': RULES['assumptions'],
              'limitations': RULES['limitations'], 'actions': [], 'can_auto_approve': False}
    try:
        c = evidence(data.get('manufacturer'), '厂家参数')
        capacity = number(c.get('front_load_pa'), '厂家正面标称荷载', True)
        if not c.get('model'):
            raise InputError('缺少组件型号，不能匹配厂家标称值')
        result['manufacturer'] = c
    except InputError as e:
        for hazard in ['wind', 'snow']:
            result[hazard] = {'status': 'INSUFFICIENT_DATA', 'label': LABELS['INSUFFICIENT_DATA'], 'reason': str(e)}
        result.update(status='INSUFFICIENT_DATA', label=LABELS['INSUFFICIENT_DATA'], priority_hazard=None, max_comparison_percent=None)
        result['actions'] = [str(e)]
        result['input_snapshot'] = snapshot
        return result
    for hazard, fn in [('wind', wind_calc), ('snow', snow_calc)]:
        try:
            result[hazard] = fn(data, capacity)
        except InputError as e:
            result[hazard] = {'status': 'INSUFFICIENT_DATA', 'label': LABELS['INSUFFICIENT_DATA'], 'reason': str(e)}
            result['actions'].append(str(e))
    available = [k for k in ['wind', 'snow'] if 'comparison_percent' in result[k]]
    if not available:
        overall = 'INSUFFICIENT_DATA'
    elif len(available) < 2:
        overall = 'PARTIAL'
    else:
        priority = {'EXCEEDED': 4, 'UNCERTAIN': 3, 'BOUNDARY': 2, 'NOT_EXCEEDED': 1}
        overall = max((result[k]['status'] for k in available), key=lambda x: priority[x])
    result['status'], result['label'] = overall, LABELS[overall]
    target = max(available, key=lambda k: result[k]['comparison_percent']['max']) if available else None
    result['priority_hazard'] = target
    result['max_comparison_percent'] = result[target]['comparison_percent']['max'] if target else None
    for hazard in available:
        if result[hazard]['status'] != 'NOT_EXCEEDED':
            result['actions'].append(('风' if hazard == 'wind' else '雪') + '项需人工复核输入依据、安装条件及厂家标称值')
    result['actions'].append('继续核查安装连接、屋顶及其他核保规则；本指标不直接决定承保、加费或拒保')
    result['actions'] = list(dict.fromkeys(result['actions']))
    result['input_snapshot'] = snapshot
    return result

def report(result):
    rows = ['# 组件正面风雪荷载初步评估', '', f"**结论：{result['label']}**", '',
            '| 项目 | 估算荷载（Pa） | 标称值对比比例 | 提示 |', '|---|---:|---:|---|']
    for key, title in [('wind', '风'), ('snow', '雪')]:
        r = result[key]
        if 'load_pa' in r:
            p, q = r['load_pa'], r['comparison_percent']
            rows.append(f"| {title} | {p['min']:.1f}～{p['max']:.1f} | {q['min']:.1f}%～{q['max']:.1f}% | {r['label']} |")
        else:
            rows.append(f"| {title} | — | — | {r['reason']} |")
    location = result['location_match']
    if location['status'] == 'MATCHED':
        rows += ['', f"取值城市/站点：{location['province']}·{location['city_source']}；匹配方式：{location['match_method']}。风雪均采用10年重现期；城市代表值并非项目点实测值。"]
        rows += ['- 数据提示：' + x for x in location['warnings']]
    if result['ignored_legacy_fields']:
        rows += ['', '旧风速、雪压、空气密度输入已忽略，计算只使用本地城市表。']
    m = result.get('manufacturer')
    if m:
        rows += ['', f"比较基准：{m['model']}，厂家正面标称值 {m['front_load_pa']} Pa；来源：{m['source']}。"]
    rows += ['', '**处理建议**', ''] + ['- ' + x for x in result['actions']]
    rows += ['', '**计算依据**', '']
    for key, title in [('wind', '风'), ('snow', '雪')]:
        t = result[key].get('trace')
        if t:
            if key == 'wind':
                rows.append(f"- 风：10年重现期基本风压 {t['q_pa']/1000:g} kN/m²；固定支架、正面均匀荷载。")
                worst = max(t['evaluated_combinations'], key=lambda x: x['load_pa'])
                rows.append(f"- 风项较不利参数：高度{worst['height_m']}m，倾角{worst['tilt_deg']}°，粗糙度{worst['terrain']}类；阵风系数{worst['beta_gz']}，高度变化系数{worst['mu_z']}，迎风体型系数{worst['mu_sw']:.3f}。")
            else:
                rows.append(f"- 雪：{t['return_period_years']}年重现期基本雪压；来源：{t['source']}；均匀积雪系数1.0。")
    rows += ['', '**评价范围**', ''] + ['- ' + x for x in result['limitations']]
    return '\n'.join(rows) + '\n'

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('input', type=Path)
    p.add_argument('--out', type=Path, default=Path('result.json'))
    p.add_argument('--report', type=Path, default=Path('report.md'))
    args = p.parse_args()
    value = evaluate(json.loads(args.input.read_text(encoding='utf-8-sig')))
    args.out.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    args.report.write_text(report(value), encoding='utf-8')
    print(f"{value['label']}；结果：{args.out}；报告：{args.report}")
