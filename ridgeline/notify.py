"""Out-of-band alerts via the Telegram Bot API.

Uses the same telegram.token / telegram.chat_id that Freqtrade's built-in
Telegram integration reads from config.private.json. Without them, alerts
are printed to the console instead, so nothing is silently lost.
"""

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def telegram_credentials(config: dict) -> Optional[tuple[str, str]]:
    tg = config.get("telegram") or {}
    token, chat_id = str(tg.get("token") or "").strip(), str(tg.get("chat_id") or "").strip()
    return (token, chat_id) if token and chat_id else None


def send(text: str, config: dict) -> bool:
    """Send one alert. Returns True if Telegram accepted it."""
    creds = telegram_credentials(config)
    if not creds:
        print(f"[ALERT] {text}", flush=True)
        return False
    token, chat_id = creds
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=10)
        r.raise_for_status()
        return True
    except httpx.HTTPError as e:
        # never log the URL: it contains the bot token
        logger.error("Telegram alert failed (%s); alert was: %s", type(e).__name__, text)
        print(f"[ALERT - Telegram failed] {text}", flush=True)
        return False
