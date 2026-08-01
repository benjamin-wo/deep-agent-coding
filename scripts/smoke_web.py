"""One-off smoke test for the web app endpoints (needs full deps)."""
import json
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("E2B_API_KEY", "test")
os.environ.setdefault("GH_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("DATA_DIR", "/tmp/dac_data")
# Web app users for the auth-gate section below.
os.environ.setdefault("WEB_APP_USERS", json.dumps({"alice": "code123"}))
os.environ.pop("DATABASE_URL", None)  # SQLite fallback in this smoke run

import main  # noqa: E402
import web_auth  # noqa: E402

# Patch the heavy/remote pieces so the endpoints are testable in isolation.
main.run_turn = lambda chat_id, text: {"type": "reply", "text": f"echo:{text}@session:{chat_id}"}
main.transcribe_audio = lambda data, mime: "[transcribed audio]"

from fastapi.testclient import TestClient  # noqa: E402

# `with` is required: it runs the FastAPI lifespan, which seeds web-app users.
with TestClient(main.app) as client:

    # 1. Static index served at /
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text, r.status_code
    print("OK  GET / serves index.html")

    # 2. Status: auth required (users configured)
    r = client.get("/api/web/status")
    assert r.json() == {"auth_required": True}, r.json()
    print("OK  GET /api/web/status")

    # 3. Auth gate: message without token -> 401
    r = client.post("/api/web/message", json={"session_id": "s", "text": "hi"})
    assert r.status_code == 401, r.status_code
    print("OK  auth gate blocks anonymous message")

    # 4. Login: wrong user / wrong code -> 401
    assert client.post("/api/web/login", json={"username": "alice", "code": "wrong"}).status_code == 401
    assert client.post("/api/web/login", json={"username": "mallory", "code": "code123"}).status_code == 401
    print("OK  bad credentials rejected")

    # 5. Login: good credentials -> token
    r = client.post("/api/web/login", json={"username": "alice", "code": "code123"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    print("OK  login returns token")

    # 6. Message round-trip with auth (SSE streaming)
    with client.stream(
        "POST", "/api/web/message",
        json={"session_id": "web-test", "text": "hello"},
        headers={"X-Auth-Token": token},
    ) as r:
        assert r.status_code == 200, r.status_code
        assert r.headers.get("content-type", "").startswith("text/event-stream")
        body = "".join(r.iter_text())
    assert "event: status" in body and '"thinking"' in body, body
    assert "event: result" in body and "echo:hello@session:web-test" in body, body
    assert "event: done" in body, body
    print("OK  POST /api/web/message streams SSE with auth:", body.replace(chr(10), " ")[:120])

    # 7. Message validation
    r = client.post("/api/web/message", json={"session_id": "", "text": "hi"}, headers={"X-Auth-Token": token})
    assert r.status_code == 400
    print("OK  message validation (400)")

    # 8. Transcription round-trip
    r = client.post("/api/web/transcribe", content=b"fakeaudio", headers={"X-Audio-Mime": "audio/ogg", "X-Auth-Token": token})
    assert r.status_code == 200 and r.json()["text"] == "[transcribed audio]", r.json()
    print("OK  POST /api/web/transcribe")

    # 9. History: the turn we sent above should be persisted (user + agent).
    r = client.get(f"/api/web/history?session_id=web-test", headers={"X-Auth-Token": token})
    turns = r.json()["turns"]
    assert len(turns) == 2 and turns[0]["role"] == "user" and turns[1]["role"] == "agent", turns
    print("OK  GET /api/web/history persists turns")

    # 10. Artifacts: an agent reply with a mermaid block should auto-save one.
    main.run_turn = lambda chat_id, text: {
        "type": "reply",
        "text": "Diagram:\n```mermaid\nflowchart LR\nA-->B\n```",
    }
    with client.stream(
        "POST", "/api/web/message",
        json={"session_id": "web-art", "text": "draw"},
        headers={"X-Auth-Token": token},
    ) as r:
        body = "".join(r.iter_text())
    assert "event: result" in body
    r = client.get("/api/web/artifacts?session_id=web-art", headers={"X-Auth-Token": token})
    arts = r.json()["artifacts"]
    assert len(arts) == 1 and arts[0]["type"] == "diagram" and arts[0]["saved"] is True, arts
    print("OK  GET /api/web/artifacts auto-saves mermaid diagram")

    # 11. Manual save toggle.
    r = client.post(f"/api/web/artifacts/{arts[0]['id']}/save?saved=0", headers={"X-Auth-Token": token})
    assert r.json()["saved"] is False
    print("OK  artifact save toggle")

web_auth.close()
print("ALL WEB SMOKE TESTS PASSED")
