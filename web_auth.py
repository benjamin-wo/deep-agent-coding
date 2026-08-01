"""Auth API for the web app (per-user codes, DB-backed sessions).

Backed by AuthStore (Postgres via DATABASE_URL, SQLite fallback in dev).
Users come from the WEB_APP_USERS env var, e.g.:
    WEB_APP_USERS='{"alice": "code123", "bob": "code456"}'

Pure module (no FastAPI) so it's unit-testable.
"""

import json
import os

from auth_store import AuthStore

_store: AuthStore | None = None


def _get_store() -> AuthStore:
    global _store
    if _store is None:
        _store = AuthStore()
    return _store


def close() -> None:
    global _store
    if _store is not None:
        _store.close()
        _store = None


def load_users_from_env() -> dict[str, str]:
    raw = os.environ.get("WEB_APP_USERS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    except Exception:
        pass
    return {}


def ensure_users() -> None:
    """Seed users from WEB_APP_USERS (called at startup)."""
    users = load_users_from_env()
    if users:
        _get_store().seed_users(users)


def auth_enabled() -> bool:
    """True if any users are configured."""
    return bool(load_users_from_env())


def login(username: str, code: str) -> str | None:
    """Verify username + code. Returns a bearer token on success, else None."""
    username = (username or "").strip()
    code = str(code or "").strip()
    if not username or not code:
        return None
    store = _get_store()
    if not store.verify_user(username, code):
        return None
    return store.create_session(username)


def is_valid(token: str | None) -> str | None:
    """Return the username for a valid (unexpired) token, else None."""
    if not token:
        return None
    return _get_store().get_session_username(token)


def revoke(token: str | None) -> None:
    if token:
        _get_store().delete_session(token)


def clear() -> None:
    """Test helper: reset the store so tests start clean."""
    close()
