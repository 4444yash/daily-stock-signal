# Quarterly retrain report — 2026-08-01

**Verdict: PROMOTE.** The candidate clears every promotion check.

The live model is never replaced by this workflow. Promotion happens only when a human merges the pull request.

## What was measured

- Training data: `results/xgboost_training_data.csv`, 1206 labelled trades
- Final fit on the most recent 300 trades (2025-04-22 to 2026-05-25)
- Target: `trade_pnl >= 25`, base rate 7.79%
- Walk-forward monthly refit, 60-day resolution buffer, gate 0.65
- Averaged over 5 seeds; scored on the `small` universe only

## Configuration comparison

| config | OOS trades | gated | lift | avg P&L | profit factor | win rate |
|---|---:|---:|---:|---:|---:|---:|
| incumbent config: all data | 234 | 19.6 | 1.72 | 4.41% | 1.71 | 37.7% |

The incumbent *model instance* cannot be scored fairly against historical out-of-sample data, because it was trained on those trades. So this table compares **configurations** under an identical walk-forward, not one saved model against another.

## Seed sensitivity

Average gated P&L across seeds: `+1.68%, +2.17%, +4.88%, +5.78%, +7.57%`

Worst seed is profitable. On this little data the seed alone moves the result substantially, so any single run is unreliable and promotion requires every seed to hold up.

## Recent 18 months (decay check)

| metric | full period | recent |
|---|---:|---:|
| gated trades | 19.6 | 5.8 |
| lift | 1.72 | 1.44 |
| avg P&L | 4.41% | -0.28% |
| profit factor | 1.71 | 1.13 |

A materially worse recent block is the earliest sign of edge decay.

## Threshold sweep

Is 0.65 still the right gate?

| gate | gated trades | 25%+ rate | lift | avg P&L | profit factor |
|---:|---:|---:|---:|---:|---:|
| 0.50 | 34.8 | 16.7% | 1.56 | 3.58% | 1.54 |
| 0.55 | 29.6 | 17.6% | 1.64 | 4.05% | 1.61 |
| 0.60 | 24.2 | 19.2% | 1.79 | 4.21% | 1.66 |
| 0.65 **(live)** | 19.6 | 18.4% | 1.72 | 4.41% | 1.71 |
| 0.70 | 14.6 | 12.4% | 1.16 | 1.72% | 1.26 |
| 0.75 | 10.8 | 14.6% | 1.37 | 2.54% | 1.35 |

Raising the gate always looks better on fewer trades. Prefer the lowest gate that still clears the bar, and treat rows with very few gated trades as noise.

## Feature importance

| feature | gain % | recent shift (SD) |
|---|---:|---:|
| distance_from_50sma | 17.0% | -0.09 |
| atr_pct | 15.0% | +0.05 |
| close_high_ratio | 12.9% | +0.00 |
| bbw_width_pct | 11.5% | +0.04 |
| nifty_distance_from_50sma | 9.3% | -0.12 |
| days_in_squeeze | 7.5% | -0.06 |
| relative_strength_125 | 6.4% | -0.03 |
| prior_runup_90 | 6.1% | -0.13 |
| volume_multiple | 5.0% | -0.04 |
| rsi_delta | 4.9% | -0.01 |
| rsi_absolute | 4.3% | -0.04 |
| nifty_trend | 0.0% | -0.11 |

**Dead features contributing nothing: `nifty_trend`.** Worth removing or reworking — they add dimensionality without signal.

`recent shift (SD)` compares the last 18 months of signals against the training window, in training standard deviations. Anything beyond about 0.5 SD on an important feature means the market has moved away from what the model learned.

## Promotion checks

| check | value | required | result |
|---|---:|---:|:--:|
| lift above base rate | 1.72 | 1.1 | PASS |
| gated trades profitable | 4.41 | 0.0 | PASS |
| profit factor | 1.71 | 1.2 | PASS |
| enough gated trades | 19.60 | 15 | PASS |
| every seed profitable | 1.68% | > 0% | PASS |

## Caveats

- Labels come from replaying the strategy on adjusted price history. Real fills differ, especially on gap-up entries and in thin names.
- If the universe is built from the current watchlist it is survivorship-biased: stocks are on it partly because they already performed.
- Trades are treated as independent. In a correlated small-cap drawdown they are not, so drawdown is understated.

