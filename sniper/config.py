"""Central config loaded from .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

FINVIZ_AUTH = os.getenv("FINVIZ_AUTH", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
PDUFA_API_KEY = os.getenv("PDUFA_API_KEY", "").strip()
EDGAR_USER_AGENT = os.getenv("EDGAR_USER_AGENT", "ApexMarketSniper contact@example.com").strip()

DB_PATH = ROOT / os.getenv("DB_PATH", "data/sniper.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

INTRADAY_POLL_MINUTES = int(os.getenv("INTRADAY_POLL_MINUTES", "2"))
CATALYST_WINDOW_DAYS = int(os.getenv("CATALYST_WINDOW_DAYS", "30"))

SCANS_DIR = ROOT / "scans"

# Timezones
TZ_UK = "Europe/London"
TZ_NY = "America/New_York"


def require(*names: str) -> None:
    missing = [n for n in names if not globals().get(n)]
    if missing:
        raise SystemExit(f"Missing required .env values: {', '.join(missing)}")
