import json
from copy import deepcopy
from app.main import app
from app.api.routes import get_vision_provider
from app.contracts import InstallationAnalysis
from app.contracts.findings import EvidenceObservation
from integration.test_case_component_review import client, ClearVision, png
from integration.test_revision_api import done
from unit.test_evidence_coverage import finding


def png(color):
    from io import BytesIO
    from PIL import Image, ImageDraw
    image=Image.new('RGB',(1200,900),color);draw=ImageDraw.Draw(image)
    for x in range(0,1200,40):draw.line((x,0,x,900),fill='black',width=5)
    for y in range(0,900,40):draw.line((0,y,1200,y),fill='gray',width=5)
    out=BytesIO();image.save(out,format='PNG');return out.getvalue()


class CoverageVision(ClearVision):
    def analyze_installation(self, materials, **kwargs):
        return InstallationAnalysis(areas=[dict(area_id='屋面A',scene_type='building_roof',status='confirmed',material_ids=[m.material.material_id for m in materials],usage='住宅',supporting_structure='建筑屋面',evidence_text='模拟：屋面与阵列关系清楚',evidence_regions=[dict(material_id=materials[0].material.material_id,bbox=dict(x_min=.1,y_min=.1,x_max=.9,y_max=.9,coordinate_space='normalized_0_1'),description='模拟屋面结构')])],provider=self.name,model=self.model_name)

    def analyze(self,item):
        fs=super().analyze(item)
        if item.material.category.value=='roof_connection':
            # Test-only service: filenames choose a deterministic fixture outcome.
            visible='补拍' in item.material.file_name
            fs.append(finding(item.material.material_id,{'support_context':'visible','connection_node':'visible','anchorage':'visible' if visible else 'occluded'}))
        return fs


def submit(client,id_,parent=None,complete=False):
    body=dict(case_id=id_,project=dict(project_name='模拟证据覆盖验收',insured_name=None,project_type='unknown',installation_type='unknown',site_address=None,longitude=None,latitude=None,proposed_start_date=None,component_model=None),rule_selection={'selected_rule_ids':['connection']},materials=[{'material_id':'node','category':'roof_connection','additional_categories':['panorama']},{'material_id':'repeat','category':'roof_connection'}])
    if parent:body['revision']=dict(parent_case_id=parent,actor='模拟核验员',reason='补拍原先被遮挡的锚固部位')
    color='blue' if complete else 'white'
    return client.post('/api/v1/underwriting/jobs',data={'manifest':json.dumps(body)},files=[('files',('锚固补拍.png' if complete else '连接近景.png',png(color),'image/png')),('files',('同图重复.png',png(color),'image/png'))])


def test_async_duplicate_gap_retake_and_round_audit(client):
    app.dependency_overrides[get_vision_provider]=lambda:CoverageVision()
    first=done(client,submit(client,'coverage-first'))
    assert first['decision']['decision']=='request_more',first['decision']
    assert first['evidence_coverage']['duplicates']==[{'material_id':'repeat','duplicate_of':'node'}]
    row=next(r for r in first['evidence_coverage']['rows'] if r['rule_id']=='connection')
    assert row['status']=='insufficient' and len(row['customer_requests'])==1
    report=client.get('/api/v1/cases/coverage-first/report').text
    assert '材料覆盖与补拍清单' in report and '锚固' in report and '不等于故意隐瞒' in report
    frozen=client.get('/api/v1/cases/coverage-first').json()
    second=done(client,submit(client,'coverage-second',parent='coverage-first',complete=True))
    row=next(r for r in second['evidence_coverage']['rows'] if r['rule_id']=='connection')
    assert row['status']=='satisfied',row
    assert not second['evidence_coverage']['customer_requests']
    assert second['decision']['decision']=='manual_review', json.dumps(second['decision']['missing_requirements'],ensure_ascii=True) # This remains a scoped inspection.
    assert client.get('/api/v1/cases/coverage-first').json()['case']==frozen['case']
    delta=client.get('/api/v1/cases/coverage-second/rounds').json()['rounds'][-1]['comparison']
    assert any(r['before']['status']=='insufficient' and r['after']['status']=='satisfied' for r in delta['evidence_coverage_changes'])
    assert client.get('/api/v1/cases/coverage-first/materials/node/source').content==png('white')
    assert client.get('/api/v1/cases/coverage-second/materials/node/source').content==png('blue')
