"""Small persistent cache for public provider data; never stores credentials."""

import hashlib
import json
import sqlite3
from pathlib import Path
from time import time

DEFAULT_CACHE_PATH = Path(__file__).resolve().parents[3] / "outputs/provider-cache.sqlite3"


class ProviderCache:
    def __init__(self, path: Path | str = DEFAULT_CACHE_PATH) -> None:
        self.path = Path(path)

    @staticmethod
    def key(namespace: str, parameters: object) -> str:
        raw = json.dumps(parameters, sort_keys=True, ensure_ascii=False, allow_nan=False)
        return namespace + ":" + hashlib.sha256(raw.encode()).hexdigest()

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.execute("CREATE TABLE IF NOT EXISTS provider_cache (key TEXT PRIMARY KEY, expires REAL NOT NULL, payload TEXT NOT NULL)")
        return connection

    def get(self, key: str) -> tuple[bool, object]:
        try:
            with self._connect() as db:
                row = db.execute("SELECT payload FROM provider_cache WHERE key=? AND expires>?", (key, time())).fetchone()
            return (True, json.loads(row[0])) if row else (False, None)
        except (OSError, sqlite3.Error, ValueError):
            return False, None

    def put(self, key: str, payload: object, ttl: float) -> None:
        try:
            raw = json.dumps(payload, ensure_ascii=False, allow_nan=False)
            with self._connect() as db:
                db.execute("INSERT OR REPLACE INTO provider_cache VALUES (?, ?, ?)", (key, time() + ttl, raw))
                db.execute("DELETE FROM provider_cache WHERE expires < ?", (time(),))
        except (OSError, sqlite3.Error, ValueError, TypeError):
            # Read-only disks/cache failures must not hide a successful lookup.
            pass
