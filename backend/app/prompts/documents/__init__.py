import json
from pathlib import Path

from app.material_catalog import canonical_category

ROOT = Path(__file__).parent
DEFAULT_PATH = ROOT.parents[3] / 'data' / 'vision_prompt_default.json'
VERSION = 'v3 · 照片与文档分流'

def builtin():
 return {'document_prompt': (ROOT/'common.txt').read_text(encoding='utf-8'), 'document_specialized_prompts': {c:(ROOT/f'{c}.txt').read_text(encoding='utf-8') for c in ('project_document','equipment_inventory')}}

def load(path=None):
 path=path or DEFAULT_PATH
 bundle=builtin()
 if path.is_file():
  saved=json.loads(path.read_text(encoding='utf-8'))
  bundle['document_prompt']=saved.get('document_prompt',bundle['document_prompt'])
  bundle['document_specialized_prompts'].update(saved.get('document_specialized_prompts',{}))
 return bundle

def compose(common,specialized,category,contract):
 return '\n\n'.join([common.strip(),'【当前材料】'+canonical_category(category),specialized.strip(),contract])

def material_prompt(category,contract):
 bundle=load();return compose(bundle['document_prompt'],bundle['document_specialized_prompts'][canonical_category(category)],category,contract)
