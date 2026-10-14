"""Telegram push (plain Bot API, no library)."""
from __future__ import annotations

import html
import logging
import time

import requests

from . import config
from .scoring import Alert

log = logging.getLogger(__name__)

EMOJI = {"LONG": "🟢", "SHORT": "🔴", "WATCH": "🟡"}
MODULE_LABEL = {
    "breakout": "ATH BREAKOUT", "undervalued": "VALUE + INSIDER",
    "exhaustion": "EXHAUSTION", "badnews": "BAD NEWS", "catalyst": "FDA CATALYST",
    "filing": "8-K FILING",
}


def send(text: str, disable_preview: bool = True, retries: int = 3) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        log.info("[telegram disabled] %s", text.replace("\n", " | ")[:200])
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json={
                "chat_id": config.TELEGRAM_CHAT_ID, "text": text[:4000],
                "parse_mode": "HTML", "disable_web_page_preview": disable_preview,
            }, timeout=20)
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)))
                continue
            log.warning("Telegram HTTP %s: %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            log.warning("Telegram error: %s", e)
        time.sleep(2 * attempt)
    return False


def fmt_alert(a: Alert) -> str:
    e = EMOJI.get(a.direction, "⚪")
    label = MODULE_LABEL.get(a.module, a.module.upper())
    price = f" @ ${a.price:,.2f}" if a.price else ""
    lines = [f"{e} <b>{label}</b> · <b>{html.escape(a.ticker)}</b>{price} · score {a.score}",
             html.escape(a.headline)]
    if a.reasons:
        lines.append("• " + "\n• ".join(html.escape(x) for x in a.reasons))
    if a.note:
        lines.append(f"<i>{html.escape(a.note)}</i> — X: {a.sentiment.lower()}")
    lines.append(f'<a href="https://finviz.com/quote.ashx?t={a.ticker}">Finviz</a>'
                 + "".join(f' · <a href="{html.escape(u)}">src</a>' for u in a.links[:2]))
    return "\n".join(lines)


def send_alert(a: Alert) -> bool:
    return send(fmt_alert(a))
