"""Write docs/data.json for the dashboard (served by GitHub Pages)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from . import config
from .store import Store

MODULES = ["breakout", "undervalued", "exhaustion", "badnews", "catalyst", "filing"]


def build(store: Store, catalysts: list, market_open: bool) -> dict:
    recent = store.recent(hours=36)
    by_mod: dict[str, list] = {m: [] for m in MODULES}
    seen: set[str] = set()
    for a in recent:                       # already sorted by score desc
        k = f"{a['ticker']}:{a['module']}"
        if k in seen:
            continue
        seen.add(k)
        by_mod.setdefault(a["module"], []).append({
            "ticker": a["ticker"], "price": a["price"], "score": a["score"],
            "direction": a["direction"], "headline": a["headline"],
            "reasons": a["reasons"], "links": a["links"], "note": a["note"],
            "sentiment": a["sentiment"], "ts": a["ts"],
        })
    cats = sorted([c for c in catalysts if c.date and 0 <= c.days_to <= 14],
                  key=lambda c: (c.date, c.ticker))
    return {
        "updated": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "market_open": market_open,
        "counts": {m: len(v) for m, v in by_mod.items()},
        "modules": by_mod,
        "catalysts": [{
            "ticker": c.ticker, "company": c.company, "date": c.date.isoformat(),
            "days": c.days_to, "type": c.event_type, "name": c.name,
            "cap": c.market_cap_usd, "runway": c.cash_runway_months,
            "median_move": c.cohort_move_median_pct, "url": c.url,
        } for c in cats],
        "scorecard": [{"module": m, "n": n, "avg": round(a, 2), "hit": round(h, 1)}
                      for m, n, a, h in store.module_stats()],
    }


def write(store: Store, catalysts: list, market_open: bool, path=None) -> None:
    path = path or (config.ROOT / "docs" / "data.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(build(store, catalysts, market_open), indent=0), encoding="utf-8")
