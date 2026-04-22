# Monitor-as-decision-system backtest

**Date:** 2026-04-18
**Scope:** Does acting on `health_check.py` flags at each monthly refit improve the model? Tested four decision rules over a 24-month window (2024-05 to 2026-04).

## TL;DR

The MVP monitor's naive rule — drop any sub-signal whose hold-out AUC ≤ 0.50 OR whose IS-OOS delta > 0.15 — **hurts** the model: full-window Sharpe drops 0.28, hold-out Sharpe drops 0.11, MaxDD worsens 3pp. Confirmed the concern flagged earlier.

A **2-month persistence rule** — drop only signals whose hold-out AUC ≤ 0.50 for 2 consecutive months — appears to beat baseline substantially: full-window Sharpe +0.31, hold-out Sharpe +0.66, MaxDD better by 5pp. But this is one 24-month backtest and I chose the rule by comparing four variants on the same data, so the result is suspect for standard overfitting reasons.

**Do not auto-promote any decision rule to production based on this single backtest.** Strongest recommendation: shadow the persist-2 rule alongside production for 2 quarters and re-evaluate.

## Setup

**Walk-forward discipline** (verified, no contamination):
- At decision date D, the rolling 365-day AUC uses labels [D-365, D-60] (y_60 requires 60 days forward; compute_auc drops NaN labels naturally). All historical at D.
- Position change from D+1 onward, evaluated on btc_return[D+1, ...]. All forward from D.
- No hold-out leakage in the decision-rule evaluation. (Rule *selection* contamination is a separate issue — discussed below.)

**Ablation design**:
- Production sub-signal weights (static, single-fit). Drop = zero that sub-signal's weight and renormalize the remaining sub-signals in the same hypothesis.
- Production ensemble weights at each monthly refit date (from `weight_history_wf365_y_60.csv`). Unchanged across the comparison.
- Only the dropping of flagged sub-signals varies across rules.
- Baseline reproduces the committed `ensemble_score` to 1e-6, so deltas are apples-to-apples.

**Rules compared**:
| rule | description | # params |
|---|---|---:|
| baseline | no monitor action (= production canonical) | — |
| naive | MVP current: drop if OOS AUC ≤ 0.50 OR IS-OOS Δ > 0.15 | 3 |
| oos_only | drop only if OOS AUC ≤ 0.50 (no delta rule) | 2 |
| oos_persist2 | drop if OOS AUC ≤ 0.50 in current AND previous month | 3 |
| oos_persist3 | drop if OOS AUC ≤ 0.50 for 3 consecutive months | 3 |

All within the 3-parameter budget except `oos_only` which is 2.

## Results

### Full backtest window (2024-05-01 → 2026-04-17, 24 months)

| rule | avg drops/mo | Sharpe (365d) | total ret | MaxDD | OOS AUC |
|---|---:|---:|---:|---:|---:|
| baseline | 0.0 | 1.053 | +83.1% | −26.7% | 0.886 |
| naive | 17.5 | 0.772 | +52.7% | −30.0% | 0.900 |
| oos_only | 16.5 | 1.040 | +87.7% | −32.5% | 0.896 |
| **oos_persist2** | **14.2** | **1.358** | **+121.7%** | **−21.3%** | **0.888** |
| oos_persist3 | 12.5 | 1.309 | +114.4% | −22.9% | 0.886 |

### Hold-out subset (2025-04-17 → 2026-04-17, 12 months)

| rule | Sharpe | total ret | MaxDD | OOS AUC |
|---|---:|---:|---:|---:|
| baseline | 1.191 | +28.6% | −19.3% | 0.886 |
| naive | 1.081 | +31.5% | −29.5% | 0.900 |
| oos_only | 0.893 | +26.3% | −32.5% | 0.896 |
| **oos_persist2** | **1.847** | **+52.1%** | **−20.4%** | **0.888** |
| oos_persist3 | 1.965 | +54.8% | −19.1% | 0.886 |

### Today's position (2026-04-17)

All five rules give position 1.00 (fully long). The decisions diverge historically, not at present.

## Mechanism check: where do persist-2's wins come from?

Monthly return comparison (persist2 − baseline):

```
  2024-05      +0.0% (no flags yet, still warming up)
  2024-06      -3.0%
  2024-07      +9.8%  ← big win
  2024-08      -1.2%
  2024-09      +1.6%
  2024-10      +0.1%
  2024-11     -24.9%  ← BIG LOSS (Trump trade rally, baseline +41%, persist2 +16%)
  2024-12      +1.1%
  2025-01      -0.0%
  2025-02      +3.1%
  2025-03      +1.9%
  2025-04      +9.8%  ← big win
  2025-05      -6.0%
  2025-06      +2.2%
  2025-07      +2.6%
  2025-08      +3.1%
  2025-09      -1.1%
  2025-10      +6.8%  ← caught the Oct 2025 turn better than baseline
  2025-11      -9.0%
  2025-12      +0.4%
  2026-01      +0.1%
  2026-02      +1.0%
  2026-03      +9.6%  ← big win
  2026-04      +8.2%  ← big win
```

