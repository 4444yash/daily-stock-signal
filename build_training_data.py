"""
Rebuilds the model training dataset from yfinance, reproducibly and in the cloud.

Replaces extract_ml_features.py, which read pre-downloaded CSV folders that are
not in the repository. That made retraining impossible anywhere except one
laptop, so it could never be automated.

Every label is produced by replaying the live strategy: the same
calculate_indicators() the scanner uses, the same triple_signal, the same
Chandelier trailing stop. Signals still open at the end of the data are dropped
rather than guessed at, and each row carries its exit date so the training step
can apply its 60-day resolution buffer.

    python build_training_data.py --years 10 --source watchlist
    python build_training_data.py --years 10 --symbols-file screener/universe.txt
    python build_training_data.py --years 10 --source history --out results/pool.csv

Output schema matches results/xgboost_training_data.csv, plus provenance columns.
"""

import argparse
import datetime
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import yfinance as yf

from daily_scanner import calculate_indicators, clean_multiindex, drop_incomplete_bars
from build_backtest import FEATURE_COLS, WARMUP_BARS, compute_features, download, prepare, simulate

TARGET_PNL_PCT = 25.0   # a "win" is a 25%+ move, matching the live model's target


def resolve_universe(args, workspace):
    """Returns a list of {symbol, ticker, batch} dicts."""
    if args.symbols_file:
        path = args.symbols_file if os.path.isabs(args.symbols_file) \
            else os.path.join(workspace, args.symbols_file)
        with open(path, encoding='utf-8') as f:
            names = [l.strip() for l in f if l.strip() and not l.startswith('#')]
        out = []
        for n in names:
            if ',' in n:                       # "SYMBOL,TICKER" form
                sym, tkr = [p.strip() for p in n.split(',')[:2]]
            elif '.' in n:                     # already a ticker
                sym, tkr = n.split('.')[0], n
            else:
                sym, tkr = n, f'{n}.NS'
            out.append({'symbol': sym, 'ticker': tkr, 'batch': 'file'})
        return out, f'file:{args.symbols_file}'

    if args.source == 'history':
        # Union of every archived watchlist. Note this is a union, not
        # point-in-time membership, so it still carries survivorship bias -
        # just less than using only today's list.
        seen, out = {}, []
        pattern = os.path.join(workspace, 'watchlist_history', 'watchlist_*.json')
        files = sorted(glob.glob(pattern))
        for fp in files:
            with open(fp, encoding='utf-8') as f:
                for s in json.load(f).get('stocks', []):
                    if s['symbol'] not in seen:
                        seen[s['symbol']] = True
                        out.append(s)
        with open(os.path.join(workspace, 'watchlist.json'), encoding='utf-8') as f:
            for s in json.load(f).get('stocks', []):
                if s['symbol'] not in seen:
                    seen[s['symbol']] = True
                    out.append(s)
        return out, f'history:{len(files)} snapshots + current'

    with open(os.path.join(workspace, 'watchlist.json'), encoding='utf-8') as f:
        wl = json.load(f)
    return wl.get('stocks', []), f"watchlist.json@{wl.get('watchlist_updated')}"


