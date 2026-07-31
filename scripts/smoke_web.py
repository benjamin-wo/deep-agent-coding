"""One-off smoke test for the web app endpoints (needs full deps)."""
import os

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token")
os.environ.setdefault("DEEPSEEK_API_KEY", "test")
os.environ.setdefault("E2B_API_KEY", "test")
os.environ.setdefault("GH_TOKEN", "test")
os.environ.setdefault("GEMINI_API_KEY", "test")
os.environ.setdefault("DATA_DIR", "/tmp/dac_data")
os.environ.setdefault("WEB_APP_PASSCODE", "")

import main  # noqa: E402

# Patch the heavy/remote pieces so the endpoints are testable in isolation.
main.run_turn = lambda chat_id, text: {"type": "reply", "text": f"echo:{text}@session:{chat_id}"}
main.transcribe_audio = lambda data, mime: "[transcribed audio]"

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(main.app)

# 1. Static index served at /
r = client.get("/")
assert r.status_code == 200 and "<html" in r.text, r.status_code
print("OK  GET / serves index.html")

# 2. Status: auth not required (no passcode)
r = client.get("/api/web/status")
assert r.json() == {"auth_required": False}, r.json()
print("OK  GET /api/web/status")

# 3. Message round-trip
r = client.post("/api/web/message", json={"session_id": "web-test", "text": "hello"})
assert r.status_code == 200
assert r.json() == {"type": "reply", "text": "echo:hello@session:web-test"}, r.json()
print("OK  POST /api/web/message ->", r.json())

# 4. Message validation
r = client.post("/api/web/message", json={"session_id": "", "text": "hi"})
assert r.status_code == 400
print("OK  message validation (400)")

# 5. Transcription round-trip
r = client.post("/api/web/transcribe", content=b"fakeaudio", headers={"X-Audio-Mime": "audio/ogg"})
assert r.status_code == 200 and r.json()["text"] == "[transcribed audio]", r.json()
print("OK  POST /api/web/transcribe")

# 6. With passcode set, endpoints require auth
main.WEB_APP_PASSCODE = "secret"
r = client.post("/api/web/message", json={"session_id": "s", "text": "hi"})
assert r.status_code == 401, r.status_code
r = client.post("/api/web/login", json={"passcode": "wrong"})
assert r.status_code == 401
r = client.post("/api/web/login", json={"passcode": "secret"})
assert r.status_code == 200
token = r.json()["token"]
r = client.post("/api/web/message", json={"session_id": "s", "text": "hi"}, headers={"X-Auth-Token": token})
assert r.status_code == 200
print("OK  passcode auth gate works")
main.WEB_APP_PASSCODE = ""

print("ALL WEB SMOKE TESTS PASSED")
