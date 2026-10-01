# Quarterly retrain report — 2026-10-01

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
| incumbent config: all data | 234 | 20.4 | 1.84 | 5.88% | 1.97 | 43.1% |

The incumbent *model instance* cannot be scored fairly against historical out-of-sample data, because it was trained on those trades. So this table compares **configurations** under an identical walk-forward, not one saved model against another.

## Seed sensitivity

Average gated P&L across seeds: `+2.25%, +2.91%, +6.92%, +7.44%, +9.88%`

Worst seed is profitable. On this little data the seed alone moves the result substantially, so any single run is unreliable and promotion requires every seed to hold up.

## Recent 18 months (decay check)

| metric | full period | recent |
|---|---:|---:|
| gated trades | 20.4 | 5.2 |
| lift | 1.84 | 1.71 |
| avg P&L | 5.88% | 8.11% |
| profit factor | 1.97 | 2.59 |

A materially worse recent block is the earliest sign of edge decay.

## Threshold sweep

Is 0.65 still the right gate?

| gate | gated trades | 25%+ rate | lift | avg P&L | profit factor |
|---:|---:|---:|---:|---:|---:|
| 0.50 | 37.8 | 16.6% | 1.55 | 3.86% | 1.58 |
| 0.55 | 31.4 | 16.1% | 1.51 | 3.08% | 1.50 |
| 0.60 | 25.0 | 16.9% | 1.59 | 3.69% | 1.58 |
| 0.65 **(live)** | 20.4 | 19.6% | 1.84 | 5.88% | 1.97 |
| 0.70 | 16.0 | 16.3% | 1.52 | 4.20% | 1.69 |
| 0.75 | 11.8 | 15.1% | 1.41 | 3.51% | 1.57 |

Raising the gate always looks better on fewer trades. Prefer the lowest gate that still clears the bar, and treat rows with very few gated trades as noise.

## Feature importance

| feature | gain % | recent shift (SD) |
|---|---:|---:|
| atr_pct | 17.7% | +0.05 |
| bbw_width_pct | 14.2% | +0.04 |
| close_high_ratio | 14.1% | +0.00 |
| distance_from_50sma | 10.8% | -0.09 |
| prior_runup_90 | 6.8% | -0.13 |
| relative_strength_125 | 6.7% | -0.03 |
| days_in_squeeze | 6.6% | -0.06 |
| rsi_absolute | 6.5% | -0.04 |
| nifty_distance_from_50sma | 5.6% | -0.12 |
| volume_multiple | 5.5% | -0.04 |
| rsi_delta | 5.4% | -0.01 |
| nifty_trend | 0.0% | -0.11 |

**Dead features contributing nothing: `nifty_trend`.** Worth removing or reworking — they add dimensionality without signal.

`recent shift (SD)` compares the last 18 months of signals against the training window, in training standard deviations. Anything beyond about 0.5 SD on an important feature means the market has moved away from what the model learned.

## Promotion checks

| check | value | required | result |
|---|---:|---:|:--:|
| lift above base rate | 1.84 | 1.1 | PASS |
| gated trades profitable | 5.88 | 0.0 | PASS |
| profit factor | 1.97 | 1.2 | PASS |
| enough gated trades | 20.40 | 15 | PASS |
| every seed profitable | 2.25% | > 0% | PASS |

## Caveats

- Labels come from replaying the strategy on adjusted price history. Real fills differ, especially on gap-up entries and in thin names.
- If the universe is built from the current watchlist it is survivorship-biased: stocks are on it partly because they already performed.
- Trades are treated as independent. In a correlated small-cap drawdown they are not, so drawdown is understated.

