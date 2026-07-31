"""Passcode auth for the web app (optional).

If WEB_APP_PASSCODE is set, the web UI requires a login. Successful login
returns a random bearer token kept in an in-memory store with an expiry.
Restarting the service invalidates tokens (user just logs in again).

Pure module (no FastAPI) so it's unit-testable.
"""

import secrets
import time

_TOKEN_TTL_SECONDS = 7 * 24 * 3600  # 7 days

_tokens: dict[str, float] = {}


def login(provided: str, expected: str, now: float | None = None) -> str | None:
    """Verify a passcode. Returns a bearer token on success, else None."""
    if not expected:
        return None  # auth disabled; caller should not be calling login
    if not provided:
        return None
    if not secrets.compare_digest(provided, expected):
        return None
    token = secrets.token_urlsafe(32)
    _tokens[token] = (now if now is not None else time.time()) + _TOKEN_TTL_SECONDS
    return token


def is_valid(token: str | None, now: float | None = None) -> bool:
    """True if the token is present and not expired (expired tokens are purged)."""
    if not token:
        return False
    now_val = now if now is not None else time.time()
    expiry = _tokens.get(token)
    if expiry is None:
        return False
    if now_val > expiry:
        _tokens.pop(token, None)
        return False
    return True


def revoke(token: str | None) -> None:
    if token:
        _tokens.pop(token, None)


def clear() -> None:
    """Test helper: drop all issued tokens."""
    _tokens.clear()
