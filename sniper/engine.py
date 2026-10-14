"""Orchestration: run scans, catalysts, filings; dedup; push; digests."""
from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import catalysts, config, edgar, grok, telegram
from .finviz import FinvizClient
from .scans import Scan, load_scans
from .scoring import SCORERS, Alert
from .store import Store

log = logging.getLogger(__name__)
_catalyst_cache: list[catalysts.Catalyst] = []
scan_stats: dict[str, dict] = {}   # scan name -> {rows, alerts, at} for the dashboard/diagnostics
_cik = edgar.CikMap()


class Engine:
    def __init__(self, fv: FinvizClient | None = None, store: Store | None = None,
                 scans: list[Scan] | None = None, push: bool = True):
        self.fv = fv or FinvizClient()
        self.store = store or Store()
        self.scans = scans or load_scans()
        self.push = push
        self.watchlist: set[str] = self._load_watchlist()

    @staticmethod
    def _load_watchlist() -> set[str]:
        p = config.ROOT / "scans" / "watchlist.txt"
        if not p.exists():
            return set()
        return {ln.strip().upper() for ln in p.read_text().splitlines()
                if ln.strip() and not ln.startswith("#")}

    # ------------------------------------------------------------ helpers
    def _emit(self, a: Alert, force: bool = False) -> bool:
        if not force and not self.store.should_push(a):
            return False
        g = grok.enrich(a.ticker, "; ".join(a.reasons[:5]), a.module)
        if g:
            a.note, a.sentiment = g
            if a.module == "exhaustion" and a.sentiment == "BULLISH":
                a.score = min(100, a.score + 5)   # retail euphoria confirms the top
                a.reasons.append("X sentiment euphoric")
        self.store.log(a)
        if self.push:
            telegram.send_alert(a)
        else:
            log.info("ALERT %s", telegram.fmt_alert(a).replace("\n", " | "))
        return True

    # -------------------------------------------------------------- scans
    def run_scans(self, when: str) -> int:
        """Run all scans for `when` ('intraday' | 'daily'); return alerts pushed."""
        pushed = 0
        cats_by_ticker = {c.ticker: c for c in _catalyst_cache if c.date}
        for scan in [s for s in self.scans if s.when == when]:
            scorer = SCORERS.get(scan.module)
            if not scorer:
                log.warning("No scorer for module %s", scan.module)
                continue
            rows = self.fv.export(filters=scan.filters, signal=scan.signal, order=scan.order)
            log.info("%s: %d rows", scan.name, len(rows))
            stat = scan_stats[scan.name] = {"rows": len(rows), "alerts": 0,
                                            "at": datetime.utcnow().isoformat(timespec="minutes")}
            for r in rows[:200]:
                a = scorer(r)
                if not a:
                    continue
                a.reasons.insert(0, scan.name)
                # catalyst boost: upcoming FDA date makes any setup more interesting
                c = cats_by_ticker.get(r.ticker)
                if c and c.days_to is not None and 0 <= c.days_to <= config.CATALYST_WINDOW_DAYS:
                    a.score = min(100, a.score + 10)
                    a.reasons.append(f"FDA {c.event_type} in {c.days_to}d ({c.name[:40]})")
                    a.links.append(c.url)
                if r.ticker in self.watchlist:
                    a.score = min(100, a.score + 5)
                    a.reasons.append("on watchlist")
                if self._emit(a):
                    pushed += 1
                    stat["alerts"] += 1
        return pushed

    # ---------------------------------------------------------- catalysts
    def refresh_catalysts(self) -> int:
        global _catalyst_cache
        _catalyst_cache = catalysts.fetch_events()
        log.info("Catalysts loaded: %d", len(_catalyst_cache))
        pushed = 0
        due = catalysts.due_alerts(_catalyst_cache)
        quotes = self.fv.quotes([c.ticker for c, _ in due]) if due else {}
        for c, days in due:
            key = f"{c.ticker}:catalyst:{c.id}:T-{days}"
            if not self.store.seen_once(key):
                continue
            q = quotes.get(c.ticker)
            reasons = [f"{c.event_type}: {c.name}", f"{c.company}"]
            if c.cohort_move_median_pct is not None:
                reasons.append(f"similar events: median move {c.cohort_move_median_pct:+.0f}%")
            if c.cash_runway_months is not None:
                reasons.append(f"cash runway ~{c.cash_runway_months:.0f} months")
            if q:
                reasons.append(f"week {q.s('Performance (Week)')}, short float {q.s('Float Short')}, "
                               f"RSI {q.s('Relative Strength Index (14)')}")
            score = {30: 60, 7: 75, 1: 85, 0: 90}[days]
            label = "DECISION DAY" if days == 0 else f"T-{days}"
            a = Alert(c.ticker, "catalyst", "WATCH", score,
                      f"{c.ticker} FDA catalyst {label} · {c.date}", reasons,
                      q.f("Price") if q else None, [c.url] + ([c.source_url] if c.source_url else []),
                      key=key)
            if self._emit(a, force=True):
                pushed += 1
        return pushed

    # ------------------------------------------------------------ filings
    def check_filings(self) -> int:
        if not _cik._map:
            _cik.refresh()
        tracked = self.watchlist | self.store.recent_tickers(days=10) | \
            {c.ticker for c in _catalyst_cache}
        pushed = 0
        for f in edgar.fetch_recent():
            t = _cik.ticker(f.cik)
            if not t or t not in tracked:
                continue
            if not self.store.seen_once(f"filing:{f.id}"):
                continue
            if f.is_negative:
                direction, score, what = "SHORT", 80, "negative 8-K"
            elif f.is_earnings:
                direction, score, what = "WATCH", 70, "earnings 8-K"
            else:
                direction, score, what = "WATCH", 60, "8-K"
            a = Alert(t, "filing", direction, score, f"{t} {what} filed",
                      [f"Items {', '.join(sorted(f.items)) or 'n/a'}", f.company],
                      None, [f.link], key=f"filing:{f.id}")
            if self._emit(a, force=True):
                pushed += 1
        return pushed

    # ------------------------------------------------------------ digests
    def digest(self, kind: str) -> None:
        now = datetime.now(ZoneInfo(config.TZ_UK))
        title = "PRE-MARKET" if kind == "am" else "POST-CLOSE"
        hours = 16 if kind == "am" else 8
        recent = self.store.recent(hours=hours)
        lines = [f"<b>APEX MARKET SNIPER · {title} · {now:%a %d %b %H:%M} UK</b>"]

        # catalysts next 7 days
        soon = sorted([c for c in _catalyst_cache if c.date and 0 <= c.days_to <= 7],
                      key=lambda c: c.date)
        if soon:
            lines.append("\n<b>FDA catalysts next 7 days</b>")
            for c in soon[:12]:
                lines.append(f"• {c.ticker} {c.date} · {c.event_type} · {c.name[:45]}")

        for mod, label in (("breakout", "Breakout setups"), ("undervalued", "Value + insider"),
                           ("exhaustion", "Exhaustion / short"), ("badnews", "Bad news"),
                           ("filing", "8-K filings")):
            rows = [r for r in recent if r["module"] == mod][:5]
            if rows:
                lines.append(f"\n<b>{label}</b>")
                for r in rows:
                    p = f" ${r['price']:,.2f}" if r["price"] else ""
                    lines.append(f"• {r['ticker']}{p} · {r['score']}")

        stats = self.store.module_stats()
        if stats and kind == "pm":
            lines.append("\n<b>Module scorecard (5-day, direction-adjusted)</b>")
            for m, n, avg, hit in stats:
                lines.append(f"• {m}: n={n} avg {avg:+.1f}% hit {hit:.0f}%")

        if len(lines) == 1:
            lines.append("Nothing of note.")
        text = "\n".join(lines)
        telegram.send(text) if self.push else log.info(text)

    # ------------------------------------------------------------- export
    def export_json(self) -> None:
        from . import export
        export.write(self.store, _catalyst_cache, us_market_open(), stats=scan_stats)

    # ----------------------------------------------------------- outcomes
    def track_outcomes(self) -> int:
        pend = self.store.pending_outcomes(min_age_days=5)
        if not pend:
            return 0
        quotes = self.fv.quotes(sorted({t for _, t, _, _ in pend}))
        n = 0
        for alert_id, t, p0, _ in pend:
            q = quotes.get(t)
            p1 = q.f("Price") if q else None
            if p0 and p1:
                self.store.record_outcome(alert_id, 100 * (p1 - p0) / p0)
                n += 1
        return n


def us_market_open(now: datetime | None = None) -> bool:
    ny = (now or datetime.now(ZoneInfo(config.TZ_NY))).astimezone(ZoneInfo(config.TZ_NY))
    if ny.weekday() >= 5:
        return False
    t = ny.hour * 60 + ny.minute
    return 9 * 60 + 30 <= t <= 16 * 60
