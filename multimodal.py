"""
Vision/audio front-end: Gemini turns images and voice notes into text, which
then gets handed to the DeepSeek-powered coding agent as a normal message.
Gemini never touches code or repos -- it's purely an input transcriber here.
"""

import base64
import os

from google import genai

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

_client = genai.Client(api_key=GEMINI_API_KEY)

_IMAGE_PROMPT = (
    "Describe this image in detail for a coding assistant that cannot see "
    "images itself. Transcribe any visible text, code, error messages, "
    "stack traces, or UI elements verbatim where possible."
)

_AUDIO_PROMPT = (
    "Transcribe this audio verbatim. Return only the transcript, no commentary."
)

_VIDEO_PROMPT = (
    "Describe this video in detail for a coding assistant that cannot see "
    "videos itself. Describe the sequence of events, any visible text, code, "
    "error messages, stack traces, UI interactions, or terminal output verbatim "
    "where possible."
)


def describe_image(image_bytes: bytes, mime_type: str, caption: str = "") -> str:
    prompt = _IMAGE_PROMPT
    if caption:
        prompt += f"\n\nThe user's caption/instruction: {caption}"
    interaction = _client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {"type": "text", "text": prompt},
            {
                "type": "image",
                "data": base64.b64encode(image_bytes).decode("utf-8"),
                "mime_type": mime_type,
            },
        ],
    )
    return interaction.output_text


def transcribe_audio(audio_bytes: bytes, mime_type: str) -> str:
    interaction = _client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {"type": "text", "text": _AUDIO_PROMPT},
            {
                "type": "audio",
                "data": base64.b64encode(audio_bytes).decode("utf-8"),
                "mime_type": mime_type,
            },
        ],
    )
    return interaction.output_text


def describe_video(video_bytes: bytes, mime_type: str, caption: str = "") -> str:
    prompt = _VIDEO_PROMPT
    if caption:
        prompt += f"\n\nThe user's caption/instruction: {caption}"
    interaction = _client.interactions.create(
        model=GEMINI_MODEL,
        input=[
            {"type": "text", "text": prompt},
            {
                "type": "video",
                "data": base64.b64encode(video_bytes).decode("utf-8"),
                "mime_type": mime_type,
            },
        ],
    )
    return interaction.output_text
