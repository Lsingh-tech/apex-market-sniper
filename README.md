# APEX MARKET SNIPER

US-stock scanner on Finviz Elite + pdufa.bio + SEC EDGAR, with Grok (xAI) explaining
each move. Runs for free on GitHub Actions, pushes alerts to Telegram, and publishes a
mobile dashboard on GitHub Pages. No server needed.

## What it does

| Module | Source | Fires when |
|---|---|---|
| **Breakouts** | Finviz | Within 5% of 52W high, relative volume up, tight base, above SMAs |
| **Value** | Finviz | Cheap on FCF, low debt, insiders buying, growth positive |
| **Exhaustion** | Finviz | RSI > 75, stretched above SMA20, parabolic week, high short float, fading |
| **Bad news** | Finviz | Earnings gap-down, downgrades, top losers on heavy volume |
| **FDA** | pdufa.bio | T-30 / T-7 / T-1 / decision day for every dated PDUFA, AdComm, readout |
| **8-K** | SEC EDGAR | New 8-K on tracked tickers; negative items flagged |

Every alert is scored 0–100 with its reasons listed. Nothing is pushed below 55.
Grok adds a two-line "why it's moving" and an X sentiment read to breakout, exhaustion,
bad-news, FDA and 8-K alerts (capped at 60 calls/day by default).
Alerts are logged, re-priced after 5 days, and the scorecard shows which modules work.

Digests: **07:00 UK** pre-market and **16:30 New York** post-close.
Intraday scans every 15 minutes during the US cash session (GitHub) or every 2 minutes (VPS).

## Set up on GitHub (free, ~15 minutes)

1. **Create a repo** (public is simplest: free Pages). Upload this folder's contents.
2. **Secrets** — repo → Settings → Secrets and variables → Actions → New repository secret:
   - `FINVIZ_AUTH` — your Finviz Elite export key
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
   - `XAI_API_KEY` — optional; leave out to run without Grok
   - `EDGAR_USER_AGENT` — e.g. `ApexMarketSniper you@email.com` (SEC requires a contact)
   - `PDUFA_API_KEY` — optional free key from pdufa.bio for a higher quota
3. **Pages** — Settings → Pages → Source: *Deploy from a branch* → `main` / `docs`.
   Your dashboard is then at `https://<user>.github.io/<repo>/`.
4. **First run** — Actions tab → *APEX Market Sniper* → *Run workflow*. Within a minute
   you'll get a Telegram message and `docs/data.json` appears; the dashboard goes live
   on the next Pages deploy (a minute or two).

The workflow commits `data/sniper.db` and `docs/data.json` back to the repo after each
run — that's how dedup, outcome tracking and the dashboard persist without a server.

Telegram: create a bot with @BotFather, message it once, then read your chat id from
`https://api.telegram.org/bot<TOKEN>/getUpdates`.

## Run locally or on a VPS instead

```
python -m venv venv && venv\Scripts\activate     (Windows)   |   source venv/bin/activate (Linux)
pip install -r requirements.txt
copy .env.example .env                           then fill it in
python main.py check          verify Finviz key, column mapping, Telegram
python main.py once --quiet   run everything now, log instead of pushing
python main.py                scheduler, 2-minute intraday polling
```

Linux `systemd` unit and Windows NSSM both work; ask and I'll write it.

## Tuning

- **Scans** — `scans/scans.yaml`. Build a screen in the Finviz Elite UI, copy the URL,
  paste its `f=` into `filters` and `s=` into `signal`. `module` picks the scorer.
- **Weights / thresholds** — `sniper/scoring.py`, one plain function per module.
- **Watchlist** — `scans/watchlist.txt`: +5 score, tracked for 8-Ks.
- **Grok** — env `GROK_ENABLED`, `GROK_DAILY_CAP`, `GROK_MODULES`, `XAI_MODEL`.
  If xAI changes its request format, `sniper/grok.py::_body()` is the only function to edit.
- **Dashboard** — `docs/index.html` is a single file reading `docs/data.json`.

## Notes

- `python main.py check` prints any Finviz columns that didn't come back as expected.
  Rows are parsed by header name, so a renumbered column degrades to `None` rather than crashing.
- pdufa.bio free tier requires a visible link back — the dashboard footer and every FDA alert carry one.
- Not investment advice. Setups and dated events, not predictions.
