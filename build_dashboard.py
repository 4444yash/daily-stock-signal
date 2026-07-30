"""
Builds docs/data/dashboard_data.json - the single payload the web dashboard reads.

Inputs (repo root):
    active_trades.json      open positions, maintained by daily_scanner.py
    trade_history.json      closed forward-tested trades, appended by daily_scanner.py
    scan_log.json           one record per daily scan run
    backtest_history.json   optional one-off historical simulation (build_backtest.py)
    watchlist.json          scanned universe

Run standalone with:  python build_dashboard.py
"""

import json
import os
import datetime
from collections import Counter, defaultdict

COST_PCT = 0.15           # round-trip brokerage + slippage assumption, in percent
PROB_THRESHOLD = 0.65     # model gate used by daily_scanner.py
ALLOCATION = 0.20         # fraction of equity per position: 5 concurrent slots.
                          # Signals overlap in time, so compounding at 100% per
                          # trade would badly overstate what the strategy could
                          # actually have earned.


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _r(value, digits=2):
    """Round, tolerating None/NaN."""
    if value is None:
        return None
    try:
        if value != value:      # NaN
            return None
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _date(value):
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value)[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def _net(pnl_pct):
    """Apply round-trip cost assumption to a gross percentage return."""
    return pnl_pct - COST_PCT


def compute_stats(trades, label):
    """Core performance block for a list of closed trades."""
    rows = []
    for t in trades:
        gross = t.get("pnl_pct")
        if gross is None or t.get("excluded"):
            continue
        rows.append({
            "symbol": t.get("symbol"),
            "batch": t.get("batch") or "",
            "signal_date": t.get("signal_date"),
            "entry_date": t.get("entry_date"),
            "exit_date": t.get("exit_date"),
            "entry_price": _r(t.get("entry_price")),
            "exit_price": _r(t.get("exit_price")),
            "pnl_pct": _r(gross),
            "net_pnl_pct": _r(_net(gross)),
            "hold_days": t.get("hold_days"),
            "prob": _r(t.get("prob"), 4),
            "reason": t.get("reason") or "",
        })

    rows.sort(key=lambda r: (r["exit_date"] or "", r["symbol"] or ""))

    stats = {
        "label": label,
        "trades": rows,
        "count": len(rows),
    }
    if not rows:
        stats.update({
            "wins": 0, "losses": 0, "win_rate": None, "avg_win": None, "avg_loss": None,
            "expectancy": None, "profit_factor": None, "payoff_ratio": None,
            "best": None, "worst": None, "avg_hold_days": None, "total_return_pct": None,
            "cumulative_pnl_pct": None, "max_drawdown_pct": None, "equity_curve": [],
            "monthly": [], "prob_buckets": [], "exit_reasons": [], "batches": [],
            "max_win_streak": 0, "max_loss_streak": 0, "current_streak": 0,
            "first_exit": None, "last_exit": None,
        })
        return stats

    nets = [r["net_pnl_pct"] for r in rows]
    wins = [v for v in nets if v > 0]
    losses = [v for v in nets if v <= 0]

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    # Equity curve: each trade risks ALLOCATION of equity, sequenced by exit date.
    equity = 100.0
    peak = 100.0
    max_dd = 0.0
    curve = []
    for r in rows:
        equity *= (1.0 + ALLOCATION * r["net_pnl_pct"] / 100.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, (equity - peak) / peak * 100.0)
        curve.append({
            "date": r["exit_date"],
            "symbol": r["symbol"],
            "equity": _r(equity),
            "pnl": r["net_pnl_pct"],
        })

    # Streaks
    max_w = max_l = cur_w = cur_l = 0
    for v in nets:
        if v > 0:
            cur_w, cur_l = cur_w + 1, 0
            max_w = max(max_w, cur_w)
        else:
            cur_l, cur_w = cur_l + 1, 0
            max_l = max(max_l, cur_l)
    current_streak = cur_w if cur_w else -cur_l

    # Monthly aggregation, compounded within each month
    by_month = defaultdict(list)
    for r in rows:
        d = _date(r["exit_date"])
        if d:
            by_month[f"{d.year}-{d.month:02d}"].append(r["net_pnl_pct"])
    monthly = []
    for month in sorted(by_month):
        vals = by_month[month]
        mult = 1.0
        for v in vals:
            mult *= (1.0 + ALLOCATION * v / 100.0)
        monthly.append({
            "month": month,
            "return_pct": _r((mult - 1.0) * 100.0),
            "trades": len(vals),
            "wins": sum(1 for v in vals if v > 0),
        })

    # Model calibration: realised win rate per predicted-probability bucket
    edges = [(PROB_THRESHOLD, 0.75), (0.75, 0.85), (0.85, 0.95), (0.95, 1.01)]
    buckets = []
    for lo, hi in edges:
        sel = [r for r in rows if r["prob"] is not None and lo <= r["prob"] < hi]
        if not sel:
            continue
        sel_nets = [r["net_pnl_pct"] for r in sel]
        sel_wins = [v for v in sel_nets if v > 0]
        buckets.append({
            "range": f"{lo:.2f}-{min(hi, 1.0):.2f}",
            "trades": len(sel),
            "win_rate": _r(len(sel_wins) / len(sel) * 100.0, 1),
            "avg_prob": _r(sum(r["prob"] for r in sel) / len(sel) * 100.0, 1),
            "avg_pnl": _r(sum(sel_nets) / len(sel_nets)),
        })

    reason_counts = Counter(r["reason"] or "Unknown" for r in rows)
    exit_reasons = [{"reason": k, "count": v} for k, v in reason_counts.most_common()]

    by_batch = defaultdict(list)
    for r in rows:
        by_batch[r["batch"] or "Unclassified"].append(r["net_pnl_pct"])
    batches = []
    for name in sorted(by_batch):
        vals = by_batch[name]
        b_wins = [v for v in vals if v > 0]
        batches.append({
            "batch": name,
            "trades": len(vals),
            "win_rate": _r(len(b_wins) / len(vals) * 100.0, 1),
            "avg_pnl": _r(sum(vals) / len(vals)),
            "total_pnl": _r(sum(vals)),
        })

    holds = [r["hold_days"] for r in rows if isinstance(r["hold_days"], (int, float))]

    stats.update({
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": _r(len(wins) / len(rows) * 100.0, 1),
        "avg_win": _r(gross_win / len(wins)) if wins else None,
        "avg_loss": _r(sum(losses) / len(losses)) if losses else None,
        "expectancy": _r(sum(nets) / len(nets)),
        "profit_factor": _r(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "payoff_ratio": _r((gross_win / len(wins)) / abs(sum(losses) / len(losses)))
                        if wins and losses and sum(losses) != 0 else None,
        "best": _r(max(nets)),
        "worst": _r(min(nets)),
        "avg_hold_days": _r(sum(holds) / len(holds), 1) if holds else None,
        "total_return_pct": _r(equity - 100.0),
        "cumulative_pnl_pct": _r(sum(nets)),
        "max_drawdown_pct": _r(max_dd),
        "equity_curve": curve,
        "monthly": monthly,
        "prob_buckets": buckets,
        "exit_reasons": exit_reasons,
        "batches": batches,
        "max_win_streak": max_w,
        "max_loss_streak": max_l,
        "current_streak": current_streak,
        "first_exit": rows[0]["exit_date"],
        "last_exit": rows[-1]["exit_date"],
    })
    return stats


def build_open_positions(active_trades):
    positions = []
    for t in active_trades:
        entry = t.get("entry_price")
        last = t.get("latest_price", entry)
        stop = t.get("current_stop")
        peak = t.get("peak_price") or max(entry or 0, last or 0)
        prev = t.get("prev_price")
        if not entry:
            continue

        unreal = (last - entry) / entry * 100.0
        # R is measured against the risk taken at entry. Fall back to the live stop
        # only while it still sits below entry; once it has trailed above, the
        # original risk is unknowable for trades logged before it was recorded.
        risk_ref = t.get("initial_stop")
        if risk_ref is None and stop is not None and stop < entry:
            risk_ref = stop
        initial_risk = None
        r_multiple = None
        if risk_ref is not None and risk_ref < entry:
            initial_risk = (entry - risk_ref) / entry * 100.0
        # Risk now locked in or still exposed, measured against the live stop
        locked = (stop - entry) / entry * 100.0 if stop is not None else None
        if initial_risk and initial_risk > 0:
            r_multiple = unreal / initial_risk

        d_entry = _date(t.get("entry_date"))
        d_last = _date(t.get("latest_date"))
        days = (d_last - d_entry).days if d_entry and d_last else None

        positions.append({
            "symbol": t.get("symbol"),
            "batch": t.get("batch") or "",
            "signal_date": t.get("signal_date"),
            "entry_date": t.get("entry_date"),
            "entry_price": _r(entry),
            "latest_price": _r(last),
            "latest_date": t.get("latest_date"),
            "current_stop": _r(stop),
            "peak_price": _r(peak),
            "prob": _r(t.get("prob"), 4),
            "unrealized_pct": _r(unreal),
            "day_change_pct": _r((last - prev) / prev * 100.0) if prev else None,
            "stop_distance_pct": _r((last - stop) / last * 100.0) if stop and last else None,
            "locked_pct": _r(locked),
            "risk_state": ("Profit locked" if locked is not None and locked > 0
                           else "Break-even stop" if locked is not None and abs(locked) < 0.5
                           else "At risk"),
            "drawdown_from_peak_pct": _r((last - peak) / peak * 100.0) if peak else None,
            "r_multiple": _r(r_multiple),
            "days_held": days,
        })
    positions.sort(key=lambda p: (p["unrealized_pct"] is None, -(p["unrealized_pct"] or 0)))
    return positions


def build_activity(scan_log, closed_trades, active_trades):
    all_runs = scan_log.get("runs", [])
    # Seeded runs were reconstructed from git and have no trigger counts, so they
    # would read as genuine zeroes in the funnel chart.
    runs = [r for r in all_runs if not r.get("seeded")]
    slim = []
    for r in runs:
        slim.append({
            "date": r.get("date"),
            "scanned": r.get("scanned"),
            "errors": r.get("errors"),
            "triggers": r.get("triggers"),
            "signals_taken": r.get("signals_taken"),
            "exits": r.get("exits"),
            "open_positions": r.get("open_positions"),
        })

    total_triggers = sum((r.get("triggers") or 0) for r in runs)
    total_taken = sum((r.get("signals_taken") or 0) for r in runs)

    # Most recent rejected triggers, useful for seeing the ML gate at work
    rejected = []
    for r in reversed(runs):
        for tr in r.get("triggers_detail", []):
            if not tr.get("taken"):
                rejected.append({
                    "symbol": tr.get("symbol"),
                    "date": tr.get("signal_date") or r.get("date"),
                    "prob": _r(tr.get("prob"), 4),
                    "close_price": _r(tr.get("close_price")),
                })
        if len(rejected) >= 25:
            break

    return {
        "runs": slim[-120:],
        "total_runs": len(all_runs),
        "logged_runs": len(runs),
        "first_run": all_runs[0].get("date") if all_runs else None,
        "total_triggers": total_triggers,
        "total_taken": total_taken,
        "gate_pass_rate": _r(total_taken / total_triggers * 100.0, 1) if total_triggers else None,
        "recent_rejected": rejected[:25],
        "last_run": all_runs[-1].get("date") if all_runs else None,
        "last_run_utc": runs[-1].get("run_at_utc") if runs else None,
    }


def build(workspace=None):
    workspace = workspace or os.path.dirname(os.path.abspath(__file__))

    active_data = _load(os.path.join(workspace, "active_trades.json"), {"trades": []})
    history_data = _load(os.path.join(workspace, "trade_history.json"), {"trades": []})
    scan_log = _load(os.path.join(workspace, "scan_log.json"), {"runs": []})
    backtest = _load(os.path.join(workspace, "backtest_history.json"), None)
    watchlist = _load(os.path.join(workspace, "watchlist.json"), {"stocks": []})

    active_trades = active_data.get("trades", [])
    closed_trades = history_data.get("trades", [])

    live = compute_stats(closed_trades, "Live (forward-tested)")
    positions = build_open_positions(active_trades)
    activity = build_activity(scan_log, closed_trades, active_trades)

    # Trades deliberately kept out of the statistics, shown for transparency.
    integrity = [{
        "symbol": t.get("symbol"),
        "entry_date": t.get("entry_date"),
        "exit_date": t.get("exit_date"),
        "pnl_pct": _r(t.get("pnl_pct")),
        "reason": t.get("reason"),
        "detail": t.get("exclude_reason") or "",
    } for t in closed_trades if t.get("excluded")]
    for run in scan_log.get("runs", []):
        for ca in run.get("corporate_actions", []):
            integrity.append({
                "symbol": ca.get("symbol"),
                "entry_date": None,
                "exit_date": run.get("date"),
                "pnl_pct": None,
                "reason": "Corporate action",
                "detail": ca.get("note", ""),
            })

    unrealized = sum((p["unrealized_pct"] or 0) for p in positions)
    universe = watchlist.get("stocks", [])
    batch_counts = Counter(s.get("batch", "Unclassified") for s in universe)

    payload = {
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
                            .strftime('%Y-%m-%dT%H:%M:%SZ'),
        "as_of": active_data.get("last_updated") or history_data.get("last_updated"),
        "config": {
            "prob_threshold": PROB_THRESHOLD,
            "cost_pct": COST_PCT,
            "allocation_pct": ALLOCATION * 100,
            "entry_rule": "BBW squeeze (<10% within 5 days) + volume spike >1.5x avg20 "
                          "+ RSI(14) 55-70 rising >8 + 20-day high breakout",
            "exit_rule": "Chandelier trailing stop: (H+L)/2 - 3 x ATR(10), evaluated on daily close, "
                         "with gap-down protection at the open",
            "model": "XGBoost classifier, 12 features, asymmetric class weighting",
            "schedule": "Weekdays 10:45 UTC (16:15 IST) via GitHub Actions",
        },
        "universe": {
            "total": len(universe),
            "updated": watchlist.get("watchlist_updated"),
            "batches": [{"batch": k, "count": v} for k, v in sorted(batch_counts.items())],
        },
        "headline": {
            "open_positions": len(positions),
            "closed_trades": live["count"],
            "win_rate": live["win_rate"],
            "expectancy": live["expectancy"],
            "profit_factor": live["profit_factor"],
            "realized_return_pct": live["total_return_pct"],
            "unrealized_pct": _r(unrealized),
            "max_drawdown_pct": live["max_drawdown_pct"],
            "best": live["best"],
            "worst": live["worst"],
            "avg_hold_days": live["avg_hold_days"],
        },
        "open": positions,
        "live": live,
        "activity": activity,
        "integrity": integrity,
        "backtest": None,
    }

    if backtest and backtest.get("trades"):
        bt = compute_stats(backtest["trades"], backtest.get("label", "Historical backtest"))
        bt["meta"] = backtest.get("meta", {})
        payload["backtest"] = bt

    out_dir = os.path.join(workspace, "docs", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "dashboard_data.json")
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=1)

    print(f"Wrote {out_path}")
    print(f"  open positions : {len(positions)}")
    print(f"  closed trades  : {live['count']}")
    print(f"  backtest trades: {payload['backtest']['count'] if payload['backtest'] else 0}")
    return payload


if __name__ == "__main__":
    build()
