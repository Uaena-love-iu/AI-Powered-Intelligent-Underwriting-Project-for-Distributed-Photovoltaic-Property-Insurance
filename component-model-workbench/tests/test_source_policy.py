import json,socket
from pathlib import Path
import httpx,pytest
from agent import ComponentSearchAgent,PublicFetcher,DocumentFetchError
from source_policy import SourcePolicy
from pv_component_lookup.naming import ComponentModelIndex
from pv_component_lookup.datasheet import DatasheetExtraction,extract_page
from pv_component_lookup.contracts import ProviderError

ROOT=Path(__file__).resolve().parents[1]
MODEL='JAM66D42-605/MB'
OFFICIAL='https://www.jasolar.com/panels/test.html'
INSTITUTION='https://www.dnv.com/news/test.html'
PERSONAL='https://www.zhihu.com/question/solar'

class Search:
    last_usage={'plugins':{'search':{'count':1}}}
    def __init__(self,leads):self.leads=leads;self.prompts=[]
    def search(self,prompt):self.prompts.append(prompt);return self.leads

class Fetch:
    def __init__(self,pages):self.pages=pages;self.visits=[];self.policy_guard=None
    def fetch(self,url):
        if self.policy_guard:self.policy_guard(url)
        self.visits.append(url)
        final,body=self.pages[url]
        return final,body.encode(),'text/html'

def make_agent(fetch,search):
    index=ComponentModelIndex(json.loads((ROOT/'data/naming_rules.json').read_text('utf-8')))
    return ComponentSearchAgent(index,None,fetcher=fetch,searcher=search,max_rounds=1)

def test_personal_false_official_and_unregistered_sources_are_rejected():
    policy=SourcePolicy('JA Solar')
    for url in [PERSONAL,'https://www.jasolar.com.evil.example/data.pdf','https://www.sohu.com/a/solar',
                'https://www.dnv.com/community/users/42','https://community.dnv.com/article/test','https://unverified.example/official-JA-Solar.pdf','https://[malformed/']:
        assert not policy.classify(url)['allowed']
    assert policy.classify(OFFICIAL)['source_tier']=='manufacturer'
    assert policy.classify(INSTITUTION)['source_tier']=='institution'
    assert not SourcePolicy('Unknown brand').summary()['official_domains_registered']

def test_official_sufficient_never_reads_institution_or_personal():
    search=Search([{'url':PERSONAL,'title':'official JA Solar'},{'url':INSTITUTION,'title':'report'},{'url':OFFICIAL,'title':'spec'}])
    fetch=Fetch({OFFICIAL:(OFFICIAL,f'<title>Official module sheet</title><p>{MODEL} Wind load: 2400 Pa. Snow load: 5400 Pa. Hail resistance: 25 mm.</p>')})
    r=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa','snow_load_pa','hail_resistance_mm'],lambda *x:None)
    assert fetch.visits==[OFFICIAL]
    assert len(search.prompts)==1
    assert r['profile'].wind_load_pa==2400
    record=r['profile'].parameter_evidence['wind_load_pa'][0]
    assert record['publisher']=='JA Solar' and record['source_url']==OFFICIAL and record['page']==1
    assert record['document_title']=='Official module sheet'
    assert record['quote'] and record['retrieved_at'] and record['source_policy_version']
    assert r['excluded_sources'][0]['url']==PERSONAL

def test_institution_is_read_after_official_and_returns_attribution():
    search=Search([{'url':INSTITUTION,'title':'DNV report'},{'url':OFFICIAL,'title':'factory'},{'url':PERSONAL,'title':'solar'}])
    fetch=Fetch({OFFICIAL:(OFFICIAL,f'<p>{MODEL} Snow load: 5400 Pa.</p>'),
                 INSTITUTION:(INSTITUTION,f'<title>DNV product test</title><meta property="article:published_time" content="2026-01-02"><p>{MODEL} Wind load: 2400 Pa. Snow load: 5400 Pa.</p>')})
    r=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa','snow_load_pa','hail_resistance_mm'],lambda *x:None)
    assert fetch.visits==[OFFICIAL,INSTITUTION]
    assert [t['phase'] for t in r['trace']]==['manufacturer','institution']
    assert r['profile'].wind_load_pa==2400
    e=r['profile'].parameter_evidence['wind_load_pa'][0]
    assert e['publisher']=='DNV' and e['source_tier']=='institution' and e['published_at']=='2026-01-02'
    assert e['source_url']==INSTITUTION and PERSONAL not in fetch.visits

def test_institution_mentioning_model_elsewhere_cannot_supply_generic_number():
    search=Search([{'url':OFFICIAL,'title':'factory'},{'url':INSTITUTION,'title':'DNV news'}])
    fetch=Fetch({OFFICIAL:(OFFICIAL,f'<p>{MODEL} Snow load: 5400 Pa.</p>'),
                 INSTITUTION:(INSTITUTION,f'<h1>Discussion of {MODEL}</h1><p>Other solar modules have wind load 4000 Pa.</p>')})
    r=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa','hail_resistance_mm'],lambda *x:None)
    assert r['profile'].wind_load_pa is None
    assert r['profile'].snow_load_pa==5400

