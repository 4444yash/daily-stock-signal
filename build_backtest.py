"""
Generates backtest_history.json: a signal-level historical simulation of the exact
live strategy (same indicators, same model, same 0.65 gate, same trailing stop)
over the current watchlist.

This is the "past performance" baseline shown on the dashboard alongside the
forward-tested live ledger. Run it locally when the watchlist changes:

    python build_backtest.py --years 3

It intentionally reuses calculate_indicators() from daily_scanner.py so the
backtest can never drift from live logic.
"""

import argparse
import datetime
import json
import os
import sys

import numpy as np
import pandas as pd
import xgboost as xgb
import yfinance as yf

from daily_scanner import calculate_indicators, clean_multiindex, drop_incomplete_bars

FEATURE_COLS = [
    "bbw_width_pct", "days_in_squeeze", "volume_multiple", "close_high_ratio",
    "rsi_absolute", "rsi_delta", "atr_pct", "distance_from_50sma",
    "nifty_trend", "nifty_distance_from_50sma", "relative_strength_125", "prior_runup_90",
]
PROB_THRESHOLD = 0.65
WARMUP_BARS = 150          # need 125-bar relative strength + 50 SMA before trusting features


def download(tickers, period):
    """Chunked download, returns {ticker: cleaned dataframe}."""
    out = {}
    chunk_size = 15
    for i in range(0, len(tickers), chunk_size):
        chunk = tickers[i:i + chunk_size]
        print(f"  downloading {i + 1}-{i + len(chunk)} of {len(tickers)}...")
        try:
            raw = yf.download(chunk, period=period, progress=False,
                              group_by='ticker', auto_adjust=False, threads=True)
        except Exception as e:
            print(f"    chunk failed: {e}")
            continue
        for tkr in chunk:
            try:
                if isinstance(raw.columns, pd.MultiIndex):
                    if tkr not in raw.columns.get_level_values(0):
                        continue
                    df = raw[tkr].copy()
                else:
                    df = raw.copy()
                df = df.reset_index()
                df.columns = [str(c).lower() for c in df.columns]
                if 'close' not in df.columns:
                    continue
                df = drop_incomplete_bars(df)
                if df.empty:
                    continue
                out[tkr] = df
            except Exception as e:
                print(f"    {tkr}: {e}")
    return out


def prepare(df):
    df = df.copy()
    df['date_parsed'] = pd.to_datetime(df['date']).dt.date
    df = df.sort_values('date_parsed').reset_index(drop=True)
    return df


def compute_features(row, idx, closes, sma50, n_close, n_sma50, ni):
    """
    The 12 model features for one signal bar.

    Shared by the backtest and by build_training_data.py so the two can never
    drift apart. Returns None if any feature is NaN.
    """
    vol_avg = row['volume_avg20']
    denom = row['high'] - row['low']
    features = {
        "bbw_width_pct": float(row['bbw']),
        "days_in_squeeze": int(row['days_in_squeeze']),
        "volume_multiple": float(row['volume'] / vol_avg) if vol_avg > 0 else 1.0,
        "close_high_ratio": float((row['close'] - row['low']) / denom) if denom > 0 else 1.0,
        "rsi_absolute": float(row['rsi_14']),
        "rsi_delta": float(row['rsi_diff']),
        "atr_pct": float(row['atr14'] / row['close'] * 100) if row['close'] > 0 else 0.0,
        "distance_from_50sma": float((row['close'] - sma50[idx]) / sma50[idx]) if sma50[idx] > 0 else 0.0,
        "nifty_trend": 1 if n_close[ni] > n_sma50[ni] else 0,
        "nifty_distance_from_50sma": float((n_close[ni] - n_sma50[ni]) / n_sma50[ni]) if n_sma50[ni] > 0 else 0.0,
        "relative_strength_125": float((closes[idx] / closes[idx - 125]) /
                                       (n_close[ni] / n_close[ni - 125])),
        "prior_runup_90": float((closes[idx] - closes[idx - 90]) / closes[idx - 90] * 100),
    }
    if any(pd.isna(v) for v in features.values()):
        return None
    return features


