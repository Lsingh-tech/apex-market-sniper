"""SEC EDGAR 8-K watcher (free). Polls the 'current filings' Atom feed.

SEC asks for a descriptive User-Agent with contact info and <=10 req/s.
We poll every few minutes and only care about 8-K / 8-K/A on tickers we
track (watchlist + anything that has alerted recently).

Item codes that usually move price:
  1.01 material agreement  1.02 termination  2.02 results of operations
  2.04 triggering events (debt)  2.05 exit/disposal costs  2.06 impairments
  4.01 auditor change  4.02 non-reliance on prior financials  5.02 officer change
  7.01 Reg FD  8.01 other events
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import requests

from . import config

log = logging.getLogger(__name__)
FEED = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent"
        "&type=8-K&company=&dateb=&owner=include&count=100&output=atom")

NEGATIVE_ITEMS = {"1.02", "2.04", "2.05", "2.06", "4.01", "4.02"}
EARNINGS_ITEMS = {"2.02"}
_ITEM_RE = re.compile(r"Item\s+(\d\.\d{2})")
_CIK_RE = re.compile(r"\((\d{10})\)")


@dataclass
class Filing:
    id: str
    title: str
    link: str
    updated: str
    items: set[str]
    company: str
    cik: str

    @property
    def is_negative(self) -> bool:
        return bool(self.items & NEGATIVE_ITEMS)

    @property
    def is_earnings(self) -> bool:
        return bool(self.items & EARNINGS_ITEMS)


def fetch_recent(session: requests.Session | None = None) -> list[Filing]:
    import feedparser  # lazy: keeps offline tests importable
    sess = session or requests.Session()
    try:
        r = sess.get(FEED, headers={"User-Agent": config.EDGAR_USER_AGENT,
                                    "Accept-Encoding": "gzip, deflate"}, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        log.warning("EDGAR fetch failed: %s", e)
        return []
    feed = feedparser.parse(r.text)
    out = []
    for e in feed.entries:
        title = e.get("title", "")
        summary = e.get("summary", "")
        items = set(_ITEM_RE.findall(summary)) | set(_ITEM_RE.findall(title))
        m = _CIK_RE.search(title)
        company = title.split(" - ")[1].split(" (")[0].strip() if " - " in title else title
        out.append(Filing(
            id=e.get("id", e.get("link", "")), title=title, link=e.get("link", ""),
            updated=e.get("updated", ""), items=items, company=company,
            cik=m.group(1) if m else "",
        ))
    return out


class CikMap:
    """Map CIK -> ticker using SEC's company_tickers.json (cached daily)."""

    URL = "https://www.sec.gov/files/company_tickers.json"

    def __init__(self):
        self._map: dict[str, str] = {}

    def refresh(self, session: requests.Session | None = None) -> None:
        sess = session or requests.Session()
        try:
            r = sess.get(self.URL, headers={"User-Agent": config.EDGAR_USER_AGENT}, timeout=30)
            r.raise_for_status()
            data = r.json()
            self._map = {str(v["cik_str"]).zfill(10): v["ticker"].upper() for v in data.values()}
            log.info("CIK map loaded: %d tickers", len(self._map))
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("CIK map refresh failed: %s", e)

    def ticker(self, cik: str) -> str | None:
        return self._map.get(cik)
