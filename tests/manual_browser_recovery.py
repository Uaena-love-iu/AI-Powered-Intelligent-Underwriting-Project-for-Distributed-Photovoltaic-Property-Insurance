"""Isolated UI acceptance server: synthetic material, no external model calls.

Run from the project root with `.venv/Scripts/python tests/manual_browser_recovery.py`.
Only port 8002 and a temporary database are used. The first vision call simulates
an HTTP 401; subsequent calls succeed so the real recovery controls can be tested.
"""
import os
from pathlib import Path
import runpy
import sys
import tempfile
import base64

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.chdir(ROOT)
directory = Path(tempfile.mkdtemp(prefix="pv-recovery-acceptance-"))
os.environ.update(PV_CASE_DB_PATH=str(directory / "cases.sqlite3"), PV_AUTH_ENABLED="false",
                 PV_VLM_BASE_URL="https://example.test/v1", PV_VLM_API_KEY="synthetic-test-only",
                 PV_VLM_MODEL="synthetic-test", PV_AMAP_WEB_SERVICE_KEY="")

from app.main import app
from app.api.ocr_routes import get_ocr_provider
from app.api.routes import get_vision_provider
from app.api.component_dependencies import get_underwriting_component_provider
from app.providers.vision import classification
from PIL import Image

fixtures = runpy.run_path(str(ROOT / "tests/integration/test_job_recovery_api.py"))


class OnceFault(fixtures["FaultVision"]):
    def analyze_with_watermark(self, item):
        try:
            return super().analyze_with_watermark(item)
        finally:
            self.fail = False


ocr, vision = fixtures["CountingOcr"](), OnceFault()
app.dependency_overrides[get_ocr_provider] = lambda: ocr
app.dependency_overrides[get_vision_provider] = lambda: vision
app.dependency_overrides[get_underwriting_component_provider] = lambda: None
classification.classify_material = lambda *args: {
    "category": "component_nameplate", "suggested_category": "component_nameplate",
    "confidence": .99, "reason": "模拟测试材料", "capture_view": "unknown", "model": "synthetic-test"}
Image.new("RGB", (300,200), "white").save(directory / "synthetic-component.png")
print("SYNTHETIC_IMAGE=" + str(directory / "synthetic-component.png"), flush=True)

from fastapi.responses import HTMLResponse


@app.get('/acceptance', response_class=HTMLResponse)
def acceptance():
    encoded = base64.b64encode((directory / "synthetic-component.png").read_bytes()).decode()
    return '''<!doctype html><meta charset="utf-8"><title>隔离的异常恢复验收</title>
    <h1>模拟材料测试（不调用外部模型）</h1><button id="load">载入模拟案件</button>
    <script>document.getElementById('load').onclick=()=>{
      const bytes=Uint8Array.from(atob(''' + repr(encoded) + '''),c=>c.charCodeAt(0));
      const file=new File([bytes],'synthetic-component.png',{type:'image/png',lastModified:1000});
      const request=indexedDB.open('pv-evaluation-replay',2);
      request.onupgradeneeded=()=>{for(const name of ['workspace','cases'])if(!request.result.objectStoreNames.contains(name))request.result.createObjectStore(name,name==='cases'?{keyPath:'id'}:{});};
      request.onsuccess=()=>{const db=request.result,tx=db.transaction('workspace','readwrite');
        tx.objectStore('workspace').put({workId:'synthetic-acceptance',items:[{id:'synthetic-image',file,fileBytes:bytes.buffer,
          category:'component_nameplate',categorySource:'human',categoryNote:'模拟验收材料',status:'pending',
          feedback:{},ocrResults:[],visionResults:[],results:[],additionalCategories:[],captureView:'unknown'}],
          selected:'synthetic-image',running:false,fullCase:null,caseView:'materials',reportItems:[],ruleDraft:null},'current');
        tx.oncomplete=()=>{db.close();location.href='/evaluation';};};};</script>'''

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
