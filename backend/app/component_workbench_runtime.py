"""Load the bundled standalone workbench once, shared by UI and underwriting."""

import sys
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_ROOT = PROJECT_ROOT / "component-model-workbench"


@lru_cache(maxsize=1)
def runtime():
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    root = str(WORKBENCH_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    import server

    if Path(server.__file__).resolve() != WORKBENCH_ROOT / "server.py":
        raise RuntimeError("Component workbench module name collision")
    return server
