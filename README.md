# Daily Stock Signal

An automated swing-trading system for Indian equities. A GitHub Actions workflow scans a
166-symbol watchlist every trading day, scores volatility-breakout setups with an XGBoost
model, manages trailing stops on open positions, pushes a phone alert, and publishes a
live dashboard.

**Dashboard:** https://4444yash.github.io/daily-stock-signal/

## The strategy

A position is opened only when four technical conditions fire on the same daily bar:

| Condition | Test |
|---|---|
| Squeeze | Bollinger band width under 10% at some point in the last 5 sessions |
| Volume expansion | Volume above 1.5x the 20-day average |
| Momentum ignition | RSI(14) between 55 and 70, rising more than 8 points in a day |
| Structure | A new 20-day high |

Every trigger is then scored by an XGBoost classifier on 12 features covering the squeeze,
the breakout bar, volatility, trend context, Nifty regime, relative strength and prior
run-up. Only setups scoring **0.65 or higher** are taken, which filters out roughly 90% of
raw triggers.

Exits are mechanical: a Chandelier trailing stop at `(High + Low) / 2 - 3 x ATR(10)`,
evaluated on the daily close with gap-down protection at the open. The stop only ratchets
upward. There is no profit target and no time limit.

## Layout

| File | Role |
|---|---|
| `daily_scanner.py` | The daily job: scans, scores, manages stops, logs, alerts |
| `build_dashboard.py` | Turns the JSON ledgers into `docs/data/dashboard_data.json` |
| `build_backtest.py` | One-off historical simulation of the same logic |
| `rebuild_history_from_git.py` | One-off backfill of closed trades from git history |
| `watchlist.json` | The scanned universe |
| `active_trades.json` | Open positions, rewritten each run |
| `trade_history.json` | Permanent ledger of closed trades |
| `scan_log.json` | One record per scan: triggers found, signals taken, exits |
| `backtest_history.json` | Historical backtest output |
| `docs/` | The static dashboard published to GitHub Pages |

## Data flow

```
yfinance ──> daily_scanner.py ──> active_trades.json
                               ├─> trade_history.json
                               ├─> scan_log.json
                               └─> ntfy.sh push alert
                                        │
                        build_dashboard.py
                                        │
                     docs/data/dashboard_data.json
                                        │
                          GitHub Pages (docs/)
```

The workflow commits the updated ledgers back to `main`, then deploys `docs/` to Pages, so
the dashboard always reflects exactly what the automation recorded.

## Setup

1. **Notifications.** Add a repository secret `NTFY_TOPIC` with your
   [ntfy.sh](https://ntfy.sh) topic name. Without it the scanner runs in console-only mode.
2. **Pages.** In *Settings → Pages*, set **Source** to **GitHub Actions**. The workflow
   handles the rest.
3. **Schedule.** The scan runs weekdays at 10:45 UTC (16:15 IST), after the NSE close.
   Trigger it manually any time from the Actions tab.

### Deploying to Vercel instead

`vercel.json` is included, so importing the repo into Vercel works with no configuration.
Vercel redeploys automatically on every commit the workflow pushes.

## Running locally

```bash
pip install -r requirements.txt

python daily_scanner.py                  # full scan, console-only without NTFY_TOPIC
python build_dashboard.py                # rebuild the dashboard payload
python build_backtest.py --years 4       # regenerate the historical backtest
python -m http.server 8000 -d docs       # then open http://localhost:8000
```

Regenerate the backtest whenever the watchlist or the model changes.

## Reading the dashboard honestly

- Equity curves size each position at **20% of capital** (5 concurrent slots). Signals
  overlap in time, so compounding at full capital would badly overstate the result.
  Per-trade metrics such as win rate, expectancy and payoff ratio are sizing-independent.
- Returns are net of an assumed **0.15% round-trip** cost.
- Entry price uses the signal day's close as a proxy for the next open, which understates
  slippage on gap-up entries.
- The backtest uses today's watchlist, so it carries survivorship bias. Treat it as a
  sanity check on the logic, not a forecast.
- The live ledger starts when trade logging was added; earlier closed trades were
  reconstructed from the commit history of `active_trades.json`.
- Trades that left the portfolio without a genuine stop breach are flagged in the
  dashboard's **data integrity log** and excluded from every statistic.

Nothing here is investment advice.

## A note on corporate actions

Yahoo Finance serves split-adjusted prices, but stored entry prices and stops are in the
price terms of the entry date. After a split the two are not comparable, and an early
version of the scanner read a 1:5 split in KRISHANA as an 80% gap-down and closed a
healthy position. `daily_scanner.py` now rescales stored levels by any split that occurred
after entry, tracking what has already been applied so repeat runs stay idempotent.
