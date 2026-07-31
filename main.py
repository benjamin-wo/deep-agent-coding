import asyncio
import os
import logging
import html
import re

import httpx
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-register Telegram webhook on startup when deployed on Railway or with WEBHOOK_URL
    domain = os.environ.get("WEBHOOK_URL") or os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if domain and TELEGRAM_BOT_TOKEN:
        url = domain if domain.startswith("http") else f"https://{domain}"
        webhook_url = f"{url.rstrip('/')}/telegram/webhook"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                payload = {"url": webhook_url}
                if WEBHOOK_SECRET:
                    payload["secret_token"] = WEBHOOK_SECRET
                resp = await client.post(f"{TELEGRAM_API}/setWebhook", json=payload)
                logger.info(f"Automatically set Telegram webhook to {webhook_url}: {resp.text}")
        except Exception:
            logger.exception("Failed to auto-set Telegram webhook on startup")
    yield


app = FastAPI(lifespan=lifespan)


def format_for_telegram(text: str) -> str:
    code_blocks = []
    def save_code_block(match):
        code = html.escape(match.group(1).strip())
        code_blocks.append(f"<pre>{code}</pre>")
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"
    text = re.sub(r"```[a-zA-Z0-9_-]*\n?(.*?)```", save_code_block, text, flags=re.DOTALL)

    inline_codes = []
    def save_inline_code(match):
        code = html.escape(match.group(1))
        inline_codes.append(f"<code>{code}</code>")
        return f"__INLINE_CODE_{len(inline_codes)-1}__"
    text = re.sub(r"`([^`]+)`", save_inline_code, text)

    text = html.escape(text, quote=False)

    text = re.sub(r"^#{1,6}\s*(.+)$", r"<b>\1</b>", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"<a href=\"\2\">\1</a>", text)
    text = re.sub(r"^\*\s+", "• ", text, flags=re.MULTILINE)
    text = re.sub(r"^-\s+", "• ", text, flags=re.MULTILINE)

    for i, block in enumerate(code_blocks):
        text = text.replace(f"__CODE_BLOCK_{i}__", block)
    for i, code in enumerate(inline_codes):
        text = text.replace(f"__INLINE_CODE_{i}__", code)

    return text


async def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
    formatted_text = format_for_telegram(text)
    payload = {"chat_id": chat_id, "text": formatted_text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{TELEGRAM_API}/sendMessage", json=payload)
        if resp.status_code != 200:
            logger.warning(f"Telegram HTML parse failed ({resp.text}), falling back to plain text")
            plain = {"chat_id": chat_id, "text": text}
            if reply_markup:
                plain["reply_markup"] = reply_markup
            await client.post(f"{TELEGRAM_API}/sendMessage", json=plain)


async def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            f"{TELEGRAM_API}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
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

    # Inline-button taps (ask_user options). Resume the pending question with
    # the chosen option by feeding it through the normal turn path.
    callback_query = update.get("callback_query")
    if callback_query:
        chat_id = callback_query["message"]["chat"]["id"]
        data = callback_query.get("data", "")
        if data.startswith("askopt:"):
            try:
                idx = int(data.split(":")[1])
                options = _ask_options.get(chat_id)
                if options and 0 <= idx < len(options):
                    await answer_callback_query(callback_query["id"], "Got it!")
                    asyncio.create_task(_process_turn_and_reply(chat_id, options[idx]))
                else:
                    await answer_callback_query(
                        callback_query["id"],
                        "Option expired -- just type your answer instead.",
                    )
            except Exception:
                logger.exception("failed to handle ask option callback")
        else:
            await answer_callback_query(callback_query["id"])
        return {"ok": True}

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

    asyncio.create_task(_process_turn_and_reply(chat_id, user_text))
    return {"ok": True}


# In-memory map of pending ask_user options per chat (chat_id -> [option, ...]).
# The question itself is persisted in the LangGraph checkpointer, so a lost
# entry only degrades the inline buttons, never the question.
_ask_options: dict[int, list[str]] = {}


async def _process_turn_and_reply(chat_id: int, user_text: str) -> None:
    logger.info(f"Processing turn in background for chat_id={chat_id}: {user_text[:100]}")
    try:
        result = await asyncio.to_thread(run_turn, chat_id, user_text)
    except Exception:
        logger.exception("agent invocation failed")
        await send_message(chat_id, "Something went wrong on my end -- try again in a moment.")
        return

    rtype = result.get("type", "reply")
    text = result.get("text", "")

    try:
        if rtype == "ask":
            options = result.get("options") or []
            reply_markup = None
            if options:
                buttons = [
                    [{"text": opt, "callback_data": f"askopt:{i}"}]
                    for i, opt in enumerate(options[:8])
                ]
                reply_markup = {"inline_keyboard": buttons}
                _ask_options[chat_id] = options[:8]
            await send_message(chat_id, text, reply_markup)
        else:
            await send_message(chat_id, text)
        logger.info(f"Sent reply to chat_id={chat_id}, type={rtype}, length={len(text)}")
    except Exception:
        logger.exception("failed to send telegram reply")


@app.get("/webhook-info")
async def webhook_info():
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{TELEGRAM_API}/getWebhookInfo")
        return resp.json()
