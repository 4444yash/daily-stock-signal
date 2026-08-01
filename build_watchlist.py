"""
Turns a Screener.in CSV export into watchlist.json, with provenance and a diff.

The fundamental screen itself cannot be reproduced from free data: promoter
holding is not exposed by yfinance, and its debt-to-equity figures come back in
inconsistent units across symbols (real examples: GARUDA 2.649, PRECWIRE 39.377).
Guessing at those would silently produce a wrong watchlist, which is worse than
doing the screen by hand. So the screen stays manual and everything downstream
is automated. See screener/README.md.

    python build_watchlist.py                    # newest export in screener/exports/
    python build_watchlist.py --export path.csv
    python build_watchlist.py --audit            # re-check computable criteria only
    python build_watchlist.py --dry-run

A stock leaving the screen never closes a trade. Exits are the trailing stop's
job. Open positions in dropped names are flagged for a human decision.
"""

import argparse
import datetime
import glob
import json
import os
import shutil
import sys

import pandas as pd
import yfinance as yf

SYMBOL_COLUMNS = ['nse code', 'symbol', 'ticker', 'nsecode', 'code', 'name']
MIN_CAP_CR, MAX_CAP_CR = 1000, 15000
MIN_GROWTH_3Y_PCT = 15
N_BATCHES = 4


def newest_export(workspace):
    files = sorted(glob.glob(os.path.join(workspace, 'screener', 'exports', '*.csv')))
    return files[-1] if files else None


def read_symbols(path):
    df = pd.read_csv(path)
    lower = {c.strip().lower(): c for c in df.columns}
    for want in SYMBOL_COLUMNS:
        if want in lower:
            col = lower[want]
            names = [str(v).strip().upper() for v in df[col].dropna()]
            names = [n for n in names if n and n not in ('NAN', 'S.NO.')]
            if names:
                return names, col
    raise SystemExit(
        f'Could not find a symbol column in {os.path.basename(path)}.\n'
        f'  columns present: {list(df.columns)}\n'
        f'  looked for     : {SYMBOL_COLUMNS}')


def resolve_ticker(symbol):
    """Return a Yahoo ticker that actually returns price history, or None."""
    for suffix in ('.NS', '.BO'):
        tkr = f'{symbol}{suffix}'
        try:
            h = yf.download(tkr, period='3mo', progress=False, auto_adjust=False)
            if h is not None and len(h.dropna(how='all')) >= 20:
                return tkr
        except Exception:
            continue
    return None


def assign_batches(symbols, previous):
    """Keep a retained stock in the batch it was already reported under."""
    prev = {s['symbol']: s.get('batch') for s in previous}
    out, fresh = {}, []
    for s in symbols:
        if prev.get(s):
            out[s] = prev[s]
        else:
            fresh.append(s)
    counts = {f'Batch {i}': 0 for i in range(1, N_BATCHES + 1)}
    for b in out.values():
        if b in counts:
            counts[b] += 1
    for s in fresh:
        target = min(counts, key=lambda k: counts[k])
        out[s] = target
        counts[target] += 1
    return out


def fundamentals(symbol, ticker):
    """Only the criteria that are reliably computable. None where unavailable."""
    res = {'market_cap_cr': None, 'sales_growth_3y_pct': None, 'profit_growth_3y_pct': None}
    try:
        t = yf.Ticker(ticker)
        cap = (t.info or {}).get('marketCap')
        if cap:
            res['market_cap_cr'] = round(cap / 1e7, 1)
    except Exception:
        pass
    try:
        fin = yf.Ticker(ticker).financials
        for key, label in (('Total Revenue', 'sales_growth_3y_pct'),
                           ('Net Income', 'profit_growth_3y_pct')):
            if fin is None or key not in fin.index:
                continue
            vals = [v for v in fin.loc[key].values if v == v]
            if len(vals) >= 4 and vals[3] and vals[3] > 0:
                cagr = ((vals[0] / vals[3]) ** (1 / 3) - 1) * 100
                res[label] = round(cagr, 1)
    except Exception:
        pass
    return res


