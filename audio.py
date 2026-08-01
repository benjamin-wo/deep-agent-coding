"""Audio normalization for transcription.

Gemini accepts OGG (Opus) + WAV, but browser MediaRecorder produces
webm/ogg/mp4 depending on platform (notably Safari -> mp4/aac), which Gemini
rejects with HTTP 400. Per wayfinder ticket #6, we normalize any uploaded
audio to OGG/Opus with ffmpeg before it reaches Gemini.

Pure module (stdlib only) so it's unit-testable. If ffmpeg is missing or the
conversion fails, we pass the original bytes through unchanged -- the caller
(Gemini) may still succeed on already-supported formats (webm/ogg).
"""

import shutil
import subprocess

# Input is read from stdin; output written to stdout as OGG/Opus.
_FFMPEG_CMD = ["ffmpeg", "-i", "-", "-vn", "-acodec", "libopus", "-f", "ogg", "-"]


def normalize_audio(audio_bytes: bytes, mime_type: str = "audio/ogg", ffmpeg_path: str | None = None) -> bytes:
    """Convert audio bytes to OGG/Opus. Returns the original bytes if ffmpeg
    is unavailable or conversion fails."""
    if not audio_bytes:
        return audio_bytes
    # Already the target format: no conversion needed.
    if mime_type.startswith("audio/ogg"):
        return audio_bytes

    exe = ffmpeg_path or shutil.which("ffmpeg")
    if not exe:
        return audio_bytes

    try:
        proc = subprocess.run(
            [exe, "-i", "-", "-vn", "-acodec", "libopus", "-f", "ogg", "-"],
            input=audio_bytes,
            capture_output=True,
            timeout=30,
        )
    except (subprocess.SubprocessError, OSError):
        return audio_bytes
    if proc.returncode != 0 or not proc.stdout:
        return audio_bytes
    return proc.stdout
