"""Conservative offline address-to-source-record lookup. No geospatial guessing."""
import json
import re
from pathlib import Path

DATA = json.loads((Path(__file__).parent/'data/city_wind_snow.json').read_text(encoding='utf-8'))
RECORDS = DATA['records']
PROVINCES = sorted({r['province'] for r in RECORDS})
FULL = {'内蒙古':'内蒙古自治区','广西':'广西壮族自治区','宁夏':'宁夏回族自治区','新疆':'新疆维吾尔自治区','西藏':'西藏自治区','香港':'香港特别行政区','澳门':'澳门特别行政区'}
MUNICIPAL = {'北京','天津','上海','重庆'}

def province_name(value):
    if not isinstance(value, str): return None
    for p in PROVINCES:
        if value in (p, FULL.get(p, p+('市' if p in MUNICIPAL else '省'))): return p
    return None

def failure(status, message, candidates=()):
    return {'status': status, 'message': message, 'candidates': [
        {'record_id':r['id'],'province':r['province'],'city_source':r['city_source']} for r in candidates]}

def exact_candidates(name, pool):
    # Exact source names have priority. Only 市 suffix normalization is supported,
    # and it is disabled if another source name would collide (黄山/黄山市).
    exact=[r for r in pool if name==r['city_source']]
    if exact: return exact
    return [r for r in pool if r['city_source'].endswith('市') and name==r['city_source'][:-1]]

def lookup(site):
    if not isinstance(site,dict): return failure('INVALID_INPUT','请提供site地址对象')
    for key in ['address','province','city','district','source_record_id']:
        if key in site and (not isinstance(site[key],str) or not site[key].strip() or len(site[key])>500):
            return failure('INVALID_INPUT',f'{key}必须为1～500字的非空字符串')
    if 'source_record_confirmed' in site and not isinstance(site['source_record_confirmed'],bool):
        return failure('INVALID_INPUT','source_record_confirmed必须是布尔值')
    address=re.sub(r'\s+','',site.get('address','')).removeprefix('中华人民共和国').removeprefix('中国')
    explicit_province=province_name(site.get('province'))
    if 'province' in site and explicit_province is None: return failure('NOT_FOUND','省份名称不在本库，请核实')
    detected=None; remaining=address
    for p in PROVINCES:
        alias=FULL.get(p,p+('市' if p in MUNICIPAL else '省'))
        if address.startswith(alias): detected=p; remaining=address[len(alias):]; break
    if explicit_province and detected and explicit_province!=detected:
        return failure('CONFLICT','结构化省份与地址省份冲突，请核实')
    province=explicit_province or detected
    pool=[r for r in RECORDS if not province or r['province']==province]
    chosen=[]; method=''
    # Explicit selection must be confirmed; a record ID is not a silent override.
    if 'source_record_id' in site:
        chosen=[r for r in pool if r['id']==site['source_record_id']]
        if not chosen: return failure('NOT_FOUND','指定记录不存在或与省份冲突')
        if not site.get('source_record_confirmed'):
            return failure('CONFIRMATION_REQUIRED','请确认所选城市/站点可代表该地址，再设置source_record_confirmed=true',chosen)
        method='人工确认城市/站点'
    elif 'city' in site:
        chosen=exact_candidates(site['city'],pool)
        method='结构化地名匹配'
        if not chosen: return failure('NOT_FOUND','结构化地名未精确匹配原表；请确认旧地名或选定站点')
        # A finer exact station is preferable to the city representative only if
        # confirmed explicitly: a district token can be ambiguous across cities.
    else:
        for r in pool:
            n=r['city_source']
            for text in [remaining,address]:
                if text==n or (text.startswith(n) and (n.endswith(('市','县','区','旗')) or text[len(n):].startswith(('市','县','区','旗')))):
                    chosen.append(r); break
        chosen=list({r['id']:r for r in chosen}.values())
        # Municipality is usable when the address explicitly names it.
        if not chosen and province in MUNICIPAL and detected:
            chosen=[r for r in pool if r['city_source']==province+'市']
        method='地址行政地名前缀匹配'
        # Filing forms may start with the prefecture while the table names the
        # county station (吴忠市同心县 -> 同心). Match an explicit county token;
        # never select a nearby station or arbitrary substring in a street name.
        county = re.match(r'^[^市县区]+市([^市县区]+)县', remaining)
        if not chosen and county:
            county_name = county.group(1)
            chosen = [r for r in pool if r['city_source'] in (county_name, county_name+'县')]
            method = '地址明确县名匹配'
    if not chosen: return failure('NOT_FOUND','地址未匹配原表城市/站点；请补充省份及表列地名，或人工选择记录')
    if len(chosen)!=1: return failure('AMBIGUOUS','存在多个城市/站点候选，请明确选择；不自动取邻近值',chosen)
    r=chosen[0]
    if 'source_name_requires_confirmation' in r['quality_flags'] and not site.get('source_record_confirmed'):
        return failure('CONFIRMATION_REQUIRED','该原表地名需核对原图和现行政区，请确认后选择记录',chosen)
    if method=='结构化地名匹配' and address:
        address_city = re.match(r'^([^市]+市)', remaining) if province not in MUNICIPAL else None
        if address_city and not r['city_source'].startswith(address_city.group(1)):
            return failure('CONFLICT','结构化城市与地址中的市名冲突，请核实')
        check=lookup({k:v for k,v in site.items() if k not in ['city','district']})
        if check['status']=='MATCHED' and check['record_id']!=r['id']:
            return failure('CONFLICT','结构化城市与地址匹配结果冲突，请核实')
    warnings=['使用所选城市/站点代表值；项目地点与表列站点的适用性需业务确认。']
    if site.get('district'): warnings.append('已保留项目区县信息；未将区县自动映射到另一气象站。')
    if r['quality_flags']: warnings.append('原表质量标记：'+','.join(r['quality_flags'])+'；保留原值，请对照原图复核。')
    return {'status':'MATCHED','record_id':r['id'],'province':r['province'],'city_source':r['city_source'],
            'match_method':method,'return_period_years':10,'unit':'kN/m²',
            'wind_pressure_kn_m2':r['wind_pressure_kn_m2']['10'],
            'snow_pressure_kn_m2':r['snow_pressure_kn_m2']['10'],
            'dataset_id':DATA['dataset_id'],'source_url':DATA['source_url'],'sources':r['sources'],
            'quality_flags':r['quality_flags'],'warnings':warnings}

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(); p.add_argument('address'); a=p.parse_args()
    print(json.dumps(lookup({'address':a.address}),ensure_ascii=False,indent=2))
