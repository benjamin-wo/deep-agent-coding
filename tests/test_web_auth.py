import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

import auth_store as auth_store_mod
import web_auth

DATA_DIR = "/tmp/dac_auth_test"


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("WEB_APP_USERS", raising=False)
    web_auth.clear()
    yield
    web_auth.clear()


def test_login_wrong_code():
    web_auth.ensure_users()
    assert web_auth.login("alice", "wrong") is None
    assert web_auth.login("", "code") is None
    assert web_auth.login("alice", "") is None


def test_login_success_returns_token_and_username():
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    token = web_auth.login("alice", "code123")
    assert token
    assert web_auth.is_valid(token) == "alice"


def test_token_not_valid_without_login():
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    assert web_auth.is_valid(None) is None
    assert web_auth.is_valid("") is None
    assert web_auth.is_valid("random-token") is None


def test_revoke():
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    token = web_auth.login("alice", "code123")
    assert web_auth.is_valid(token) == "alice"
    web_auth.revoke(token)
    assert web_auth.is_valid(token) is None


def test_sessions_survive_store_reopen():
    """DB-backed: a token stays valid after the store is closed/reopened."""
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    token = web_auth.login("alice", "code123")
    assert web_auth.is_valid(token) == "alice"
    web_auth.close()  # simulate restart
    assert web_auth.is_valid(token) == "alice"  # still valid from DB


def test_unknown_user_rejected():
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    assert web_auth.login("mallory", "code123") is None


def test_auth_enabled():
    assert web_auth.auth_enabled() is False
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    assert web_auth.auth_enabled() is True


def test_codes_stored_hashed_not_plaintext(tmp_path):
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "super-secret-code"})
    web_auth.ensure_users()
    db = sqlite3.connect(str(tmp_path / "auth.sqlite"))
    row = db.execute("SELECT code_hash FROM users WHERE username='alice'").fetchone()
    db.close()
    assert row is not None
    assert "super-secret-code" not in row[0]
    assert row[0].startswith("pbkdf2$")


def test_seed_users_upsert_keeps_existing_hash(tmp_path):
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    db = sqlite3.connect(str(tmp_path / "auth.sqlite"))
    h1 = db.execute("SELECT code_hash FROM users WHERE username='alice'").fetchone()[0]
    db.close()

    # Re-seed with a different code: hash must NOT change (user already exists).
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "different"})
    web_auth.ensure_users()
    db = sqlite3.connect(str(tmp_path / "auth.sqlite"))
    h2 = db.execute("SELECT code_hash FROM users WHERE username='alice'").fetchone()[0]
    db.close()
    assert h1 == h2
    # And the original code still works.
    assert web_auth.login("alice", "code123") is not None
    assert web_auth.login("alice", "different") is None


def test_expired_session_purged(tmp_path):
    os.environ["WEB_APP_USERS"] = json.dumps({"alice": "code123"})
    web_auth.ensure_users()
    store = web_auth._get_store()
    now = time.time()
    token = store.create_session("alice", now=now - auth_store_mod.TOKEN_TTL_SECONDS - 10)
    assert store.get_session_username(token, now=now) is None
    db = sqlite3.connect(str(tmp_path / "auth.sqlite"))
    row = db.execute("SELECT 1 FROM sessions WHERE token=?", (token,)).fetchone()
    db.close()
    assert row is None  # purged
