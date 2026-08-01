# Quarterly watchlist refresh

The watchlist is rebuilt every quarter from the fundamental screen in
[`query.txt`](query.txt). One manual step, then everything else is automated.

## The one manual step

1. Open [Screener.in](https://www.screener.in), paste the query from `query.txt`, run it.
2. Export the result to CSV.
3. Save it here as `screener/exports/screen_YYYY-MM-DD.csv` using the date you ran it.
4. Commit.

Committing that file triggers `.github/workflows/watchlist_refresh.yml`, which does
the rest.

## What the automation does

`build_watchlist.py` picks up the newest export and:

- reads the symbol column, whatever it happens to be called
- resolves each name to a Yahoo ticker, trying `.NS` then `.BO`
- verifies each ticker actually returns price history, so a dead symbol cannot
  silently enter the watchlist and fail every scan
- splits the survivors into four batches for reporting continuity
- archives the outgoing list to `watchlist_history/`
- writes the new `watchlist.json` with full provenance
- prints a diff: added, removed, retained
- **warns loudly if you hold an open position in a name that just dropped out**

Nothing is deleted from `active_trades.json`. A stock leaving the screen does not
close the trade; the trailing stop still governs the exit. The warning exists so
the decision is yours.

## Reviewing the result

The workflow opens a pull request rather than committing to `main`. Read the diff
summary in the PR body, confirm the added and removed names look sane, then merge.
The next daily scan picks up the new list automatically.

## Between quarters

`watchlist_refresh.yml` also runs monthly in `--audit` mode. It re-checks the two
criteria that *are* computable from free data — market capitalization band and
3-year sales and profit growth — and reports any holding that has drifted out.

This is an early warning, not an automatic removal. Promoter holding and debt to
equity cannot be verified this way (see `query.txt`), so a clean audit does not
mean a stock still passes the full screen.

## History

Every previous watchlist is kept in `watchlist_history/`. Beyond the audit trail,
this is what makes unbiased retraining possible later: to know which stocks were
tradeable at a past date you need point-in-time membership, not today's list.
Training on today's watchlist is survivorship-biased, which is why the dashboard
labels the backtest accordingly.
