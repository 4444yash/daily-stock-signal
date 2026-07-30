"""
One-off backfill: reconstructs trade_history.json from the git history of
active_trades.json.

Every daily run committed the open-position state. When a symbol disappears
between two consecutive commits, that position was closed on the later commit's
`last_updated` date. The exit price is recovered by replaying the scanner's own
rule against that day's actual bar:

    open <= stop  -> exit at the open   (gap-down)
    otherwise     -> exit at the close  (trailing stop breach)

Symbols that reappear later with the same entry_date were manual corrections,
not real exits, so those pseudo-exits are discarded.

    python rebuild_history_from_git.py            # write trade_history.json
    python rebuild_history_from_git.py --dry-run  # inspect only
"""

import argparse
import datetime
import json
import os
import subprocess
import sys

import pandas as pd
import yfinance as yf

from daily_scanner import calculate_indicators

FILE = "active_trades.json"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


def snapshots():
    """Chronological list of (sha, parsed json) for every commit touching FILE."""
    log = git("log", "--reverse", "--format=%H", "--follow", "--", FILE)
    out = []
    for sha in [l.strip() for l in log.splitlines() if l.strip()]:
        blob = git("show", f"{sha}:{FILE}")
        if not blob.strip():
            continue
        try:
            out.append((sha, json.loads(blob)))
        except json.JSONDecodeError:
            continue
    return out


