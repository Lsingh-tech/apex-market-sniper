"""Turn Finviz rows into scored, explainable alerts.

Each scorer returns (score 0-100, reasons[]) or None to skip the row.
Scores are deliberately simple and transparent; the point is ranking and
"why now", not prediction. Tune thresholds here, not in the scans.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .finviz import Row

MIN_SCORE = 55  # below this we don't push


@dataclass
class Alert:
    ticker: str
    module: str          # breakout | undervalued | exhaustion | badnews | catalyst | filing
    direction: str       # LONG | SHORT | WATCH
    score: int
    headline: str
    reasons: list[str] = field(default_factory=list)
    price: float | None = None
    links: list[str] = field(default_factory=list)
    key: str = ""        # dedup key; defaults to ticker:module
    note: str = ""       # Grok "why it's moving"
    sentiment: str = ""  # BULLISH | BEARISH | MIXED | QUIET

    def __post_init__(self):
        if not self.key:
            self.key = f"{self.ticker}:{self.module}"


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v:+.1f}%"


# ---------------------------------------------------------------- breakout
def score_breakout(r: Row) -> Alert | None:
    hi52 = r.f("52-Week High")      # % below high, negative number
    relvol = r.f("Relative Volume") or 0
    volw = r.f("Volatility (Week)") or 99
    sma50 = r.f("50-Day Simple Moving Average") or 0
    sma200 = r.f("200-Day Simple Moving Average") or 0
    perfq = r.f("Performance (Quarter)") or 0
    chg = r.f("Change") or 0
    rsi = r.f("Relative Strength Index (14)") or 50

    score, why = 0, []
    if hi52 is not None:
        dist = abs(hi52)
        if dist <= 1: score += 30; why.append(f"{dist:.1f}% from 52W high")
        elif dist <= 3: score += 22; why.append(f"{dist:.1f}% from 52W high")
        elif dist <= 5: score += 12; why.append(f"{dist:.1f}% from 52W high")
    if relvol >= 3: score += 25; why.append(f"rel vol {relvol:.1f}x")
    elif relvol >= 2: score += 18; why.append(f"rel vol {relvol:.1f}x")
    elif relvol >= 1.5: score += 10; why.append(f"rel vol {relvol:.1f}x")
    if volw <= 3: score += 15; why.append(f"tight base (vol {volw:.1f}%/wk)")
    elif volw <= 5: score += 8
    if sma50 > 0 and sma200 > 0: score += 10; why.append("above SMA50 & SMA200")
    if 0 < perfq <= 40: score += 8; why.append(f"quarter {_pct(perfq)}")
    if chg > 0: score += 5
    if rsi > 85: score -= 15; why.append(f"RSI {rsi:.0f} (stretched)")
    if score < MIN_SCORE:
        return None
    return Alert(r.ticker, "breakout", "LONG", min(score, 100),
                 f"{r.ticker} pressing 52W high", why, r.f("Price"))


# ------------------------------------------------------------- undervalued
def score_undervalued(r: Row) -> Alert | None:
    pfcf = r.f("P/Free Cash Flow")
    de = r.f("Total Debt/Equity")
    insider = r.f("Insider Transactions") or 0
    epsq = r.f("EPS growth quarter over quarter") or 0
    salesq = r.f("Sales growth quarter over quarter") or 0
    roe = r.f("Return on Equity") or 0
    hi52 = r.f("52-Week High")
    short = r.f("Float Short") or 0
    recom = r.f("Analyst Recom")

    score, why = 0, []
    if pfcf is not None and 0 < pfcf <= 10: score += 25; why.append(f"P/FCF {pfcf:.1f}")
    elif pfcf is not None and 0 < pfcf <= 15: score += 15; why.append(f"P/FCF {pfcf:.1f}")
    if de is not None and de <= 0.3: score += 10; why.append(f"D/E {de:.2f}")
    if insider >= 5: score += 25; why.append(f"insider buying {_pct(insider)}")
    elif insider > 0: score += 12; why.append(f"insider buying {_pct(insider)}")
    if epsq > 15: score += 10; why.append(f"EPS q/q {_pct(epsq)}")
    if salesq > 10: score += 8; why.append(f"sales q/q {_pct(salesq)}")
    if roe > 15: score += 7
    if hi52 is not None and abs(hi52) >= 25: score += 10; why.append(f"{abs(hi52):.0f}% off 52W high")
    if short >= 10: score += 5; why.append(f"short float {short:.0f}%")
    if recom is not None and recom <= 2.0: score += 5
    if score < MIN_SCORE:
        return None
    return Alert(r.ticker, "undervalued", "LONG", min(score, 100),
                 f"{r.ticker} undervalued + insider interest", why, r.f("Price"))


# -------------------------------------------------------------- exhaustion
def score_exhaustion(r: Row) -> Alert | None:
    rsi = r.f("Relative Strength Index (14)") or 50
    sma20 = r.f("20-Day Simple Moving Average") or 0
    perfw = r.f("Performance (Week)") or 0
    perfm = r.f("Performance (Month)") or 0
    short = r.f("Float Short") or 0
    relvol = r.f("Relative Volume") or 1
    chg = r.f("Change") or 0
    cfo = r.f("Change from Open") or 0

    score, why = 0, []
    if rsi >= 85: score += 25; why.append(f"RSI {rsi:.0f}")
    elif rsi >= 75: score += 15; why.append(f"RSI {rsi:.0f}")
    if sma20 >= 40: score += 25; why.append(f"{sma20:.0f}% above SMA20")
    elif sma20 >= 20: score += 15; why.append(f"{sma20:.0f}% above SMA20")
    if perfw >= 50: score += 15; why.append(f"week {_pct(perfw)}")
    elif perfw >= 30: score += 10; why.append(f"week {_pct(perfw)}")
    if perfm >= 100: score += 10
    if short >= 20: score += 10; why.append(f"short float {short:.0f}%")
    # fading intraday: opened up, giving it back
    if chg > 0 and cfo < -2: score += 10; why.append("reversing from open")
    if relvol < 0.8: score += 5; why.append("volume drying up")
    if score < MIN_SCORE:
        return None
    return Alert(r.ticker, "exhaustion", "SHORT", min(score, 100),
                 f"{r.ticker} parabolic / exhaustion", why, r.f("Price"))


# ----------------------------------------------------------------- badnews
def score_badnews(r: Row) -> Alert | None:
    gap = r.f("Gap") or 0
    chg = r.f("Change") or 0
    relvol = r.f("Relative Volume") or 1
    sma50 = r.f("50-Day Simple Moving Average") or 0
    recom = r.f("Analyst Recom")
    edate, _ = r.earnings
    recent_earn = edate is not None and abs((edate - date.today()).days) <= 1

    score, why = 0, []
    if gap <= -10: score += 30; why.append(f"gap {_pct(gap)}")
    elif gap <= -5: score += 20; why.append(f"gap {_pct(gap)}")
    if chg <= -10: score += 20; why.append(f"today {_pct(chg)}")
    elif chg <= -5: score += 10; why.append(f"today {_pct(chg)}")
    if relvol >= 3: score += 20; why.append(f"rel vol {relvol:.1f}x")
    elif relvol >= 2: score += 12; why.append(f"rel vol {relvol:.1f}x")
    if recent_earn: score += 15; why.append(f"earnings {edate}")
    if sma50 < 0: score += 10; why.append("below SMA50")
    if recom is not None and recom >= 3: score += 5
    if score < MIN_SCORE:
        return None
    return Alert(r.ticker, "badnews", "SHORT", min(score, 100),
                 f"{r.ticker} selling on news/earnings", why, r.f("Price"))


SCORERS = {
    "breakout": score_breakout,
    "undervalued": score_undervalued,
    "exhaustion": score_exhaustion,
    "badnews": score_badnews,
}
