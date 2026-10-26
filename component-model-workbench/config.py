"""Server-side configuration; the browser only receives availability flags."""
import os
from pathlib import Path
from dotenv import load_dotenv
from pv_component_lookup.credentials import get_solar_stack_key
from pv_component_lookup.semantic import DatasheetSemanticReader

ROOT=Path(__file__).resolve().parent
load_dotenv(ROOT/'.env',override=False)
if (ROOT.parent / 'pyproject.toml').exists():
    load_dotenv(ROOT.parent / '.env', override=False)

def ai_reader():
    key=os.getenv('PV_COMPONENT_DATASHEET_AI_KEY') or os.getenv('PV_VLM_API_KEY','')
    base=os.getenv('PV_COMPONENT_DATASHEET_AI_BASE_URL') or os.getenv('PV_VLM_BASE_URL','')
    model=os.getenv('PV_COMPONENT_DATASHEET_AI_MODEL') or os.getenv('PV_VLM_MODEL','')
    return DatasheetSemanticReader(base,key,model,cache_dir=ROOT/'private/ai_cache') if key and base and model else None

def solar_key():
    return get_solar_stack_key()
