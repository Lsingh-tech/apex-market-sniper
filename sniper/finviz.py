"""Finviz Elite export client.

Endpoint: https://elite.finviz.com/export?v=111&f=<filters>&s=<signal>&c=<cols>&auth=<key>
Returns CSV. We request a fixed set of columns by ID and parse rows by header
name, so a column-ID mismatch degrades gracefully instead of breaking.
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

EXPORT_URL = "https://elite.finviz.com/export"

# Finviz custom-view column IDs (community-verified, stable for years).
COLUMNS: dict[str, int] = {
    "Ticker": 1, "Company": 2, "Sector": 3, "Industry": 4, "Country": 5,
    "Market Cap": 6, "P/E": 7, "Forward P/E": 8, "PEG": 9, "P/S": 10, "P/B": 11,
    "P/Free Cash Flow": 13, "EPS (ttm)": 16, "EPS growth this year": 17,
    "EPS growth next year": 18, "EPS growth quarter over quarter": 22,
    "Sales growth quarter over quarter": 23, "Shares Float": 25,
    "Insider Ownership": 26, "Insider Transactions": 27,
    "Institutional Ownership": 28, "Institutional Transactions": 29,
    "Float Short": 30, "Short Ratio": 31, "Return on Equity": 33,
    "Total Debt/Equity": 38, "Gross Margin": 39, "Operating Margin": 40,
    "Performance (Week)": 42, "Performance (Month)": 43,
    "Performance (Quarter)": 44, "Performance (Year)": 46, "Beta": 48,
    "Average True Range": 49, "Volatility (Week)": 50, "Volatility (Month)": 51,
    "20-Day Simple Moving Average": 52, "50-Day Simple Moving Average": 53,
    "200-Day Simple Moving Average": 54, "50-Day High": 55, "50-Day Low": 56,
    "52-Week High": 57, "52-Week Low": 58, "Relative Strength Index (14)": 59,
    "Change from Open": 60, "Gap": 61, "Analyst Recom": 62,
    "Average Volume": 63, "Relative Volume": 64, "Price": 65, "Change": 66,
    "Volume": 67, "Earnings Date": 68, "Target Price": 69,
}
DEFAULT_COLS = ",".join(str(v) for v in COLUMNS.values())

_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def parse_num(v: str | None) -> float | None:
    """'12.5%' -> 12.5, '1.2B' -> 1.2e9, '-' -> None."""
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if s in ("", "-", "N/A"):
        return None
    pct = s.endswith("%")
    s = s.rstrip("%")
    mult = 1.0
    if s and s[-1].upper() in _SUFFIX:
        mult = _SUFFIX[s[-1].upper()]
        s = s[:-1]
    try:
        return float(s) * mult
    except ValueError:
        return None


_EARN_RE = re.compile(r"^([A-Z][a-z]{2}) (\d{1,2})(?:/([ab]))?$")


def parse_earnings(v: str | None, today: date | None = None) -> tuple[date | None, str | None]:
    """'Nov 05/a' -> (date(YYYY,11,5), 'AMC'). Year inferred as nearest."""
    if not v:
        return None, None
    m = _EARN_RE.match(v.strip())
    if not m:
        return None, None
    today = today or date.today()
    mon, day, when = m.groups()
    try:
        d = datetime.strptime(f"{mon} {day} {today.year}", "%b %d %Y").date()
    except ValueError:
        return None, None
    # Finviz shows dates within ~a year either side; pick nearest year.
    if (d - today).days > 200:
        d = d.replace(year=today.year - 1)
    elif (today - d).days > 200:
        d = d.replace(year=today.year + 1)
    return d, {"a": "AMC", "b": "BMO"}.get(when or "", None)


@dataclass
class Row:
    ticker: str
    raw: dict[str, str]
    num: dict[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.num = {k: parse_num(v) for k, v in self.raw.items()}

    def f(self, col: str) -> float | None:
        return self.num.get(col)

    def s(self, col: str) -> str:
        return self.raw.get(col, "")

    @property
    def earnings(self) -> tuple[date | None, str | None]:
        return parse_earnings(self.raw.get("Earnings Date"))


class FinvizClient:
    def __init__(self, auth: str | None = None, session: requests.Session | None = None):
        self.auth = auth or config.FINVIZ_AUTH
        if not self.auth:
            raise ValueError("FINVIZ_AUTH not set")
        self.sess = session or requests.Session()
        self.sess.headers["User-Agent"] = "Mozilla/5.0 (ApexMarketSniper)"
        self._last_call = 0.0

    def _throttle(self, min_gap: float = 1.0) -> None:
        gap = time.time() - self._last_call
        if gap < min_gap:
            time.sleep(min_gap - gap)
        self._last_call = time.time()

    def export(self, filters: str = "", signal: str = "", order: str = "",
               cols: str = DEFAULT_COLS, tickers: list[str] | None = None,
               retries: int = 3) -> list[Row]:
        params: dict[str, Any] = {"v": "111", "c": cols, "auth": self.auth}
        if filters:
            params["f"] = filters
        if signal:
            params["s"] = signal
        if order:
            params["o"] = order
        if tickers:
            params["t"] = ",".join(t.upper() for t in tickers)

        for attempt in range(1, retries + 1):
            self._throttle()
            try:
                r = self.sess.get(EXPORT_URL, params=params, timeout=30)
                if r.status_code == 200 and r.text.lstrip().startswith("No."):
                    return self.parse_csv(r.text)
                if r.status_code in (401, 403):
                    raise PermissionError("Finviz rejected auth key (401/403)")
                log.warning("Finviz export HTTP %s (attempt %d): %s",
                            r.status_code, attempt, r.text[:120].replace("\n", " "))
            except requests.RequestException as e:
                log.warning("Finviz export error (attempt %d): %s", attempt, e)
            time.sleep(2 * attempt)
        return []

    @staticmethod
    def parse_csv(text: str) -> list[Row]:
        reader = csv.DictReader(io.StringIO(text))
        out: list[Row] = []
        for rec in reader:
            t = (rec.get("Ticker") or "").strip()
            if not t:
                continue
            rec.pop("No.", None)
            out.append(Row(ticker=t, raw=rec))
        return out

    def quotes(self, tickers: list[str]) -> dict[str, Row]:
        """Snapshot for a specific list of tickers (chunked to 100)."""
        res: dict[str, Row] = {}
        for i in range(0, len(tickers), 100):
            for row in self.export(tickers=tickers[i:i + 100]):
                res[row.ticker] = row
        return res