def audit(workspace):
    """Between quarters: flag holdings that drifted out of the computable criteria."""
    with open(os.path.join(workspace, 'watchlist.json'), encoding='utf-8') as f:
        wl = json.load(f)
    active_path = os.path.join(workspace, 'active_trades.json')
    held = set()
    if os.path.exists(active_path):
        with open(active_path, encoding='utf-8') as f:
            held = {t['symbol'] for t in json.load(f).get('trades', [])}

    print(f'auditing {len(wl["stocks"])} symbols against computable criteria only')
    print('(promoter holding and debt/equity cannot be verified this way)\n')
    flagged = []
    for i, s in enumerate(wl['stocks'], 1):
        f_ = fundamentals(s['symbol'], s['ticker'])
        reasons = []
        cap = f_['market_cap_cr']
        if cap is not None and not (MIN_CAP_CR <= cap <= MAX_CAP_CR):
            reasons.append(f'market cap {cap:,.0f}cr outside {MIN_CAP_CR}-{MAX_CAP_CR}')
        for key, label in (('sales_growth_3y_pct', 'sales'), ('profit_growth_3y_pct', 'profit')):
            v = f_[key]
            if v is not None and v < MIN_GROWTH_3Y_PCT:
                reasons.append(f'3y {label} growth {v:.1f}% < {MIN_GROWTH_3Y_PCT}%')
        if reasons:
            flagged.append({'symbol': s['symbol'], 'held': s['symbol'] in held,
                            'reasons': reasons, **f_})
            mark = '  [HELD]' if s['symbol'] in held else ''
            print(f'  {s["symbol"]:<12} {"; ".join(reasons)}{mark}')
        if i % 25 == 0:
            print(f'  ... {i}/{len(wl["stocks"])}')

    out = os.path.join(workspace, 'screener', 'audit_latest.json')
    with open(out, 'w', encoding='utf-8') as f:
        json.dump({'audited_at_utc': datetime.datetime.now(datetime.timezone.utc)
                                     .strftime('%Y-%m-%dT%H:%M:%SZ'),
                   'checked': len(wl['stocks']), 'flagged': flagged,
                   'note': 'Computable criteria only. Not a full screen re-run.'},
                  f, indent=2)
    print(f'\n{len(flagged)} of {len(wl["stocks"])} flagged. Written to {out}')
    print('Advisory only: nothing is removed automatically.')
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--export', default=None, help='screener CSV; default = newest in screener/exports/')
    ap.add_argument('--audit', action='store_true', help='re-check computable criteria, change nothing')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--skip-validation', action='store_true',
                    help='trust the symbols without a Yahoo price check (fast, unsafe)')
    args = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    workspace = os.path.dirname(os.path.abspath(__file__))
    if args.audit:
        return audit(workspace)

    export = args.export or newest_export(workspace)
    if not export:
        print('No screener export found. Save one as screener/exports/screen_YYYY-MM-DD.csv')
        print('See screener/README.md')
        return 1
    if not os.path.isabs(export):
        export = os.path.join(workspace, export)

    symbols, col = read_symbols(export)
    symbols = sorted(set(symbols))
    print(f'export : {os.path.relpath(export, workspace)}')
    print(f'column : "{col}"  ->  {len(symbols)} unique symbols\n')

    wl_path = os.path.join(workspace, 'watchlist.json')
    with open(wl_path, encoding='utf-8') as f:
        old = json.load(f)
    old_stocks = old.get('stocks', [])
    old_syms = {s['symbol'] for s in old_stocks}
    old_tickers = {s['symbol']: s['ticker'] for s in old_stocks}

    resolved, unresolved = [], []
    if args.skip_validation:
        for s in symbols:
            resolved.append({'symbol': s, 'ticker': old_tickers.get(s, f'{s}.NS')})
    else:
        print('validating tickers against Yahoo (retained names reuse their known ticker)...')
        for i, s in enumerate(symbols, 1):
            tkr = old_tickers.get(s) or resolve_ticker(s)
            if tkr:
                resolved.append({'symbol': s, 'ticker': tkr})
            else:
                unresolved.append(s)
                print(f'  UNRESOLVED {s} - no price history on .NS or .BO, excluded')
            if i % 25 == 0:
                print(f'  ... {i}/{len(symbols)}')

    batches = assign_batches([r['symbol'] for r in resolved], old_stocks)
    new_stocks = [{'symbol': r['symbol'], 'ticker': r['ticker'], 'batch': batches[r['symbol']]}
                  for r in sorted(resolved, key=lambda r: (batches[r['symbol']], r['symbol']))]
    new_syms = {s['symbol'] for s in new_stocks}

    added = sorted(new_syms - old_syms)
    removed = sorted(old_syms - new_syms)
    retained = sorted(new_syms & old_syms)

    active_path = os.path.join(workspace, 'active_trades.json')
    held = set()
    if os.path.exists(active_path):
        with open(active_path, encoding='utf-8') as f:
            held = {t['symbol'] for t in json.load(f).get('trades', [])}
    held_removed = sorted(held & set(removed))

    print(f'\n{"="*58}\n  WATCHLIST DIFF\n{"="*58}')
    print(f'  previous : {len(old_syms)}')
    print(f'  new      : {len(new_syms)}')
    print(f'  added    : {len(added)}')
    print(f'  removed  : {len(removed)}')
    print(f'  retained : {len(retained)}')
    if unresolved:
        print(f'  excluded (no price data): {len(unresolved)}  {unresolved}')
    if added:
        print(f'\n  ADDED  : {", ".join(added)}')
    if removed:
        print(f'\n  REMOVED: {", ".join(removed)}')
    if held_removed:
        print(f'\n  *** WARNING: open positions in removed names: {", ".join(held_removed)}')
        print('      These trades are NOT closed. The trailing stop still governs the')
        print('      exit. Decide manually whether to exit early.')
    print('='*58)

    run_date = datetime.date.today().isoformat()
    payload = {
        'watchlist_updated': run_date,
        'source': {
            'method': 'Screener.in fundamental screen, exported manually',
            'query_file': 'screener/query.txt',
            'export_file': os.path.relpath(export, workspace).replace('\\', '/'),
            'symbol_column': col,
            'generated_by': 'build_watchlist.py',
        },
        'criteria_summary': old.get('criteria_summary', {}),
        'refresh': {'cadence': 'quarterly', 'last_run': run_date,
                    'process': 'screener/README.md'},
        'counts': {
            'total': len(new_stocks),
            'by_batch': {b: sum(1 for s in new_stocks if s['batch'] == b)
                         for b in sorted({s['batch'] for s in new_stocks})},
        },
        'diff_vs_previous': {
            'previous_updated': old.get('watchlist_updated'),
            'added': added, 'removed': removed,
            'retained_count': len(retained),
            'excluded_no_price_data': unresolved,
            'open_positions_in_removed': held_removed,
        },
        'stocks': new_stocks,
    }

    if args.dry_run:
        print('\n(dry run, nothing written)')
        return 0

    hist_dir = os.path.join(workspace, 'watchlist_history')
    os.makedirs(hist_dir, exist_ok=True)
    archive = os.path.join(hist_dir, f'watchlist_{old.get("watchlist_updated", "unknown")}.json')
    if not os.path.exists(archive):
        shutil.copy2(wl_path, archive)
        print(f'\narchived previous list -> {os.path.relpath(archive, workspace)}')

    with open(wl_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)
    print(f'wrote watchlist.json with {len(new_stocks)} symbols')

    summary = os.path.join(workspace, 'screener', 'last_refresh.md')
    with open(summary, 'w', encoding='utf-8') as f:
        f.write(f'# Watchlist refresh {run_date}\n\n')
        f.write(f'Source: `{payload["source"]["export_file"]}`\n\n')
        f.write(f'| | count |\n|---|---:|\n')
        f.write(f'| previous | {len(old_syms)} |\n| new | {len(new_syms)} |\n')
        f.write(f'| added | {len(added)} |\n| removed | {len(removed)} |\n')
        f.write(f'| retained | {len(retained)} |\n\n')
        if added:
            f.write(f'**Added:** {", ".join(added)}\n\n')
        if removed:
            f.write(f'**Removed:** {", ".join(removed)}\n\n')
        if unresolved:
            f.write(f'**Excluded, no price data:** {", ".join(unresolved)}\n\n')
        if held_removed:
            f.write(f'> **Open positions in removed names:** {", ".join(held_removed)}. '
                    f'Not closed automatically; the trailing stop still governs the exit.\n')
    print(f'wrote {os.path.relpath(summary, workspace)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