def simulate(df, idx, closes, opens, highs, lows, atrs10):
    """Enter at next open, trail with (H+L)/2 - 3*ATR10, exit on close breach or gap-down."""
    entry_idx = idx + 1
    if entry_idx >= len(opens):
        return None

    entry_price = float(opens[entry_idx])
    atr0 = atrs10[idx]
    if pd.isna(atr0):
        atr0 = entry_price * 0.03
    stop = (highs[idx] + lows[idx]) / 2.0 - 3.0 * float(atr0)

    exit_price, exit_idx, reason = None, None, None
    peak = entry_price

    for j in range(entry_idx, len(opens)):
        o_j, h_j, l_j, c_j = float(opens[j]), float(highs[j]), float(lows[j]), float(closes[j])
        peak = max(peak, h_j)
        atr_j = atrs10[j] if not pd.isna(atrs10[j]) else atr0

        if o_j <= stop:
            exit_price, exit_idx, reason = o_j, j, "SL Hit (Open Gap)"
            break
        stop = max(stop, (h_j + l_j) / 2.0 - 3.0 * float(atr_j))
        if c_j <= stop:
            exit_price, exit_idx, reason = c_j, j, "SL/TSL Hit"
            break

    open_at_end = exit_price is None
    if open_at_end:
        exit_price, exit_idx, reason = float(closes[-1]), len(closes) - 1, "Still open at data end"

    return {
        "entry_idx": entry_idx,
        "entry_price": entry_price,
        "exit_idx": exit_idx,
        "exit_price": exit_price,
        "peak_price": peak,
        "reason": reason,
        "open_at_end": open_at_end,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=3, help="history window in years")
    ap.add_argument("--limit", type=int, default=0, help="only scan first N symbols (debug)")
    args = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    workspace = os.path.dirname(os.path.abspath(__file__))
    period = f"{args.years}y"

    with open(os.path.join(workspace, "watchlist.json"), 'r') as f:
        stocks = json.load(f).get("stocks", [])
    if args.limit:
        stocks = stocks[:args.limit]
    print(f"Backtesting {len(stocks)} symbols over {period}.")

    model_path = os.path.join(workspace, "results", "xgboost_live_model_asymmetric.json")
    if not os.path.exists(model_path):
        model_path = os.path.join(workspace, "xgboost_live_model_asymmetric.json")
    model = xgb.XGBClassifier()
    model.load_model(model_path)

    print("Downloading Nifty 50...")
    nifty = drop_incomplete_bars(clean_multiindex(
        yf.download("^NSEI", period=period, progress=False)))
    nifty = prepare(nifty)
    nifty['sma50'] = nifty['close'].rolling(window=50).mean()
    n_idx = {d: i for i, d in enumerate(nifty['date_parsed'])}
    n_close = nifty['close'].values
    n_sma50 = nifty['sma50'].values

    print("Downloading stock history...")
    frames = download([s["ticker"] for s in stocks], period)
    print(f"Got usable data for {len(frames)} / {len(stocks)} symbols.")

    trades = []
    feature_rows = []
    for s in stocks:
        df = frames.get(s["ticker"])
        if df is None or len(df) < WARMUP_BARS + 5:
            continue
        try:
            df = prepare(df)
            df = calculate_indicators(df)
        except Exception as e:
            print(f"  {s['symbol']}: indicator error {e}")
            continue

        closes = df['close'].values
        opens = df['open'].values
        highs = df['high'].values
        lows = df['low'].values
        atrs10 = df['atr10'].values
        atrs14 = df['atr14'].values
        sma50 = df['sma50'].values

        sig_idx = df.index[(df['triple_signal'] == 1) & (df.index >= WARMUP_BARS)]
        for idx in sig_idx:
            row = df.loc[idx]
            sig_date = row['date_parsed']
            ni = n_idx.get(sig_date)
            if ni is None or ni < 125:
                continue

            features = compute_features(row, idx, closes, sma50, n_close, n_sma50, ni)
            if features is None:
                continue

            prob = float(model.predict_proba(pd.DataFrame([features])[FEATURE_COLS])[:, 1][0])
            feature_rows.append({"symbol": s["symbol"], "date": str(sig_date), "prob": prob,
                                 "taken": prob >= PROB_THRESHOLD})
            if prob < PROB_THRESHOLD:
                continue

            res = simulate(df, idx, closes, opens, highs, lows, atrs10)
            if res is None or res["open_at_end"]:
                continue

            entry_date = df.loc[res["entry_idx"], 'date_parsed']
            exit_date = df.loc[res["exit_idx"], 'date_parsed']
            pnl = (res["exit_price"] - res["entry_price"]) / res["entry_price"] * 100.0

            trades.append({
                "symbol": s["symbol"],
                "batch": s.get("batch", ""),
                "signal_date": str(sig_date),
                "entry_date": str(entry_date),
                "exit_date": str(exit_date),
                "entry_price": round(res["entry_price"], 2),
                "exit_price": round(res["exit_price"], 2),
                "peak_price": round(res["peak_price"], 2),
                "pnl_pct": round(pnl, 2),
                "hold_days": (exit_date - entry_date).days,
                "prob": round(prob, 4),
                "reason": res["reason"],
            })

        print(f"  {s['symbol']}: {sum(1 for t in trades if t['symbol'] == s['symbol'])} trades")

    trades.sort(key=lambda t: (t["exit_date"], t["symbol"]))
    total_triggers = len(feature_rows)
    taken = sum(1 for f in feature_rows if f["taken"])

    payload = {
        "label": f"Historical backtest ({args.years}y, signal-level)",
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc)
                            .strftime('%Y-%m-%dT%H:%M:%SZ'),
        "meta": {
            "years": args.years,
            "symbols_scanned": len(frames),
            "technical_triggers": total_triggers,
            "passed_model_gate": taken,
            "gate_pass_rate": round(taken / total_triggers * 100, 1) if total_triggers else None,
            "prob_threshold": PROB_THRESHOLD,
            "note": "Signal-level results: every gated signal is treated as one equally sized "
                    "trade. Not capped by portfolio slots, and survivorship-biased because it "
                    "uses today's watchlist.",
        },
        "trades": trades,
    }

    out = os.path.join(workspace, "backtest_history.json")
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=1)
    print(f"\nWrote {out}: {len(trades)} closed trades "
          f"from {total_triggers} triggers ({taken} passed the gate).")


if __name__ == "__main__":
    main()
