import time

import web_auth


def setup_function():
    web_auth.clear()


def test_login_wrong_passcode():
    assert web_auth.login("wrong", "secret") is None


def test_login_empty_passcode():
    assert web_auth.login("", "secret") is None
    assert web_auth.login(None, "secret") is None


def test_login_success_returns_token():
    token = web_auth.login("secret", "secret")
    assert token
    assert web_auth.is_valid(token)


def test_token_not_valid_without_login():
    assert web_auth.is_valid(None) is False
    assert web_auth.is_valid("") is False
    assert web_auth.is_valid("random-token") is False


def test_token_expires():
    now = time.time()
    token = web_auth.login("secret", "secret", now=now)
    assert web_auth.is_valid(token, now=now + 1000)
    # TTL is 7 days; anything past that is expired and purged.
    assert web_auth.is_valid(token, now=now + 8 * 24 * 3600) is False


def test_revoke():
    token = web_auth.login("secret", "secret")
    assert web_auth.is_valid(token)
    web_auth.revoke(token)
    assert web_auth.is_valid(token) is False