**16/24 months win, 7/24 lose, 1 tie.** The wins are concentrated: five +8%-to-+10% months carry most of the alpha. The single loss in Nov 2024 (−24.9% relative) is catastrophic in magnitude — persist2 was defensive into the Trump-trade rally when baseline caught +41%. If that kind of miss recurs in a different market cycle, the cumulative edge evaporates.

Sharpe hides this concentration: a few +10% months divided by a low stdev looks great on paper. The realised story is lumpier.

## Overfitting concerns

Naming these explicitly:

1. **One window, one market regime.** 2024-05 to 2026-04 is a specific macro environment. Whether persist-2 wins in a bear-dominated window or a sideways window is unknown.

2. **Rule selection on the evaluation set.** I tested four variants. The best one winning by a visibly wide margin is the expected behaviour under multiple-comparison noise. With four rules on 24 points, I've effectively done a mini grid search. In a cleaner study, rule selection would be on an earlier window and evaluation on a held-out later window.

3. **Persistence = 2 is suspiciously specific.** Persist-3 is close but slightly worse. Persist-1 (= oos_only) is worse than baseline. The optimum sitting at exactly 2 on this particular 24-month window could easily be noise.

4. **Classic-cycle often gets fully dropped.** Under persist-2, classic_cycle's 4 sub-signals are frequently all flagged, meaning the remediated composite falls back to baseline for that hypothesis. So persist-2's action on classic_cycle is often "no action" (soft failure mode). The rule's real work is on macro and crypto_deriv sub-signals, which have 8 and 10 sub-signals respectively and can absorb drops.

5. **Nov 2024 is a tail risk that should give us pause.** Baseline +40.8%, persist2 +15.8%, 25% underperformance in one month. In live trading that would be a highly visible career event. The fact that the backtest still comes out ahead cumulatively is not sufficient evidence that the rule is safe.

## Interpretation

The backtest shows:

- The naive MVP rule (as shipped in v14) is confirmed to hurt. Ship the monitor as reporting-only, as it already is. Do not auto-apply its flags.
- A persistence-gated variant *might* be a genuine improvement. The evidence is single-window and the rule-selection process was loose. Insufficient to ship.
- Whatever edge exists is concentrated in a handful of months and carries real tail risk (Nov 2024-type miss).

## Recommendations

1. **Keep `health_check.py` as reporting-only.** Do not add automated action in this session. HANDOVER open item #7's "reports stay reports" stance is vindicated by this backtest.

2. **Shadow the persist-2 rule.** At each monthly refit, compute which signals persist-2 would have dropped and what the resulting position would have been. Log it alongside production. Two quarters of parallel data (= 6 monthly comparison points beyond this backtest) would meaningfully reduce the one-window concern. This has no production risk — it's a parallel computation.

3. **Extend the backtest window backward.** The `health_check_history.csv` currently covers 24 months. Extending to 36–42 months (back to ~mid-2022) would add 12–18 decision points with different market conditions. Bounded by needing ≥ 365 days of post-calibration-start data for the trailing window, first feasible decision date is ~2022-07. Not a huge engineering cost.

4. **Before ever automating the rule, run a proper out-of-sample rule-selection test.** Select the rule (persistence length, threshold) on the first half of an extended backtest; evaluate on the second half. If persist-2 wins cleanly out-of-sample, that's much stronger evidence than what we have now.

5. **If a promotion from reporting to action ever happens, it should replace rather than augment.** The current MVP rule (naive) should be dropped from the monitor's default output in favor of whatever rule survives validation. Carrying both flag-sets would create confusion about which to act on.

## What this backtest doesn't answer

- Would acting on flags earlier (different rolling window than 365 days) help or hurt?
- Does the rule behave differently in bear regimes? The 24-month window had one major correction (Oct 2025); not enough to characterise bear-regime performance.
- Does the gain persist if ensemble weights are also refit on remediated composites (more expensive, more honest ablation)?
- Does drop-and-renormalise compare well to a softer "reduce weight by half" intervention?

Each of these is a separate experiment. The one executed here is the minimum viable: does the straightforward rule help? Answer: the current MVP rule doesn't; a persistence-gated variant might.

## Files produced

- `monitor_backtest.csv` — day-by-day ensemble_score, percentile, position, strategy_return for baseline vs naive rule. For audit.
- `backtest_monitor_report.md` — this document.