def test_personal_comments_inside_official_page_are_not_parameter_evidence():
    search=Search([{'url':OFFICIAL,'title':'factory'}])
    fetch=Fetch({OFFICIAL:(OFFICIAL,f'<p>{MODEL} Snow load: 5400 Pa.</p><div id="comments"><div><p>{MODEL} Wind load: 9999 Pa.</p><a href="{PERSONAL}">personal</a></div></div>')})
    r=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa','hail_resistance_mm'],lambda *x:None)
    assert r['profile'].wind_load_pa is None
    assert r['profile'].snow_load_pa==5400
    assert PERSONAL not in fetch.visits

def test_redirect_to_personal_is_blocked_before_request():
    visits=[]
    def transport(req):
        visits.append(str(req.url));return httpx.Response(302,headers={'Location':PERSONAL})
    fetch=PublicFetcher(transport=httpx.MockTransport(transport),
                        resolver=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))],
                        policy_guard=lambda url:SourcePolicy('JA Solar').require(url,'manufacturer'))
    with pytest.raises(ProviderError):fetch.fetch(OFFICIAL)
    assert visits==[OFFICIAL]

def test_even_custom_fetcher_unapproved_final_url_cannot_supply_values():
    search=Search([{'url':OFFICIAL,'title':'factory'}])
    fetch=Fetch({OFFICIAL:(PERSONAL,f'<p>{MODEL} Wind load: 9999 Pa.</p>')})
    r=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa'],lambda *x:None)
    assert r['profile'] is None
    assert r['sources'][0]['status']=='read_failed'

def test_ja_annotation_cannot_take_front_range_for_rear_wind():
    text='Maximum static load on the back side of 2400 Pa (i.e. wind load) and a maximum static load on the front side of either 2400 Pa or 5400 Pa (i.e. wind and snow load), depending on the modules type.'
    r=DatasheetExtraction();extract_page(text,1,OFFICIAL,r)
    assert r.values()['wind_load_pa']==2400
    assert r.values()['back_static_load_pa']==2400
    assert 'snow_load_pa' not in r.values()
    assert all(e['value']==2400 for e in r.evidence['wind_load_pa'])

def test_explicit_hazards_also_write_mechanical_values_without_conversion():
    r=DatasheetExtraction();extract_page('Test load pressure: 5400Pa Max from the front side (snow) and 2400Pa from the rear (wind)',1,OFFICIAL,r)
    values=r.values()
    assert values['front_static_load_pa']==values['snow_load_pa']==5400
    assert values['back_static_load_pa']==values['wind_load_pa']==2400
    assert r.evidence['front_static_load_pa'][0]['load_basis']=='test'


def public_fetch(transport):
    return PublicFetcher(transport=httpx.MockTransport(transport),
        resolver=lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])


@pytest.mark.parametrize('status,body,code',[(406,'<script src="/_fec_sbu/fec_wrapper.js"></script>','access_challenge'),
    (403,'Access denied','access_denied'),(404,'Not found','document_unavailable')])
def test_fetch_failure_reports_http_status_without_retrying_denials(status,body,code):
    visits=[]
    def transport(req):
        visits.append(str(req.url));return httpx.Response(status,text=body)
    with pytest.raises(DocumentFetchError) as error:public_fetch(transport).fetch(OFFICIAL)
    assert error.value.code==code and error.value.http_status==status
    assert f'HTTP {status}' in str(error.value)
    assert visits==[OFFICIAL]


def test_temporary_http_failure_can_recover_once(monkeypatch):
    monkeypatch.setattr('agent.time.sleep',lambda seconds:None)
    visits=[]
    def transport(req):
        visits.append(str(req.url))
        return httpx.Response(503 if len(visits)==1 else 200,content=b'%PDF-test',headers={'content-type':'application/pdf'})
    assert public_fetch(transport).fetch(OFFICIAL)[1]==b'%PDF-test'
    assert len(visits)==2


def test_timeout_retry_is_bounded_and_distinct_from_missing_document():
    visits=[]
    def transport(req):
        visits.append(str(req.url));raise httpx.ReadTimeout('fixture',request=req)
    with pytest.raises(DocumentFetchError) as error:public_fetch(transport).fetch(OFFICIAL)
    assert error.value.code=='timeout' and error.value.http_status is None
    assert len(visits)==2


def test_agent_retains_structured_challenge_reason_and_searches_pdf():
    search=Search([{'url':OFFICIAL,'title':'factory'}])
    fetch=public_fetch(lambda req:httpx.Response(406,text='<script src="/_fec_sbu/fec_wrapper.js"></script>'))
    result=make_agent(fetch,search).run(MODEL,'JA Solar',['wind_load_pa'],lambda *x:None)
    source=result['sources'][0]
    assert source['failure_code']=='access_challenge' and source['http_status']==406
    assert result['status']=='source_unavailable' and result['profile'] is None
    assert 'site:jasolar.com' in search.prompts[0] and 'filetype:pdf' in search.prompts[0]