def ticker_for(symbol, watchlist):
    for s in watchlist:
        if s["symbol"] == symbol:
            return s["ticker"]
    return f"{symbol}.NS"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    workspace = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(workspace, "watchlist.json")) as f:
        watchlist = json.load(f)["stocks"]

    snaps = snapshots()
    print(f"Found {len(snaps)} committed states of {FILE}.")
    if len(snaps) < 2:
        print("Not enough history to reconstruct.")
        return

    # Detect disappearances between consecutive states.
    candidates = []          # provisional exits
    reappeared = set()       # (symbol, entry_date) seen again after a disappearance

    for (sha_a, a), (sha_b, b) in zip(snaps, snaps[1:]):
        prev = {t["symbol"]: t for t in a.get("trades", [])}
        curr = {t["symbol"]: t for t in b.get("trades", [])}
        exit_date = b.get("last_updated") or a.get("last_updated")

        for sym, trade in prev.items():
            if sym in curr:
                continue
            entry_date = trade.get("entry_date") or trade.get("signal_date")
            candidates.append({
                "symbol": sym,
                "trade": trade,
                "exit_date": exit_date,
                "sha": sha_b,
            })

        # Anything present now that was previously marked gone with the same entry
        for sym, trade in curr.items():
            key = (sym, trade.get("entry_date") or trade.get("signal_date"))
            if any(c["symbol"] == sym for c in candidates):
                reappeared.add(key)

    # Anything present in the newest committed state is still open, so any earlier
    # disappearance was a manual correction rather than a real exit.
    open_now = {t["symbol"] for t in snaps[-1][1].get("trades", [])}
    real = []
    for c in candidates:
        if c["symbol"] in open_now:
            print(f"  skipping {c['symbol']} @ {c['exit_date']}: still open in the latest state")
            continue
        real.append(c)

    # Deduplicate on (symbol, entry_date), keeping the last recorded exit
    dedup = {}
    for c in real:
        key = (c["symbol"], c["trade"].get("entry_date") or c["trade"].get("signal_date"))
        dedup[key] = c
    real = list(dedup.values())
    print(f"{len(real)} closed position(s) to price.\n")

    trades = []
    for c in real:
        sym = c["symbol"]
        t = c["trade"]
        entry_date = t.get("entry_date") or t.get("signal_date")
        entry_price = float(t["entry_price"])
        stop = float(t.get("current_stop", 0) or 0)
        exit_date = c["exit_date"]
        tkr = ticker_for(sym, watchlist)

        # Normalise the stored entry/stop into the split-adjusted terms Yahoo serves,
        # otherwise a split reads as a catastrophic gap-down.
        split_factor = 1.0
        try:
            splits = yf.Ticker(tkr).splits
            entry_dt = datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()
            if splits is not None and len(splits):
                for ts, ratio in splits.items():
                    if ts.date() > entry_dt and ratio and float(ratio) > 0:
                        split_factor *= float(ratio)
        except Exception as e:
            print(f"  {sym}: split lookup failed ({e})")
        if split_factor != 1.0:
            entry_price /= split_factor
            stop /= split_factor
            print(f"  {sym}: {split_factor:g}:1 split after entry — entry restated to {entry_price:.2f}")

        # Replay the scanner's own exit test against the real bar, so a split-driven
        # phantom exit can be told apart from a genuine trailing-stop hit.
        exit_price, reason, excluded, exclude_reason = None, "SL/TSL Hit", False, None
        try:
            exit_dt = datetime.datetime.strptime(exit_date, "%Y-%m-%d").date()
            df = yf.download(tkr, start=exit_dt - datetime.timedelta(days=90),
                             end=exit_dt + datetime.timedelta(days=4),
                             progress=False, auto_adjust=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.reset_index()
            df.columns = [str(x).lower() for x in df.columns]
            df = calculate_indicators(df.sort_values("date").reset_index(drop=True))
            df["d"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            hit = df.index[df["d"] == exit_date]
            if len(hit):
                i = hit[0]
                o, h, l, cl = (float(df.at[i, k]) for k in ("open", "high", "low", "close"))
                atr10 = df.at[i, "atr10"]
                atr10 = float(atr10) if not pd.isna(atr10) else cl * 0.03

                trailed = (h + l) / 2.0 - 3.0 * atr10
                trailed = max(stop, trailed) if stop else trailed

                if stop and o <= stop:
                    exit_price, reason = o, "SL Hit (Open Gap)"
                elif cl <= trailed:
                    # Genuine breach under the current close-based rule.
                    exit_price, reason = cl, "SL/TSL Hit"
                elif stop and l <= stop:
                    # Legitimate under the intraday-low rule that applied before the
                    # scanner switched to close-based exits (commit 02aac42).
                    exit_price, reason = stop, "SL Hit (intraday, legacy rule)"
                else:
                    # Stop was never breached on split-adjusted prices.
                    exit_price, reason = cl, "Closed in error"
                    excluded, exclude_reason = True, (
                        f"Left the portfolio on {exit_date} without any stop breach "
                        f"(close {cl:.2f}, low {l:.2f}, stop {trailed:.2f}). "
                        + (f"Caused by an unadjusted {split_factor:g}:1 split. "
                           if split_factor != 1.0 else "")
                        + "Excluded from performance statistics.")
        except Exception as e:
            print(f"  {sym}: price replay failed ({e})")

        if exit_price is None:
            # Fall back to the last stop level the scanner had recorded.
            exit_price = stop or t.get("latest_price") or entry_price
            reason = "SL/TSL Hit (reconstructed)"

        pnl = (exit_price - entry_price) / entry_price * 100
        try:
            hold = (datetime.datetime.strptime(exit_date, "%Y-%m-%d").date()
                    - datetime.datetime.strptime(entry_date, "%Y-%m-%d").date()).days
        except Exception:
            hold = None

        rec = {
            "symbol": sym,
            "batch": t.get("batch", ""),
            "signal_date": t.get("signal_date", entry_date),
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "peak_price": round(float(t.get("latest_price", exit_price)), 2),
            "pnl_pct": round(pnl, 2),
            "hold_days": hold,
            "prob": t.get("prob"),
            "reason": reason,
            "source": "reconstructed_from_git",
        }
        if split_factor != 1.0:
            rec["split_factor"] = split_factor
        if excluded:
            rec["excluded"] = True
            rec["exclude_reason"] = exclude_reason
        trades.append(rec)
        print(f"  {sym:12s} {entry_date} -> {exit_date}  "
              f"{entry_price:>9.2f} -> {exit_price:>9.2f}  {pnl:+7.2f}%  {reason}")

    trades.sort(key=lambda r: (r["exit_date"], r["symbol"]))
    payload = {
        "last_updated": snaps[-1][1].get("last_updated", ""),
        "note": "Backfilled from the git history of active_trades.json; later trades are "
                "logged directly by daily_scanner.py.",
        "trades": trades,
    }

    if args.dry_run:
        print("\n(dry run, nothing written)")
        return

    out = os.path.join(workspace, "trade_history.json")
    if os.path.exists(out):
        with open(out) as f:
            existing = json.load(f).get("trades", [])
        keys = {(t["symbol"], t.get("entry_date")) for t in trades}
        merged = trades + [t for t in existing if (t["symbol"], t.get("entry_date")) not in keys]
        merged.sort(key=lambda r: (r["exit_date"], r["symbol"]))
        payload["trades"] = merged

    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"\nWrote {out} with {len(payload['trades'])} closed trades.")

    # Seed the scan log with the dates the workflow demonstrably ran. Trigger counts
    # were never recorded back then, so those runs are flagged `seeded` and kept out
    # of the funnel statistics.
    log_path = os.path.join(workspace, "scan_log.json")
    existing_runs = []
    if os.path.exists(log_path):
        with open(log_path) as f:
            existing_runs = json.load(f).get("runs", [])
    known = {r.get("date") for r in existing_runs}
    seeded = []
    for _, snap in snaps:
        d = snap.get("last_updated")
        if not d or d in known:
            continue
        known.add(d)
        seeded.append({
            "date": d,
            "seeded": True,
            "open_positions": len(snap.get("trades", [])),
        })
    runs = sorted(existing_runs + seeded, key=lambda r: r.get("date") or "")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump({"last_updated": runs[-1]["date"] if runs else "",
                   "note": "Runs marked `seeded` were reconstructed from git history and "
                           "carry only the date and open-position count.",
                   "runs": runs}, f, indent=2)
    print(f"Wrote {log_path} with {len(runs)} runs ({len(seeded)} seeded).")


if __name__ == "__main__":
    main()
