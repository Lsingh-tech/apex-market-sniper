"""Offline smoke test: parser, scorers, store, engine wiring. No network."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["FINVIZ_AUTH"] = "test"
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "t.db")

from sniper import config  # noqa: E402
config.DB_PATH = Path(os.environ["DB_PATH"])

from sniper.finviz import FinvizClient, Row, parse_earnings, parse_num  # noqa: E402
from sniper.scoring import SCORERS  # noqa: E402
from sniper.store import Store  # noqa: E402
from sniper.engine import Engine  # noqa: E402
from sniper.scans import load_scans  # noqa: E402
from sniper import telegram  # noqa: E402

HEADER = ("No.,Ticker,Company,Sector,Market Cap,P/Free Cash Flow,Insider Transactions,"
          "EPS growth quarter over quarter,Sales growth quarter over quarter,Return on Equity,"
          "Total Debt/Equity,Float Short,Performance (Week),Performance (Month),"
          "Performance (Quarter),Volatility (Week),20-Day Simple Moving Average,"
          "50-Day Simple Moving Average,200-Day Simple Moving Average,52-Week High,"
          "Relative Strength Index (14),Change from Open,Gap,Analyst Recom,Relative Volume,"
          "Price,Change,Earnings Date")

CSV = HEADER + "\n" + "\n".join([
    # breakout candidate: 1% off high, relvol 2.8, tight, above SMAs
    '1,BRK1,Breakout Inc,Technology,4.20B,22.1,0.50%,12.00%,8.00%,14.00%,0.40,3.10%,4.00%,9.00%,22.00%,2.40%,3.10%,8.20%,21.00%,-1.10%,68.00,0.40%,0.30%,2.10,2.80,41.20,1.80%,Nov 05/a',
    # undervalued: P/FCF 8, insider +12%, D/E 0.1
    '2,VAL1,Value Corp,Industrials,900M,8.10,12.40%,18.00%,11.00%,19.00%,0.10,11.00%,-1.00%,-3.00%,-8.00%,2.00%,-1.00%,-4.00%,-9.00%,-31.00%,44.00,0.00%,0.00%,1.80,0.90,12.30,-0.20%,Oct 29/b',
    # exhaustion: RSI 88, 45% above SMA20, week +62%, short 24%, fading
    '3,PARA,Parabolic Ltd,Healthcare,350M,-,-2.00%,-,-,-20.00%,0.00,24.00%,62.00%,140.00%,210.00%,18.00%,45.00%,90.00%,160.00%,-2.00%,88.00,-3.50%,6.00%,3.00,0.70,9.80,4.00%,Nov 12/a',
    # badnews: gap -14, change -18, relvol 5.2, earnings yesterday
    '4,MISS,Missed Co,Consumer,2.1B,15.00,0.00%,-30.00%,-5.00%,5.00%,0.90,6.00%,-20.00%,-25.00%,-10.00%,9.00%,-15.00%,-22.00%,-30.00%,-40.00%,22.00,-2.00%,-14.00%,3.20,5.20,18.40,-18.00%,{EARN}',
    # noise row: should not alert anywhere
    '5,MEH,Nothing Corp,Utilities,5B,25.00,0.00%,2.00%,1.00%,8.00%,1.20,2.00%,0.50%,1.00%,3.00%,1.50%,0.50%,1.00%,3.00%,-12.00%,52.00,0.10%,0.00%,2.50,1.00,55.00,0.10%,Dec 02/a',
])


class FakeFinviz(FinvizClient):
    def __init__(self):
        self.calls = []

    def export(self, filters="", signal="", order="", cols="", tickers=None, retries=3):
        self.calls.append((filters, signal))
        from datetime import date, timedelta
        y = (date.today() - timedelta(days=1)).strftime("%b %d").replace(" 0", " ")
        rows = self.parse_csv(CSV.replace("{EARN}", y))
        if tickers:
            rows = [r for r in rows if r.ticker in tickers]
        return rows


def test_parsers():
    assert parse_num("12.5%") == 12.5
    assert parse_num("4.20B") == 4.2e9
    assert parse_num("-") is None
    from datetime import date
    d, w = parse_earnings("Nov 05/a", today=date(2026, 10, 14))
    assert d == date(2026, 11, 5) and w == "AMC"
    d, w = parse_earnings("Jan 20/b", today=date(2026, 12, 1))
    assert d == date(2027, 1, 20) and w == "BMO"


def test_scorers():
    rows = {r.ticker: r for r in FinvizClient.parse_csv(CSV.replace("{EARN}", "Oct 13/a"))}
    assert SCORERS["breakout"](rows["BRK1"]) is not None
    assert SCORERS["undervalued"](rows["VAL1"]) is not None
    assert SCORERS["exhaustion"](rows["PARA"]) is not None
    a = SCORERS["badnews"](rows["MISS"])
    assert a is not None and a.direction == "SHORT"
    for m in SCORERS:
        assert SCORERS[m](rows["MEH"]) is None, m


def test_engine_dedup():
    fv = FakeFinviz()
    store = Store()
    eng = Engine(fv=fv, store=store, scans=load_scans(), push=False)
    n1 = eng.run_scans("intraday") + eng.run_scans("daily")
    n2 = eng.run_scans("intraday") + eng.run_scans("daily")
    assert n1 >= 4, n1
    assert n2 == 0, "dedup failed"
    assert len(fv.calls) == 2 * len(load_scans())
    msg = telegram.fmt_alert(SCORERS["breakout"](fv.export()[0]))
    assert "BRK1" in msg and "<b>" in msg
    eng.digest("am")


if __name__ == "__main__":
    test_parsers(); print("parsers ok")
    test_scorers(); print("scorers ok")
    test_engine_dedup(); print("engine ok")
