import os
import logging

import httpx
from fastapi import FastAPI, Request, Header, HTTPException

from agent import run_turn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("deepagent-telegram")

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
# Telegram echoes this back in a header on every webhook call, so you can
# verify requests actually came from Telegram and not a random POST to your URL.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

app = FastAPI()


async def send_message(chat_id: int, text: str) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        await client.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )


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
    if not message or "text" not in message:
        return {"ok": True}  # ignore non-text updates (photos, stickers, etc.)

    chat_id = message["chat"]["id"]
    text = message["text"]

    try:
        reply = run_turn(chat_id, text)
    except Exception:
        logger.exception("agent invocation failed")
        reply = "Something went wrong on my end -- try again in a moment."

    await send_message(chat_id, reply)
    return {"ok": True}
