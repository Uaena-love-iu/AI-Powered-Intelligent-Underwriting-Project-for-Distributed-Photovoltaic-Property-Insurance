"""Isolated browser acceptance demo. All vision responses are simulated."""
from pathlib import Path
import os
import sys

ROOT=Path(__file__).resolve().parents[2]
os.chdir(ROOT)
os.environ['PV_AUTH_ENABLED']='false'
os.environ['PV_CASE_DB_PATH']=str(ROOT/'outputs'/'v2-browser-acceptance.sqlite3')
os.environ['PV_VERIFICATION_ENABLED']='true'
sys.path[:0]=[str(ROOT/'backend'),str(ROOT/'tests'/'unit')]
from test_verification_v2 import case, finding, Model, MockOcrProvider
from app.verification.engine import run_loop,recalculate
from app.verification.store import Store
from app.api.routes import get_vision_provider
from app.api.ocr_routes import get_ocr_provider
from app.main import app

class DemoModel(Model):
    def plan_verification(self,ctx):
        if self.mode=='classification':
            result=super().plan_verification(ctx)
            result['question']='【模拟验收】现有照片中的组件表面痕迹是否属于破损？'
            return result
        return super().plan_verification(ctx)

store=Store()
for name,mode in [('demo-review','classification'),('demo-supplement','missing'),('demo-failed','fail')]:
    if store.repo.get_case(name):continue
    c,inputs=case([finding(evidence_coverage=[dict(target='module_surface',status='visible',evidence_text='表面清楚可见，分类仍需确认')])])
    c.case_id=name;c.project.project_name='模拟验收案例（非真实模型识别）'
    c.materials=c.materials[:2];inputs=inputs[:2];c.installation_analysis.areas=c.installation_analysis.areas[:1]
    run_loop(c,DemoModel(mode),inputs);recalculate(c)
    store.repo.save_case(c);store.archive_sources(c.case_id,inputs)
app.dependency_overrides[get_vision_provider]=lambda:DemoModel()
app.dependency_overrides[get_ocr_provider]=lambda:MockOcrProvider()

if __name__=='__main__':
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=8006)
