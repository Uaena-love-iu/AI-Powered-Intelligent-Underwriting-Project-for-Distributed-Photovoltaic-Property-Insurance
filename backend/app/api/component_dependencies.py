"""All component entry points use the delivered workbench."""
from pathlib import Path

from app.providers.component import ComponentProvider

PROJECT_ROOT = Path(__file__).resolve().parents[3]

def get_component_provider() -> ComponentProvider:
    from app.providers.component_workbench import ComponentWorkbenchProvider
    from app.storage.cases import CaseRepository
    return ComponentWorkbenchProvider(case_repository=CaseRepository())

get_underwriting_component_provider = get_component_provider
