"""SQLite store: alert log + dedup + outcome tracking."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from . import config
from .scoring import Alert

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    key TEXT NOT NULL,
    ticker TEXT NOT NULL,
    module TEXT NOT NULL,
    direction TEXT,
    score INTEGER,
    price REAL,
    headline TEXT,
    reasons TEXT,
    links TEXT,
    note TEXT DEFAULT '',
    sentiment TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_alerts_key_ts ON alerts(key, ts);
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (
    alert_id INTEGER PRIMARY KEY,
    p1d REAL, p3d REAL, p5d REAL, checked_ts TEXT
);
"""


class Store:
    def __init__(self, path=None):
        path = path or config.DB_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)

    # -- dedup ----------------------------------------------------------
    def should_push(self, a: Alert, cooldown_hours: float = 24, rescore_delta: int = 15) -> bool:
        """Push if never alerted in cooldown window, or score jumped by >= delta."""
        row = self.db.execute(
            "SELECT ts, score FROM alerts WHERE key=? ORDER BY ts DESC LIMIT 1", (a.key,)
        ).fetchone()
        if not row:
            return True
        ts, score = datetime.fromisoformat(row[0]), row[1] or 0
        if datetime.utcnow() - ts > timedelta(hours=cooldown_hours):
            return True
        return a.score - score >= rescore_delta

    def seen_once(self, key: str) -> bool:
        """True the first time a key is seen (for filings / catalyst T-N)."""
        cur = self.db.execute("INSERT OR IGNORE INTO seen(key, ts) VALUES (?, ?)",
                              (key, datetime.utcnow().isoformat()))
        self.db.commit()
        return cur.rowcount == 1

    # -- log ------------------------------------------------------------
    def log(self, a: Alert) -> int:
        cur = self.db.execute(
            "INSERT INTO alerts(ts,key,ticker,module,direction,score,price,headline,reasons,links,"
            "note,sentiment) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.utcnow().isoformat(), a.key, a.ticker, a.module, a.direction, a.score,
             a.price, a.headline, json.dumps(a.reasons), json.dumps(a.links), a.note, a.sentiment),
        )
        self.db.commit()
        return cur.lastrowid

    def recent(self, hours: float = 24, module: str | None = None) -> list[dict]:
        since = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        q = ("SELECT id,ts,ticker,module,direction,score,price,headline,reasons,links,note,sentiment"
             " FROM alerts WHERE ts>=?")
        args: list = [since]
        if module:
            q += " AND module=?"; args.append(module)
        q += " ORDER BY score DESC, ts DESC"
        cols = ["id", "ts", "ticker", "module", "direction", "score", "price", "headline",
                "reasons", "links", "note", "sentiment"]
        out = []
        for r in self.db.execute(q, args):
            d = dict(zip(cols, r))
            d["reasons"] = json.loads(d["reasons"] or "[]")
            d["links"] = json.loads(d["links"] or "[]")
            out.append(d)
        return out

    def recent_tickers(self, days: int = 10) -> set[str]:
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()
        return {r[0] for r in self.db.execute("SELECT DISTINCT ticker FROM alerts WHERE ts>=?", (since,))}

    # -- outcomes -------------------------------------------------------
    def pending_outcomes(self, min_age_days: int = 5) -> list[tuple[int, str, float, str]]:
        cutoff = (datetime.utcnow() - timedelta(days=min_age_days)).isoformat()
        return self.db.execute(
            "SELECT a.id, a.ticker, a.price, a.direction FROM alerts a "
            "LEFT JOIN outcomes o ON o.alert_id=a.id "
            "WHERE o.alert_id IS NULL AND a.ts<=? AND a.price IS NOT NULL", (cutoff,)
        ).fetchall()

    def record_outcome(self, alert_id: int, p5d: float) -> None:
        self.db.execute("INSERT OR REPLACE INTO outcomes(alert_id,p5d,checked_ts) VALUES (?,?,?)",
                        (alert_id, p5d, datetime.utcnow().isoformat()))
        self.db.commit()

    def module_stats(self) -> list[tuple[str, int, float, float]]:
        """(module, n, avg 5d move %, hit rate %) — direction-adjusted."""
        rows = self.db.execute(
            "SELECT a.module, a.direction, o.p5d FROM alerts a JOIN outcomes o ON o.alert_id=a.id"
        ).fetchall()
        agg: dict[str, list[float]] = {}
        for m, d, p in rows:
            if p is None:
                continue
            agg.setdefault(m, []).append(p if d != "SHORT" else -p)
        out = []
        for m, xs in agg.items():
            n = len(xs)
            out.append((m, n, sum(xs) / n, 100 * sum(1 for x in xs if x > 0) / n))
        return sorted(out, key=lambda t: -t[2])
