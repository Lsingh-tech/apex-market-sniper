"""FDA / biotech catalyst radar via pdufa.bio (free, keyless).

Docs: https://www.pdufa.bio/developers
Attribution required on free tiers: alerts carry a pdufa.bio link.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import requests

from . import config

log = logging.getLogger(__name__)
BASE = "https://www.pdufa.bio/api/v1"

ALERT_DAYS = (30, 7, 1, 0)  # T-30, T-7, T-1, decision day


@dataclass
class Catalyst:
    id: str
    ticker: str
    company: str
    event_type: str
    name: str
    date: date | None
    date_month: str | None
    date_precision: str
    market_cap_usd: float | None
    cash_runway_months: float | None
    cohort_move_median_pct: float | None
    url: str
    source_url: str | None
    status: str

    @property
    def days_to(self) -> int | None:
        return (self.date - date.today()).days if self.date else None


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def fetch_events(days_ahead: int | None = None, session: requests.Session | None = None) -> list[Catalyst]:
    days_ahead = days_ahead or config.CATALYST_WINDOW_DAYS
    sess = session or requests.Session()
    headers = {"User-Agent": "ApexMarketSniper"}
    if config.PDUFA_API_KEY:
        headers["x-api-key"] = config.PDUFA_API_KEY
    today = date.today()
    params = {
        "from": today.isoformat(),
        "to": (today + timedelta(days=days_ahead)).isoformat(),
        "limit": 1000,
    }
    out: list[Catalyst] = []
    offset = 0
    while True:
        params["offset"] = offset
        try:
            r = sess.get(f"{BASE}/events", params=params, headers=headers, timeout=30)
            r.raise_for_status()
            payload = r.json()
        except (requests.RequestException, ValueError) as e:
            log.warning("pdufa.bio fetch failed: %s", e)
            break
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        for e in rows:
            d = e.get("date")
            out.append(Catalyst(
                id=str(e.get("id")), ticker=(e.get("ticker") or "").upper(),
                company=e.get("company") or "", event_type=e.get("type") or "",
                name=e.get("name") or "", date=datetime.fromisoformat(d).date() if d else None,
                date_month=e.get("date_month"), date_precision=e.get("date_precision") or "",
                market_cap_usd=_f(e.get("market_cap_usd")),
                cash_runway_months=_f(e.get("cash_runway_months")),
                cohort_move_median_pct=_f(e.get("cohort_move_median_pct")),
                url=e.get("url") or "https://www.pdufa.bio", source_url=e.get("source_url"),
                status=e.get("status") or "",
            ))
        if len(rows) < 1000:
            break
        offset += 1000
    # Keep tradeable, company-level events only: conferences come back with
    # pseudo-tickers (IDWEEK, ESMO...) and aren't catalysts for a stock.
    return [c for c in out
            if c.ticker and c.event_type.lower() != "conference"
            and c.ticker.isalpha() and len(c.ticker) <= 5]


def due_alerts(cats: list[Catalyst]) -> list[tuple[Catalyst, int]]:
    """Catalysts whose days_to hits an alert threshold today."""
    due = []
    for c in cats:
        if c.date_precision == "day" and c.days_to in ALERT_DAYS:
            due.append((c, c.days_to))
    return due
