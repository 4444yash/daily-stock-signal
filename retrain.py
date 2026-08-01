"""
Quarterly model retraining with an evidence report. Never promotes on its own.

Produces, into model_reports/:
    report.md            human-readable analysis of what changed and why
    model_card.json      provenance + metrics for the candidate
    candidate_model.json the trained candidate, NOT installed
    walkforward.csv      every out-of-sample prediction behind the numbers

Design decisions worth knowing:

* Every number is averaged over several random seeds. A single run is close to
  meaningless here: on 300 rows with 100 trees the seed alone moves mean gated
  P&L from +1.7% to +7.6%. The saved results/xgboost_oos_predictions_asymmetric.csv
  happens to be seed 42, at the top of that range.

* The 60-day resolution buffer from train_xgboost_asymmetric.py is preserved
  exactly. Training for a given month may only use signals that had resolved
  60 days before that month began.

* Metrics are reported on the SERVING population (the traded universe), not on
  everything. A model that ranks large caps well is irrelevant if you only
  trade small caps.

* The incumbent model instance cannot be scored fairly on historical
  out-of-sample data because it was trained on it. So the comparison is between
  configurations under an identical walk-forward, and the report says so.

    python retrain.py
    python retrain.py --data results/training_data_generated.csv --exclude-nifty50
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import warnings

import numpy as np
import pandas as pd
import xgboost as xgb

warnings.filterwarnings('ignore')

FEATURE_COLS = [
    "bbw_width_pct", "days_in_squeeze", "volume_multiple", "close_high_ratio",
    "rsi_absolute", "rsi_delta", "atr_pct", "distance_from_50sma",
    "nifty_trend", "nifty_distance_from_50sma", "relative_strength_125", "prior_runup_90",
]
BUFFER_DAYS = 60
WINDOW = 300
GATE = 0.65
SEEDS = [42, 7, 13, 99, 2024]
SWEEP = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]

# Promotion bar. Deliberately conservative: doing nothing keeps a working model.
MIN_LIFT = 1.10          # must beat the base rate by 10%
MIN_AVG_PNL = 0.0        # gated trades must be profitable on average
MIN_PF = 1.20
MIN_TAKEN = 15           # below this the result is noise


def hyperparams(seed, scale_pos_weight):
    """Production hyperparameters from train_xgboost_asymmetric.py, unchanged."""
    return xgb.XGBClassifier(
        max_depth=3, learning_rate=0.05, n_estimators=100,
        subsample=0.8, colsample_bytree=0.8, random_state=seed,
        eval_metric='logloss', scale_pos_weight=scale_pos_weight,
    )


def sample_weights(frame):
    """Asymmetric weighting from production: emphasise big winners and all losers."""
    pnl = frame['trade_pnl'].values
    w = np.ones(len(frame))
    pos = pnl > 25.0
    w[pos] = 1.0 + np.clip(pnl[pos] / 15.0, 0, 3.0)
    neg = pnl < 0
    w[neg] = 1.0 + np.clip(np.abs(pnl[neg]) / 15.0, 0, 3.0)
    return w


def fit(train, seed):
    y = train['target']
    npos, nneg = int((y == 1).sum()), int((y == 0).sum())
    m = hyperparams(seed, nneg / npos if npos else 1.0)
    m.fit(train[FEATURE_COLS], y, sample_weight=sample_weights(train))
    return m


def walk_forward(df, train_pool, eval_mask, window, seed):
    """
    Monthly refit. For each month, train on signals resolved at least
    BUFFER_DAYS before the month began, then predict that month's trades.
    """
    rows = []
    for month in sorted(df.year_month.unique()):
        m_start = pd.Period(month, freq='M').start_time
        cutoff = m_start - pd.Timedelta(days=BUFFER_DAYS)

        train = train_pool[train_pool.signal_date < cutoff].tail(window)
        if len(train) < window or train.target.nunique() < 2:
            continue

        test = df[(df.year_month == month) & eval_mask]
        if not len(test):
            continue

        model = fit(train, seed)
        probs = model.predict_proba(test[FEATURE_COLS])[:, 1]
        for p, t in zip(probs, test.itertuples()):
            rows.append({'prob': float(p), 'pnl': t.trade_pnl, 'target': t.target,
                         'symbol': t.symbol, 'signal_date': t.signal_date,
                         'month': str(month), 'seed': seed})
    return pd.DataFrame(rows)


def metrics(res, gate=GATE):
    if not len(res):
        return None
    base = res.target.mean()
    g = res[res.prob >= gate]
    if not len(g):
        return {'n_oos': len(res), 'n_taken': 0, 'base_rate': base * 100,
                'rate': np.nan, 'lift': np.nan, 'avg': np.nan,
                'median': np.nan, 'pf': np.nan, 'win': np.nan, 'total': 0.0}
    wins = g[g.pnl > 0].pnl.sum()
    loss = abs(g[g.pnl <= 0].pnl.sum())
    return {
        'n_oos': len(res), 'n_taken': len(g), 'base_rate': base * 100,
        'rate': g.target.mean() * 100,
        'lift': g.target.mean() / base if base else np.nan,
        'avg': g.pnl.mean(), 'median': g.pnl.median(),
        'pf': wins / loss if loss else np.inf,
        'win': (g.pnl > 0).mean() * 100,
        'total': g.pnl.sum(),
    }


def mean_metrics(runs):
    runs = [r for r in runs if r]
    if not runs:
        return None, []
    keys = runs[0].keys()
    return {k: float(np.nanmean([r[k] for r in runs])) for k in keys}, runs


def fmt(v, suffix='', digits=2):
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return 'n/a'
    return f'{v:.{digits}f}{suffix}'


def git_sha():
    try:
        return subprocess.run(['git', 'rev-parse', '--short', 'HEAD'],
                              capture_output=True, text=True).stdout.strip() or 'unknown'
    except Exception:
        return 'unknown'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='results/xgboost_training_data.csv')
    ap.add_argument('--exclude-nifty50', action='store_true',
                    help='train only on non-Nifty-50 rows')
    ap.add_argument('--serving-universe', choices=['small', 'all'], default='small',
                    help='which population to score on')
    ap.add_argument('--window', type=int, default=WINDOW)
    ap.add_argument('--outdir', default='model_reports')
    args = ap.parse_args()

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    workspace = os.path.dirname(os.path.abspath(__file__))
    data_path = args.data if os.path.isabs(args.data) else os.path.join(workspace, args.data)
    outdir = args.outdir if os.path.isabs(args.outdir) else os.path.join(workspace, args.outdir)
    os.makedirs(outdir, exist_ok=True)

    df = pd.read_csv(data_path)
    df['signal_date'] = pd.to_datetime(df['signal_date'])
    df = df.sort_values('signal_date').reset_index(drop=True)
    df['year_month'] = df['signal_date'].dt.to_period('M')
    if 'source_batch' not in df.columns:
        df['source_batch'] = 'unknown'
    df['is_small'] = df.source_batch != 'Nifty 50'

    serving = df.is_small if args.serving_universe == 'small' else pd.Series(True, index=df.index)
    print(f'data    : {len(df)} rows  {df.signal_date.min().date()} -> {df.signal_date.max().date()}')
    print(f'serving : {int(serving.sum())} rows ({args.serving_universe})')

    # ---- configurations to compare -------------------------------------
    configs = [('incumbent config: all data', df, args.window)]
    if df.is_small.sum() >= args.window:
        configs.append(('candidate: small-cap only', df[df.is_small], args.window))
    else:
        print(f'\nNOTE small-cap rows ({int(df.is_small.sum())}) < window ({args.window}); '
              'small-cap-only config is not viable and is skipped.')
    if args.exclude_nifty50:
        configs = [c for c in configs if 'all data' not in c[0]] or configs

    print('\nwalk-forward, %d seeds, %d-day resolution buffer, gate %.2f' %
          (len(SEEDS), BUFFER_DAYS, GATE))
    print(f"\n{'config':<32}{'OOS':>6}{'taken':>7}{'lift':>7}{'avg':>9}{'PF':>7}{'win':>8}")
    print('-' * 76)

    results, all_preds = {}, []
    for label, pool, window in configs:
        runs, preds = [], []
        for s in SEEDS:
            r = walk_forward(df, pool, serving, window, s)
            if len(r):
                r['config'] = label
                preds.append(r)
                runs.append(metrics(r))
        agg, raw = mean_metrics(runs)
        if agg is None:
            print(f'{label:<32}  insufficient data')
            continue
        results[label] = {'agg': agg, 'runs': raw,
                          'preds': pd.concat(preds) if preds else pd.DataFrame()}
        all_preds.extend(preds)
        print(f"{label:<32}{agg['n_oos']:>6.0f}{agg['n_taken']:>7.1f}"
              f"{fmt(agg['lift']):>7}{fmt(agg['avg'], '%'):>9}"
              f"{fmt(agg['pf']):>7}{fmt(agg['win'], '%', 1):>8}")

    if not results:
        print('\nNo viable configuration. Nothing written.')
        return 1

    primary_label = configs[0][0]
    primary = results[primary_label]
    agg = primary['agg']

    # ---- threshold sweep ------------------------------------------------
    sweep = []
    pool_preds = primary['preds']
    for g in SWEEP:
        per_seed = [metrics(pool_preds[pool_preds.seed == s], g) for s in SEEDS]
        m, _ = mean_metrics(per_seed)
        if m:
            sweep.append({'gate': g, **m})

    # ---- recent decay ---------------------------------------------------
    recent_cut = df.signal_date.max() - pd.Timedelta(days=540)
    rp = pool_preds[pd.to_datetime(pool_preds.signal_date) >= recent_cut]
    recent, _ = mean_metrics([metrics(rp[rp.seed == s]) for s in SEEDS])

    # ---- candidate model + importance + drift ---------------------------
    train_pool = df[df.is_small] if args.exclude_nifty50 else df
    final_train = train_pool.tail(args.window)
    candidate = fit(final_train, SEEDS[0])
    cand_path = os.path.join(outdir, 'candidate_model.json')
    candidate.save_model(cand_path)

    imp = pd.Series(candidate.get_booster().get_score(importance_type='gain'))
    imp = (imp / imp.sum() * 100) if imp.sum() else imp
    dead = [f for f in FEATURE_COLS if imp.get(f, 0.0) < 0.01]

    recent_signals = df[df.signal_date >= recent_cut]
    drift = []
    for f in FEATURE_COLS:
        tr, rc = final_train[f], recent_signals[f]
        if not len(rc):
            continue
        sd = tr.std()
        drift.append({'feature': f, 'train_mean': tr.mean(), 'recent_mean': rc.mean(),
                      'shift_sd': (rc.mean() - tr.mean()) / sd if sd else 0.0,
                      'importance': imp.get(f, 0.0)})
    drift.sort(key=lambda d: -abs(d['shift_sd']))

    # ---- verdict --------------------------------------------------------
    checks = [
        ('lift above base rate', agg['lift'], MIN_LIFT, agg['lift'] >= MIN_LIFT),
        ('gated trades profitable', agg['avg'], MIN_AVG_PNL, agg['avg'] > MIN_AVG_PNL),
        ('profit factor', agg['pf'], MIN_PF, agg['pf'] >= MIN_PF),
        ('enough gated trades', agg['n_taken'], MIN_TAKEN, agg['n_taken'] >= MIN_TAKEN),
    ]
    passed = all(c[3] for c in checks)
    spread = [r['avg'] for r in primary['runs']]
    seed_stable = (min(spread) > 0) if spread else False
    verdict = 'PROMOTE' if (passed and seed_stable) else 'HOLD'

    # ---- write artifacts ------------------------------------------------
    if all_preds:
        pd.concat(all_preds).to_csv(os.path.join(outdir, 'walkforward.csv'), index=False)

    card = {
        'generated_at_utc': datetime.datetime.now(datetime.timezone.utc)
                            .strftime('%Y-%m-%dT%H:%M:%SZ'),
        'git_sha': git_sha(),
        'verdict': verdict,
        'training_data': os.path.relpath(data_path, workspace).replace('\\', '/'),
        'rows_total': int(len(df)),
        'rows_used_for_final_fit': int(len(final_train)),
        'window': args.window,
        'buffer_days': BUFFER_DAYS,
        'gate': GATE,
        'seeds': SEEDS,
        'excluded_nifty50': bool(args.exclude_nifty50),
        'serving_universe': args.serving_universe,
        'final_fit_date_range': [str(final_train.signal_date.min().date()),
                                 str(final_train.signal_date.max().date())],
        'target_definition': 'trade_pnl >= 25',
        'target_base_rate_pct': round(float(df.target.mean() * 100), 2),
        'walkforward': {k: {kk: (None if isinstance(vv, float) and (np.isnan(vv) or np.isinf(vv))
                                 else round(float(vv), 4))
                            for kk, vv in v['agg'].items()} for k, v in results.items()},
        'seed_spread_avg_pnl': [round(float(x), 3) for x in sorted(spread)],
        'threshold_sweep': [{k: (None if isinstance(v, float) and (np.isnan(v) or np.isinf(v))
                                 else round(float(v), 4)) for k, v in s.items()} for s in sweep],
        'feature_importance_pct': {f: round(float(imp.get(f, 0.0)), 2) for f in FEATURE_COLS},
        'dead_features': dead,
        'checks': [{'name': n, 'value': None if np.isnan(v) else round(float(v), 3),
                    'threshold': t, 'pass': bool(p)} for n, v, t, p in checks],
    }
    with open(os.path.join(outdir, 'model_card.json'), 'w', encoding='utf-8') as f:
        json.dump(card, f, indent=2)

    write_report(outdir, card, results, primary_label, agg, recent, sweep, drift, dead,
                 checks, spread, seed_stable, verdict, df, final_train)

    print(f'\nverdict: {verdict}')
    for n, v, t, p in checks:
        print(f'  [{"PASS" if p else "FAIL"}] {n}: {fmt(v)} (needs {t})')
    print(f'  [{"PASS" if seed_stable else "FAIL"}] every seed profitable: '
          f'{fmt(min(spread) if spread else float("nan"), "%")} worst')
    print(f'\nartifacts in {outdir}')
    return 0


def write_report(outdir, card, results, primary_label, agg, recent, sweep, drift, dead,
                 checks, spread, seed_stable, verdict, df, final_train):
    L = []
    A = L.append
    A(f'# Quarterly retrain report — {card["generated_at_utc"][:10]}')
    A('')
    A(f'**Verdict: {verdict}.** '
      + ('The candidate clears every promotion check.' if verdict == 'PROMOTE'
         else 'The candidate does not clear the promotion bar. The live model stays in place.'))
    A('')
    A('The live model is never replaced by this workflow. Promotion happens only when a '
      'human merges the pull request.')
    A('')
    A('## What was measured')
    A('')
    A(f'- Training data: `{card["training_data"]}`, {card["rows_total"]} labelled trades')
    A(f'- Final fit on the most recent {card["rows_used_for_final_fit"]} trades '
      f'({card["final_fit_date_range"][0]} to {card["final_fit_date_range"][1]})')
    A(f'- Target: `{card["target_definition"]}`, base rate {card["target_base_rate_pct"]}%')
    A(f'- Walk-forward monthly refit, {card["buffer_days"]}-day resolution buffer, '
      f'gate {card["gate"]}')
    A(f'- Averaged over {len(card["seeds"])} seeds; scored on the '
      f'`{card["serving_universe"]}` universe only')
    A('')
    A('## Configuration comparison')
    A('')
    A('| config | OOS trades | gated | lift | avg P&L | profit factor | win rate |')
    A('|---|---:|---:|---:|---:|---:|---:|')
    for label, r in results.items():
        a = r['agg']
        A(f'| {label} | {a["n_oos"]:.0f} | {a["n_taken"]:.1f} | {fmt(a["lift"])} | '
          f'{fmt(a["avg"], "%")} | {fmt(a["pf"])} | {fmt(a["win"], "%", 1)} |')
    A('')
    A('The incumbent *model instance* cannot be scored fairly against historical '
      'out-of-sample data, because it was trained on those trades. So this table compares '
      '**configurations** under an identical walk-forward, not one saved model against another.')
    A('')
    A('## Seed sensitivity')
    A('')
    A(f'Average gated P&L across seeds: '
      f'`{", ".join(f"{x:+.2f}%" for x in sorted(spread))}`')
    A('')
    A(f'Worst seed {"is profitable" if seed_stable else "**loses money**"}. '
      'On this little data the seed alone moves the result substantially, so any single '
      'run is unreliable and promotion requires every seed to hold up.')
    A('')
    if recent:
        A('## Recent 18 months (decay check)')
        A('')
        A(f'| metric | full period | recent |')
        A('|---|---:|---:|')
        A(f'| gated trades | {agg["n_taken"]:.1f} | {recent["n_taken"]:.1f} |')
        A(f'| lift | {fmt(agg["lift"])} | {fmt(recent["lift"])} |')
        A(f'| avg P&L | {fmt(agg["avg"], "%")} | {fmt(recent["avg"], "%")} |')
        A(f'| profit factor | {fmt(agg["pf"])} | {fmt(recent["pf"])} |')
        A('')
        A('A materially worse recent block is the earliest sign of edge decay.')
        A('')
    A('## Threshold sweep')
    A('')
    A('Is 0.65 still the right gate?')
    A('')
    A('| gate | gated trades | 25%+ rate | lift | avg P&L | profit factor |')
    A('|---:|---:|---:|---:|---:|---:|')
    for s in sweep:
        marker = ' **(live)**' if abs(s['gate'] - card['gate']) < 1e-9 else ''
        A(f'| {s["gate"]:.2f}{marker} | {s["n_taken"]:.1f} | {fmt(s["rate"], "%", 1)} | '
          f'{fmt(s["lift"])} | {fmt(s["avg"], "%")} | {fmt(s["pf"])} |')
    A('')
    A('Raising the gate always looks better on fewer trades. Prefer the lowest gate that '
      'still clears the bar, and treat rows with very few gated trades as noise.')
    A('')
    A('## Feature importance')
    A('')
    A('| feature | gain % | recent shift (SD) |')
    A('|---|---:|---:|')
    for d in sorted(drift, key=lambda x: -x['importance']):
        A(f'| {d["feature"]} | {d["importance"]:.1f}% | {d["shift_sd"]:+.2f} |')
    A('')
    if dead:
        A(f'**Dead features contributing nothing: `{", ".join(dead)}`.** '
          'Worth removing or reworking — they add dimensionality without signal.')
        A('')
    A('`recent shift (SD)` compares the last 18 months of signals against the training '
      'window, in training standard deviations. Anything beyond about 0.5 SD on an '
      'important feature means the market has moved away from what the model learned.')
    A('')
    A('## Promotion checks')
    A('')
    A('| check | value | required | result |')
    A('|---|---:|---:|:--:|')
    for n, v, t, p in checks:
        A(f'| {n} | {fmt(v)} | {t} | {"PASS" if p else "FAIL"} |')
    A(f'| every seed profitable | {fmt(min(spread) if spread else float("nan"), "%")} | > 0% | '
      f'{"PASS" if seed_stable else "FAIL"} |')
    A('')
    A('## Caveats')
    A('')
    A('- Labels come from replaying the strategy on adjusted price history. Real fills '
      'differ, especially on gap-up entries and in thin names.')
    A('- If the universe is built from the current watchlist it is survivorship-biased: '
      'stocks are on it partly because they already performed.')
    A('- Trades are treated as independent. In a correlated small-cap drawdown they are '
      'not, so drawdown is understated.')
    A('')
    with open(os.path.join(outdir, 'report.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(L) + '\n')


if __name__ == '__main__':
    sys.exit(main())
