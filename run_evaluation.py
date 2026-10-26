from pathlib import Path
import subprocess
import sys
if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    raise SystemExit(subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--app-dir", str(root / "backend"), "--host", "127.0.0.1", "--port", "8000"], cwd=root))
