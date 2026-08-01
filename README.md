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
| `build_backtest.py` | Historical simulation, and the shared feature/exit functions |
| `build_training_data.py` | Regenerates the labelled training set from yfinance |
| `retrain.py` | Trains a candidate, compares it, writes the evidence report |
| `build_watchlist.py` | Turns a Screener.in export into `watchlist.json` |
| `rebuild_history_from_git.py` | One-off backfill of closed trades from git history |
| `train_xgboost_asymmetric.py` | The original trainer that produced the live model |
| `watchlist.json` | The scanned universe, with provenance |
| `watchlist_history/` | Every previous watchlist, for point-in-time work |
| `screener/` | The fundamental screen, its exports, and the refresh process |
| `active_trades.json` | Open positions, rewritten each run |
| `trade_history.json` | Permanent ledger of closed trades |
| `scan_log.json` | One record per scan: triggers found, signals taken, exits |
| `backtest_history.json` | Historical backtest output |
| `model_reports/` | Retrain reports, model cards, candidate models |
| `docs/` | The static dashboard published to GitHub Pages |

## Scheduled automation

| Workflow | When | What it does |
|---|---|---|
| `daily_scan.yml` | Weekdays 10:45 UTC | Scans, manages stops, alerts, republishes the dashboard |
| `retrain.yml` | 1st of Jan/Apr/Jul/Oct | Trains a candidate, opens a PR with the evidence |
| `watchlist_refresh.yml` | On screener export, plus monthly | Rebuilds the watchlist, or audits criteria |

Only the daily scan commits to `main` directly. Both quarterly jobs open pull
requests, because a bad model or a wrong watchlist would quietly cost money for
three months.

## Retraining

The model is trained on a rolling window of the most recent 300 resolved trades,
with a 60-day resolution buffer so no signal is trained on before its outcome is
settled. Target is `trade_pnl >= 25%` — a 25% move, not merely a profitable
trade — because the strategy earns its return from a small number of large
winners, not from a high win rate.

```bash
python build_training_data.py --years 10 --source history   # rebuild the dataset
python retrain.py                                           # candidate + report
```

`retrain.py` never installs a model. It writes `model_reports/candidate_model.json`
alongside a report covering walk-forward lift, a gate threshold sweep, recent-period
decay, feature importance and feature drift, then gives a PROMOTE or HOLD verdict
against fixed criteria. Promotion is a manual copy.

Every figure is averaged over five random seeds. This matters more than it sounds:
on 300 rows with 100 trees, the seed alone moves mean gated P&L from +1.7% to
+7.6%. Single-run numbers here are close to meaningless, and
`results/xgboost_oos_predictions_asymmetric.csv` happens to be seed 42, near the
top of that range.

### On the training universe

79% of the current training data comes from Nifty 50 large caps, while the live
universe is ₹1,000–15,000cr small caps. That mismatch looks wrong, and removing the
large-cap rows was tested directly. It made things clearly worse:

| config | OOS trades | gated | lift | avg P&L | PF |
|---|---:|---:|---:|---:|---:|
| all data, window 300 (live) | 234 | 19.6 | 1.72 | +4.41% | 1.71 |
| all data, window 150 | 244 | 14.8 | 1.00 | +2.09% | 1.34 |
| small-cap only, window 150 | 49 | 0.8 | 0.00 | n/a | n/a |

Only 248 small-cap trades exist, below the 300-trade window, so a small-cap-only
model gates in roughly one trade and is not a functioning model. The large-cap rows
supply volume the model needs, and that structure evidently transfers.

The question is not settled, it is currently untestable. Answering it properly needs
a much larger screener-profile pool, which is what `build_training_data.py` exists to
produce. **Until then the live configuration stays as it is.**

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

## The watchlist

Built quarterly from a fundamental screen on Screener.in — profitable, efficiently
run small and mid caps with founder ownership, where leverage is tolerated only in
proportion to return on capital. The full query and its intent live in
[`screener/query.txt`](screener/query.txt).

The screen stays manual on purpose. Two of its criteria cannot be computed reliably
from free data: promoter holding is not exposed by yfinance, and its debt-to-equity
values come back in inconsistent units across symbols (GARUDA `2.649`, PRECWIRE
`39.377`, KSHINTL `39.74` — a mix of ratios and percentages). Since debt tiering is
the core of the query, automating it would silently produce a wrong watchlist.

So you export the CSV once a quarter and everything after that is automated:
ticker validation against Yahoo, batch assignment, archiving, and a diff showing
what was added, removed and retained. See [`screener/README.md`](screener/README.md).

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

## Data hygiene

Two Yahoo Finance quirks caused real damage before they were handled, so both are
guarded explicitly.

**Incomplete bars.** Yahoo can return a placeholder row for the current session with NaN
prices. Because every NaN comparison is false, the stop checks silently did nothing, the
trailing stop stopped ratcheting for that day, and NaN leaked into `active_trades.json` as
a bare `NaN` token that is not valid strict JSON. `drop_incomplete_bars()` now keeps only
fully-formed OHLC bars, and the state files are written with `allow_nan=False` so a stray
NaN fails loudly instead of producing an unparseable payload.

## A note on corporate actions

Yahoo Finance serves split-adjusted prices, but stored entry prices and stops are in the
price terms of the entry date. After a split the two are not comparable, and an early
version of the scanner read a 1:5 split in KRISHANA as an 80% gap-down and closed a
healthy position. `daily_scanner.py` now rescales stored levels by any split that occurred
after entry, tracking what has already been applied so repeat runs stay idempotent.
