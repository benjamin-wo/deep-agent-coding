"""Persistent user + session store for the web app.

Per wayfinder ticket #5 (revised): per-user codes, with sessions stored in
Postgres (DATABASE_URL). Falls back to a local SQLite file (DATA_DIR/auth.sqlite)
for local dev and unit tests so the module stays runnable without Postgres.

Security notes (short-term model per the ticket):
- Codes are never stored in plaintext: salted PBKDF2-HMAC-SHA256 (stdlib).
- Session tokens are random 32-byte URL-safe strings; they expire after 7 days.
- Production-grade hardening (bcrypt/argon2, OAuth, roles) layers onto the
  same schema later -- the ticket explicitly deferred that.
"""

import hashlib
import os
import secrets
import sqlite3
import threading
from pathlib import Path

TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days
_ITERATIONS = 200_000

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL
);
"""

_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    code_hash TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""


def _hash_code(code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt.encode("utf-8"), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def _verify_code(code: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt.encode("utf-8"), int(iterations))
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


class AuthStore:
    """Sync store; small queries. Async callers should wrap calls in
    asyncio.to_thread so the event loop isn't blocked."""

    def __init__(self, database_url: str | None = None, data_dir: str | None = None):
        self.database_url = (database_url if database_url is not None
                             else os.environ.get("DATABASE_URL", "")).strip()
        self.data_dir = data_dir or os.environ.get("DATA_DIR", "/data")
        self._pg = None
        self._sqlite = None
        self._lock = threading.Lock()
        self._init_schema()

    # -- setup ------------------------------------------------------------

    def _init_schema(self) -> None:
        if self.database_url:
            import psycopg
            self._pg = psycopg.connect(self.database_url, autocommit=True)
            with self._pg.cursor() as cur:
                cur.execute(_PG_SCHEMA)
        else:
            Path(self.data_dir).mkdir(parents=True, exist_ok=True)
            self._sqlite = sqlite3.connect(str(Path(self.data_dir) / "auth.sqlite"))
            self._sqlite.executescript(_SQLITE_SCHEMA)
            self._sqlite.commit()

    def close(self) -> None:
        if self._pg is not None:
            self._pg.close()
            self._pg = None
        if self._sqlite is not None:
            self._sqlite.close()
            self._sqlite = None

    # -- users ------------------------------------------------------------

    def seed_users(self, users: dict[str, str]) -> None:
        """Upsert users from a {username: code} mapping (e.g. WEB_APP_USERS).
        Existing users keep their stored hash; new users are hashed."""
        with self._lock:
            for username, code in users.items():
                username = username.strip()
                code = str(code).strip()
                if not username or not code:
                    continue
                if self._user_exists(username):
                    continue
                self._insert_user(username, _hash_code(code))

    def _user_exists(self, username: str) -> bool:
        if self._pg is not None:
            with self._pg.cursor() as cur:
                cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
                return cur.fetchone() is not None
        row = self._sqlite.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        return row is not None

    def _insert_user(self, username: str, code_hash: str) -> None:
        if self._pg is not None:
            with self._pg.cursor() as cur:
                cur.execute(
                    "INSERT INTO users (username, code_hash) VALUES (%s, %s) ON CONFLICT (username) DO NOTHING",
                    (username, code_hash),
                )
        else:
            self._sqlite.execute(
                "INSERT OR IGNORE INTO users (username, code_hash) VALUES (?, ?)",
                (username, code_hash),
            )
            self._sqlite.commit()

    def verify_user(self, username: str, code: str) -> bool:
        """True if username exists and code matches its stored hash."""
        with self._lock:
            if self._pg is not None:
                with self._pg.cursor() as cur:
                    cur.execute("SELECT code_hash FROM users WHERE username = %s", (username,))
                    row = cur.fetchone()
            else:
                row = self._sqlite.execute(
                    "SELECT code_hash FROM users WHERE username = ?", (username,)
                ).fetchone()
        if not row:
            return False
        return _verify_code(code, row[0])

    # -- sessions ---------------------------------------------------------

    def create_session(self, username: str, now: float | None = None) -> str:
        token = secrets.token_urlsafe(32)
        expires = (now if now is not None else __import__("time").time()) + TOKEN_TTL_SECONDS
        with self._lock:
            if self._pg is not None:
                with self._pg.cursor() as cur:
                    cur.execute(
                        "INSERT INTO sessions (token, username, expires_at) VALUES (%s, %s, %s)",
                        (token, username, expires),
                    )
            else:
                self._sqlite.execute(
                    "INSERT INTO sessions (token, username, expires_at) VALUES (?, ?, ?)",
                    (token, username, expires),
                )
                self._sqlite.commit()
        return token

    def get_session_username(self, token: str, now: float | None = None) -> str | None:
        if not token:
            return None
        now_val = now if now is not None else __import__("time").time()
        with self._lock:
            if self._pg is not None:
                with self._pg.cursor() as cur:
                    cur.execute("SELECT username, expires_at FROM sessions WHERE token = %s", (token,))
                    row = cur.fetchone()
            else:
                row = self._sqlite.execute(
                    "SELECT username, expires_at FROM sessions WHERE token = ?", (token,)
                ).fetchone()
        if not row:
            return None
        username, expires_at = row
        if now_val > float(expires_at):
            self.delete_session(token)
            return None
        return username

    def delete_session(self, token: str) -> None:
        with self._lock:
            if self._pg is not None:
                with self._pg.cursor() as cur:
                    cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            else:
                self._sqlite.execute("DELETE FROM sessions WHERE token = ?", (token,))
                self._sqlite.commit()

    def count_users(self) -> int:
        if self._pg is not None:
            with self._pg.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM users")
                return cur.fetchone()[0]
        return self._sqlite.execute("SELECT COUNT(*) FROM users").fetchone()[0]