def median_turnover_cr(df, idx):
    """Median 20-day traded value in Rs crore, as of the signal bar.

    Point-in-time by construction. Not a model feature - it is metadata used to
    filter a pool down to screener-profile names, and to sanity-check whether a
    fill was realistic.
    """
    lo = max(0, idx - 19)
    tv = (df['close'].values[lo:idx + 1] * df['volume'].values[lo:idx + 1])
    if not len(tv):
        return None
    return float(np.median(tv) / 1e7)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--years', type=int, default=10, help='history window')
    ap.add_argument('--source', choices=['watchlist', 'history'], default='watchlist')
    ap.add_argument('--symbols-file', default=None,
                    help='newline-separated symbols or tickers; overrides --source')
    ap.add_argument('--out', default='results/training_data_generated.csv')
    ap.add_argument('--batch-label', default=None,
                    help='override source_batch for every row')
    ap.add_argument('--limit', type=int, default=0, help='first N symbols (debug)')
    args = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    workspace = os.path.dirname(os.path.abspath(__file__))
    period = f'{args.years}y'

    universe, provenance = resolve_universe(args, workspace)
    if args.limit:
        universe = universe[:args.limit]
    print(f'universe: {len(universe)} symbols  ({provenance})')
    print(f'period  : {period}')

    print('\ndownloading Nifty 50 benchmark...')
    nifty = prepare(drop_incomplete_bars(clean_multiindex(
        yf.download('^NSEI', period=period, progress=False, auto_adjust=False))))
    nifty['sma50'] = nifty['close'].rolling(window=50).mean()
    n_idx = {d: i for i, d in enumerate(nifty['date_parsed'])}
    n_close = nifty['close'].values
    n_sma50 = nifty['sma50'].values
    print(f'  {len(nifty)} sessions, {nifty.date_parsed.iloc[0]} -> {nifty.date_parsed.iloc[-1]}')

    print('\ndownloading stock history...')
    frames = download([s['ticker'] for s in universe], period)
    print(f'usable data for {len(frames)} / {len(universe)} symbols')

    rows = []
    stats = {'signals': 0, 'unresolved': 0, 'nan_features': 0, 'no_benchmark': 0}

    print('\nextracting signals...')
    for n, s in enumerate(universe, 1):
        df = frames.get(s['ticker'])
        if df is None or len(df) < WARMUP_BARS + 5:
            continue
        try:
            df = calculate_indicators(prepare(df))
        except Exception as e:
            print(f'  {s["symbol"]}: indicator error {e}')
            continue

        closes, opens = df['close'].values, df['open'].values
        highs, lows = df['high'].values, df['low'].values
        atrs10, sma50 = df['atr10'].values, df['sma50'].values

        found = 0
        for idx in df.index[(df['triple_signal'] == 1) & (df.index >= WARMUP_BARS)]:
            row = df.loc[idx]
            sig_date = row['date_parsed']
            ni = n_idx.get(sig_date)
            if ni is None or ni < 125:
                stats['no_benchmark'] += 1
                continue

            stats['signals'] += 1
            features = compute_features(row, idx, closes, sma50, n_close, n_sma50, ni)
            if features is None:
                stats['nan_features'] += 1
                continue

            res = simulate(df, idx, closes, opens, highs, lows, atrs10)
            if res is None or res['open_at_end']:
                # No settled outcome, so no label. Never guess at one.
                stats['unresolved'] += 1
                continue

            pnl = (res['exit_price'] - res['entry_price']) / res['entry_price'] * 100.0
            entry_date = df.loc[res['entry_idx'], 'date_parsed']
            exit_date = df.loc[res['exit_idx'], 'date_parsed']

            rows.append({
                **features,
                'symbol': s['symbol'],
                'signal_date': str(sig_date),
                'target': int(pnl >= TARGET_PNL_PCT),
                'trade_pnl': round(pnl, 4),
                'source_batch': args.batch_label or s.get('batch', ''),
                # provenance / analysis columns, not model features
                'entry_date': str(entry_date),
                'exit_date': str(exit_date),
                'entry_price': round(res['entry_price'], 2),
                'exit_price': round(res['exit_price'], 2),
                'hold_days': (exit_date - entry_date).days,
                'exit_reason': res['reason'],
                'median_turnover_20d_cr': round(median_turnover_cr(df, idx) or 0, 2),
            })
            found += 1

        if found:
            print(f'  [{n}/{len(universe)}] {s["symbol"]}: {found} labelled trades')

    if not rows:
        print('\nNo labelled trades produced. Nothing written.')
        return

    out = pd.DataFrame(rows).sort_values(['signal_date', 'symbol']).reset_index(drop=True)
    ordered = FEATURE_COLS + ['symbol', 'signal_date', 'target', 'trade_pnl', 'source_batch',
                              'entry_date', 'exit_date', 'entry_price', 'exit_price',
                              'hold_days', 'exit_reason', 'median_turnover_20d_cr']
    out = out[ordered]

    out_path = args.out if os.path.isabs(args.out) else os.path.join(workspace, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    out.to_csv(out_path, index=False)

    meta_path = os.path.splitext(out_path)[0] + '_meta.json'
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump({
            'generated_at_utc': datetime.datetime.now(datetime.timezone.utc)
                                .strftime('%Y-%m-%dT%H:%M:%SZ'),
            'universe_source': provenance,
            'symbols_requested': len(universe),
            'symbols_with_data': len(frames),
            'period': period,
            'target_definition': f'trade_pnl >= {TARGET_PNL_PCT}',
            'rows': len(out),
            'signal_date_range': [out.signal_date.min(), out.signal_date.max()],
            'target_base_rate_pct': round(out.target.mean() * 100, 2),
            'diagnostics': stats,
            'note': 'Labels replay daily_scanner.calculate_indicators and the live '
                    'trailing stop. Unresolved signals are excluded, not imputed. '
                    'Apply the 60-day resolution buffer at training time using exit_date.',
        }, f, indent=2)

    print(f'\nwrote {out_path}')
    print(f'  rows                 : {len(out)}')
    print(f'  date range           : {out.signal_date.min()} -> {out.signal_date.max()}')
    print(f'  target base rate     : {out.target.mean() * 100:.1f}%')
    print(f'  mean / median pnl    : {out.trade_pnl.mean():+.2f}% / {out.trade_pnl.median():+.2f}%')
    print(f'  median turnover (cr) : {out.median_turnover_20d_cr.median():.2f}')
    print(f'  diagnostics          : {stats}')
    print(f'  metadata             : {meta_path}')


if __name__ == '__main__':
    main()
