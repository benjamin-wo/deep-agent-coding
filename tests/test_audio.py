import subprocess
from pathlib import Path

import pytest

from audio import normalize_audio


def test_ogg_passthrough():
    data = b"already-ogg-bytes"
    assert normalize_audio(data, "audio/ogg") == data


def test_ffmpeg_missing_passthrough(monkeypatch):
    monkeypatch.setattr("audio.shutil.which", lambda name: None)
    data = b"raw-audio"
    assert normalize_audio(data, "audio/mp4") == data


def test_empty_passthrough():
    assert normalize_audio(b"", "audio/mp4") == b""


def test_ffmpeg_converts(monkeypatch):
    """When ffmpeg is present and succeeds, return its stdout."""
    calls = {}

    def fake_run(cmd, input=None, capture_output=None, timeout=None):
        calls["cmd"] = cmd
        calls["input"] = input
        return subprocess.CompletedProcess(cmd, 0, stdout=b"CONVERTED-OGG", stderr=b"")

    monkeypatch.setattr("audio.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("audio.subprocess.run", fake_run)
    data = b"webm-bytes"
    out = normalize_audio(data, "audio/webm")
    assert out == b"CONVERTED-OGG"
    # ffmpeg got the right args (stdin -> ogg/opus stdout)
    assert "-acodec" in calls["cmd"] and "libopus" in calls["cmd"]
    assert calls["input"] == data


def test_ffmpeg_failure_passthrough(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, timeout=None):
        return subprocess.CompletedProcess(cmd, 1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr("audio.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("audio.subprocess.run", fake_run)
    data = b"webm-bytes"
    assert normalize_audio(data, "audio/webm") == data


def test_ffmpeg_exception_passthrough(monkeypatch):
    def fake_run(cmd, input=None, capture_output=None, timeout=None):
        raise subprocess.TimeoutExpired(cmd, 30)

    monkeypatch.setattr("audio.shutil.which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr("audio.subprocess.run", fake_run)
    data = b"webm-bytes"
    assert normalize_audio(data, "audio/webm") == data


def test_dockerfile_installs_ffmpeg():
    """Guardrail: the container must install ffmpeg or the normalizer can't run."""
    dockerfile = Path(__file__).resolve().parent.parent / "Dockerfile"
    assert dockerfile.exists()
    text = dockerfile.read_text()
    assert "ffmpeg" in text
    assert "apt-get" in text
