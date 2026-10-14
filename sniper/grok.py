"""Grok (xAI) enrichment: 'why is it moving' + X sentiment for an alert.

Uses the OpenAI-compatible chat endpoint with xAI live search over X and the
web. If xAI changes the request shape, `_body()` is the only thing to edit.
A daily call cap protects the bill; when the cap is hit or the key is missing,
enrichment silently returns None and alerts go out un-annotated.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, timedelta

import requests

from . import config

log = logging.getLogger(__name__)

XAI_API_KEY = os.getenv("XAI_API_KEY", "").strip()
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4").strip()
GROK_ENABLED = os.getenv("GROK_ENABLED", "true").lower() in ("1", "true", "yes")
GROK_DAILY_CAP = int(os.getenv("GROK_DAILY_CAP", "60"))
GROK_MODULES = set(filter(None, os.getenv(
    "GROK_MODULES", "breakout,exhaustion,badnews,catalyst,filing").split(",")))

URL = "https://api.x.ai/v1/chat/completions"

SYSTEM = (
    "You are a terse equities desk analyst. Using live X posts and web news from the last 48 hours, "
    "explain in at most 2 short sentences what is driving the stock's move today, then give crowd "
    "sentiment on X as one word: BULLISH, BEARISH, MIXED or QUIET. Output strictly as JSON: "
    '{"why": "...", "sentiment": "..."}. No preamble. If you find nothing relevant, say so in why.'
)


class _Budget:
    """Per-day call counter persisted to a small JSON file."""

    def __init__(self, path):
        self.path = path
        self.day, self.n = str(date.today()), 0
        try:
            d = json.loads(path.read_text())
            if d.get("day") == self.day:
                self.n = int(d.get("n", 0))
        except (OSError, ValueError):
            pass

    def take(self) -> bool:
        if str(date.today()) != self.day:
            self.day, self.n = str(date.today()), 0
        if self.n >= GROK_DAILY_CAP:
            return False
        self.n += 1
        try:
            self.path.write_text(json.dumps({"day": self.day, "n": self.n}))
        except OSError:
            pass
        return True


_budget = _Budget(config.ROOT / "data" / "grok_budget.json")


def _body(ticker: str, context: str) -> dict:
    since = (datetime.utcnow() - timedelta(days=2)).strftime("%Y-%m-%d")
    return {
        "model": XAI_MODEL,
        "temperature": 0.2,
        "max_tokens": 200,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Ticker: ${ticker}. Scanner context: {context}"},
        ],
        "search_parameters": {
            "mode": "on",
            "return_citations": False,
            "from_date": since,
            "max_search_results": 15,
            "sources": [{"type": "x"}, {"type": "web"}],
        },
    }


def enrich(ticker: str, context: str, module: str) -> tuple[str, str] | None:
    """Return (why, sentiment) or None."""
    if not (GROK_ENABLED and XAI_API_KEY and module in GROK_MODULES):
        return None
    if not _budget.take():
        log.info("Grok daily cap reached; skipping %s", ticker)
        return None
    try:
        r = requests.post(URL, json=_body(ticker, context), timeout=45,
                          headers={"Authorization": f"Bearer {XAI_API_KEY}"})
        if r.status_code != 200:
            log.warning("Grok HTTP %s: %s", r.status_code, r.text[:200])
            return None
        text = r.json()["choices"][0]["message"]["content"].strip()
        m = re.search(r"\{.*\}", text, re.S)
        d = json.loads(m.group(0) if m else text)
        why = str(d.get("why", "")).strip()[:280]
        sent = str(d.get("sentiment", "MIXED")).strip().upper()
        if sent not in ("BULLISH", "BEARISH", "MIXED", "QUIET"):
            sent = "MIXED"
        return (why, sent) if why else None
    except (requests.RequestException, ValueError, KeyError, IndexError) as e:
        log.warning("Grok enrich failed for %s: %s", ticker, e)
        return None
