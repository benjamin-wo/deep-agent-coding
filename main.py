import os
import logging

import httpx
from fastapi import FastAPI, Request, Header, HTTPException

from agent import run_turn
from multimodal import describe_image, transcribe_audio, describe_video

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("deepagent-telegram")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Telegram echoes this back in a header on every webhook call, so you can
# verify requests actually came from Telegram and not a random POST to your URL.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
TELEGRAM_FILE_API = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()


async def send_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


async def download_telegram_file(file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id})
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]
        file_resp = await client.get(f"{TELEGRAM_FILE_API}/{file_path}")
        file_resp.raise_for_status()
        return file_resp.content


async def _extract_user_text(message: dict) -> str | None:
    """Turn whatever kind of Telegram message this is into text for the
    DeepSeek agent. Images/audio go through Gemini first (see multimodal.py).
    Returns None for message types we don't handle (stickers, documents, etc)."""
    if "text" in message:
        return message["text"]

    if "photo" in message:
        file_id = message["photo"][-1]["file_id"]  # largest resolution
        image_bytes = await download_telegram_file(file_id)
        caption = message.get("caption", "")
        description = describe_image(image_bytes, "image/jpeg", caption)
        return f"[Image received]\n{description}"

    if "voice" in message:
        file_id = message["voice"]["file_id"]
        audio_bytes = await download_telegram_file(file_id)
        transcript = transcribe_audio(audio_bytes, "audio/ogg")
        return f"[Voice message transcript]\n{transcript}"

    if "audio" in message:
        file_id = message["audio"]["file_id"]
        mime_type = message["audio"].get("mime_type", "audio/mpeg")
        audio_bytes = await download_telegram_file(file_id)
        transcript = transcribe_audio(audio_bytes, mime_type)
        return f"[Audio file transcript]\n{transcript}"

    if "video" in message:
        file_id = message["video"]["file_id"]
        mime_type = message["video"].get("mime_type", "video/mp4")
        video_bytes = await download_telegram_file(file_id)
        caption = message.get("caption", "")
        description = describe_video(video_bytes, mime_type, caption)
        return f"[Video received]\n{description}"

    if "video_note" in message:
        file_id = message["video_note"]["file_id"]
        video_bytes = await download_telegram_file(file_id)
        description = describe_video(video_bytes, "video/mp4")
        return f"[Video note received]\n{description}"

    if "document" in message:
        doc = message["document"]
        mime_type = doc.get("mime_type", "")
        if mime_type.startswith("image/"):
            file_id = doc["file_id"]
            image_bytes = await download_telegram_file(file_id)
            caption = message.get("caption", "")
            description = describe_image(image_bytes, mime_type, caption)
            return f"[Image document received]\n{description}"
        elif mime_type.startswith("video/"):
            file_id = doc["file_id"]
            video_bytes = await download_telegram_file(file_id)
            caption = message.get("caption", "")
            description = describe_video(video_bytes, mime_type, caption)
            return f"[Video document received]\n{description}"
        elif mime_type.startswith("audio/"):
            file_id = doc["file_id"]
            audio_bytes = await download_telegram_file(file_id)
            transcript = transcribe_audio(audio_bytes, mime_type)
            return f"[Audio document received]\n{transcript}"

    return None


@app.get("/")
async def health():
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(default=""),
):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="bad secret token")

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return {"ok": True}

    chat_id = message["chat"]["id"]

    try:
        user_text = await _extract_user_text(message)
    except Exception:
        logger.exception("failed to process incoming message")
        await send_message(chat_id, "Couldn't process that message -- try again?")
        return {"ok": True}

    if user_text is None:
        return {"ok": True}  # ignore stickers, documents, etc.

    try:
        reply = run_turn(chat_id, user_text)
    except Exception:
        logger.exception("agent invocation failed")
        reply = "Something went wrong on my end -- try again in a moment."

    await send_message(chat_id, reply)
    return {"ok": True}


@app.get("/webhook-info")
async def webhook_info():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{TELEGRAM_API}/getWebhookInfo")
        return resp.json()
