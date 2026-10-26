"""Small local account and bearer-session store for the demo service."""

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from app.storage import CaseRepository

Role = Literal["viewer", "underwriter", "admin"]
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_HASH_BYTES = 32
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


class UserAlreadyExists(Exception):
    """Raised when an account name is already present."""


class LastActiveAdmin(Exception):
    """Raised when an operation would remove the final active administrator."""


class UserNotFound(Exception):
    """Raised when a password reset targets an unknown account."""


@dataclass(frozen=True, slots=True)
class AuthUser:
    username: str
    role: Role


def auth_enabled() -> bool:
    value = os.getenv("PV_AUTH_ENABLED", "false").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError("PV_AUTH_ENABLED must be true or false")


class AuthRepository:
    """Store salted password hashes and hashed, expiring session tokens."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = CaseRepository(database_path).database_path

    def ensure_initial_admin(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            count = connection.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0]
        if count:
            return
        username = os.getenv("PV_ADMIN_USERNAME", "").strip()
        password = os.getenv("PV_ADMIN_PASSWORD", "")
        if not username or not password:
            raise ValueError(
                "Set PV_ADMIN_USERNAME and PV_ADMIN_PASSWORD before enabling authentication"
            )
        self.create_user(username=username, password=password, role="admin")

    def create_user(self, *, username: str, password: str, role: Role) -> AuthUser:
        self._validate_credentials(username, password)
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt)
        created_at = datetime.now(UTC).isoformat()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            try:
                with connection:
                    connection.execute(
                        """
                        INSERT INTO auth_users (
                            username, role, password_salt, password_hash, active, created_at
                        ) VALUES (?, ?, ?, ?, 1, ?)
                        """,
                        (username, role, salt.hex(), password_hash.hex(), created_at),
                    )
            except sqlite3.IntegrityError as exc:
                raise UserAlreadyExists(username) from exc
        return AuthUser(username=username, role=role)

    def authenticate(self, *, username: str, password: str) -> AuthUser | None:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            row = connection.execute(
                """
                SELECT role, password_salt, password_hash, active
                FROM auth_users WHERE username = ?
                """,
                (username,),
            ).fetchone()
        if row is None:
            salt = bytes(16)
            hmac.compare_digest(self._password_hash(password, salt), bytes(_HASH_BYTES))
            return None
        role, encoded_salt, encoded_hash, active = row
        candidate = self._password_hash(password, bytes.fromhex(encoded_salt))
        if not active or not hmac.compare_digest(candidate.hex(), encoded_hash):
            return None
        return AuthUser(username=username, role=role)

    def issue_session(self, user: AuthUser, *, lifetime_hours: int = 8) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(hours=lifetime_hours)
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            with connection:
                connection.execute(
                    "INSERT INTO auth_sessions (token_hash, username, expires_at) VALUES (?, ?, ?)",
                    (self._token_hash(token), user.username, expires_at.isoformat()),
                )
                connection.execute(
                    "DELETE FROM auth_sessions WHERE expires_at <= ?",
                    (datetime.now(UTC).isoformat(),),
                )
        return token, expires_at.isoformat()

    def resolve_session(self, token: str) -> AuthUser | None:
        now = datetime.now(UTC).isoformat()
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            row = connection.execute(
                """
                SELECT users.username, users.role
                FROM auth_sessions AS sessions
                JOIN auth_users AS users ON users.username = sessions.username
                WHERE sessions.token_hash = ? AND sessions.expires_at > ? AND users.active = 1
                """,
                (self._token_hash(token), now),
            ).fetchone()
            if row is None:
                with connection:
                    connection.execute(
                        "DELETE FROM auth_sessions WHERE token_hash = ? OR expires_at <= ?",
                        (self._token_hash(token), now),
                    )
                return None
        return AuthUser(username=row[0], role=row[1])

    def revoke_session(self, token: str) -> None:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            with connection:
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?",
                    (self._token_hash(token),),
                )

    def list_users(self) -> list[dict[str, object]]:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT username, role, active, created_at FROM auth_users ORDER BY username"
            ).fetchall()
            return [dict(row) for row in rows]

    def deactivate_user(self, username: str) -> bool:
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            with connection:
                target = connection.execute(
                    "SELECT role, active FROM auth_users WHERE username = ?",
                    (username,),
                ).fetchone()
                if target is None:
                    return False
                if target[0] == "admin" and target[1]:
                    admins = connection.execute(
                        "SELECT COUNT(*) FROM auth_users WHERE role = 'admin' AND active = 1"
                    ).fetchone()[0]
                    if admins <= 1:
                        raise LastActiveAdmin(username)
                cursor = connection.execute(
                    "UPDATE auth_users SET active = 0 WHERE username = ?",
                    (username,),
                )
                connection.execute(
                    "DELETE FROM auth_sessions WHERE username = ?",
                    (username,),
                )
            return cursor.rowcount > 0

    def reset_password(self, *, username: str, password: str) -> None:
        self._validate_credentials(username, password)
        salt = secrets.token_bytes(16)
        encoded_hash = self._password_hash(password, salt).hex()
        with closing(sqlite3.connect(self.database_path, timeout=10)) as connection:
            self._initialize(connection)
            with connection:
                cursor = connection.execute(
                    "UPDATE auth_users SET password_salt = ?, password_hash = ? WHERE username = ?",
                    (salt.hex(), encoded_hash, username),
                )
                if cursor.rowcount == 0:
                    raise UserNotFound(username)
                connection.execute(
                    "DELETE FROM auth_sessions WHERE username = ?",
                    (username,),
                )

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS auth_users (
                username TEXT PRIMARY KEY,
                role TEXT NOT NULL CHECK (role IN ('viewer', 'underwriter', 'admin')),
                password_salt TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                username TEXT NOT NULL REFERENCES auth_users(username) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry
                ON auth_sessions(expires_at);
            """
        )

    @staticmethod
    def _validate_credentials(username: str, password: str) -> None:
        if not _USERNAME_PATTERN.fullmatch(username):
            raise ValueError("username must be 3-64 letters, digits, dots, dashes or underscores")
        if len(password) < 12 or len(password) > 256:
            raise ValueError("password must be between 12 and 256 characters")

    @staticmethod
    def _password_hash(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=_SCRYPT_N,
            r=_SCRYPT_R,
            p=_SCRYPT_P,
            dklen=_HASH_BYTES,
        )

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()
