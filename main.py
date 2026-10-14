"""APEX MARKET SNIPER — 24/7 scanner service.

Usage:
  python main.py              run the scheduler (foreground)
  python main.py once         run every job once now (dry test, pushes to Telegram)
  python main.py once --quiet same, but log instead of pushing
  python main.py cron         one stateless pass (GitHub Actions / any external cron)
  python main.py check        verify Finviz key, columns and Telegram
  python main.py digest am|pm send a digest now
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from sniper import config
from sniper.engine import Engine, us_market_open

logging.basicConfig(
    level=config.LOG_LEVEL,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(config.ROOT / "data" / "sniper.log", encoding="utf-8")],
)
log = logging.getLogger("main")


def build_engine(push: bool = True) -> Engine:
    config.require("FINVIZ_AUTH")
    (config.ROOT / "data").mkdir(exist_ok=True)
    return Engine(push=push)


def job_intraday(eng: Engine) -> None:
    if not us_market_open():
        return
    n = eng.run_scans("intraday")
    n += eng.check_filings()
    log.info("intraday cycle: %d alerts", n)
    eng.export_json()


def job_premarket(eng: Engine) -> None:
    eng.refresh_catalysts()
    n = eng.run_scans("daily")
    eng.track_outcomes()
    log.info("pre-market scan: %d alerts", n)
    eng.digest("am")
    eng.export_json()


def job_postclose(eng: Engine) -> None:
    eng.track_outcomes()
    eng.digest("pm")
    eng.export_json()


def job_cron(eng: Engine) -> None:
    """One stateless pass for GitHub Actions: decide what is due from the clock.

    Runs every 15 min. Pre-market and post-close jobs fire once per day, tracked
    in the store so a late or duplicate cron tick doesn't double-send.
    """
    uk = datetime.now(ZoneInfo(config.TZ_UK))
    ny = datetime.now(ZoneInfo(config.TZ_NY))
    today = uk.date().isoformat()
    weekday = uk.weekday() < 5
    eng.refresh_catalysts()
    if weekday and uk.hour >= 7 and eng.store.seen_once(f"digest:am:{today}"):
        n = eng.run_scans("daily")
        eng.track_outcomes()
        log.info("pre-market scan: %d alerts", n)
        eng.digest("am")
    if us_market_open():
        n = eng.run_scans("intraday") + eng.check_filings()
        log.info("intraday cycle: %d alerts", n)
    else:
        eng.check_filings()
    if weekday and (ny.hour, ny.minute) >= (16, 30) and eng.store.seen_once(f"digest:pm:{today}"):
        eng.track_outcomes()
        eng.digest("pm")
    eng.export_json()


def run_scheduler() -> None:
    eng = build_engine()
    eng.refresh_catalysts()
    uk, ny = ZoneInfo(config.TZ_UK), ZoneInfo(config.TZ_NY)
    sched = BlockingScheduler(timezone=uk)
    # Pre-market digest 07:00 UK, Mon-Fri (US futures already moving, FDA dates known)
    sched.add_job(job_premarket, CronTrigger(day_of_week="mon-fri", hour=7, minute=0, timezone=uk),
                  args=[eng], id="premarket", misfire_grace_time=600)
    # Intraday polling during US cash session (gated inside the job)
    sched.add_job(job_intraday, "interval", minutes=config.INTRADAY_POLL_MINUTES,
                  args=[eng], id="intraday", max_instances=1, coalesce=True)
    # Post-close digest 16:30 NY (21:30 UK in BST, 21:30 GMT in winter — follows NY)
    sched.add_job(job_postclose, CronTrigger(day_of_week="mon-fri", hour=16, minute=30, timezone=ny),
                  args=[eng], id="postclose", misfire_grace_time=600)
    # Refresh catalyst calendar every 6h and filings-only poll outside RTH every 15 min
    sched.add_job(eng.refresh_catalysts, "interval", hours=6, id="catalysts")
    sched.add_job(lambda: None if us_market_open() else eng.check_filings(),
                  "interval", minutes=15, id="filings_offhours")
    log.info("Scheduler started %s", datetime.now(uk).isoformat(timespec="minutes"))
    sched.start()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "cron":
        job_cron(build_engine())
    elif args and args[0] == "once":
        eng = build_engine(push="--quiet" not in args)
        eng.refresh_catalysts()
        eng.run_scans("daily")
        eng.run_scans("intraday")
        eng.check_filings()
        eng.digest("am")
        eng.export_json()
    elif args and args[0] == "check":
        # Verify Finviz key + column mapping and Telegram in one go.
        from sniper.finviz import COLUMNS, FinvizClient
        from sniper import telegram
        config.require("FINVIZ_AUTH")
        rows = FinvizClient().export(filters="sh_avgvol_o1000,sh_price_o50", order="-marketcap")
        if not rows:
            raise SystemExit("Finviz export returned nothing — check FINVIZ_AUTH / Elite status")
        got = set(rows[0].raw.keys())
        missing = [c for c in COLUMNS if c not in got]
        print(f"Finviz OK: {len(rows)} rows, {len(got)} columns")
        print("Missing expected columns:", missing or "none")
        print("Sample:", rows[0].ticker, rows[0].s("Price"), rows[0].s("Earnings Date"),
              rows[0].s("52-Week High"), rows[0].s("Relative Volume"))
        ok = telegram.send("APEX MARKET SNIPER: check OK ✅")
        print("Telegram:", "sent" if ok else "not configured / failed")
    elif args and args[0] == "digest":
        eng = build_engine()
        eng.refresh_catalysts()
        eng.digest(args[1] if len(args) > 1 else "am")
    else:
        run_scheduler()
