# Memo — Unpin fed_funds_stress_rank + MIN_CALIB tightening test

**Date:** 2026-04-18
**Author:** Session 14 follow-up

## TL;DR

Two proposed changes, tested together:

1. **Unpin `fed_funds_stress_rank`** in `build_macro_equities.py`'s `PINNED_DIRECTION`. **Shipped.** No-op in current state (calibration AUC 0.5054 is above 0.5, so the auto-flip branch doesn't fire either way). Prospective safety: if calibration AUC ever crosses below 0.5 in a future refit, auto-flip will now activate. Zero effect on today's numbers.

2. **Tighten macro `MIN_CALIB` from 2018-10-01 to 2021-06-30** in `common.py`. **Rejected.** Hurts hold-out Sharpe by 0.20 and hold-out AUC by 0.031. Today's position drops 1.00 → 0.43. Mechanism identified; existing MIN_CALIB keeping fed_funds at floor weight is confirmed as structural insurance against the signal's hold-out drift.

## Part A — unpin fed_funds_stress_rank (SHIPPED)

**Change:** Remove `"fed_funds_stress_rank"` from `PINNED_DIRECTION` set in `build_macro_equities.py`.

**Effect today:** None. Pinning in `auc_excess_weights` only changes behavior when raw calibration AUC < 0.5. On the production 2018-onward calibration window, fed_funds_stress_rank's raw AUC is 0.5054 — the auto-flip branch doesn't trigger, so pinning and unpinning produce identical weights and composite.

**Verification:**

| signal | raw AUC | prod-pin weight | unpin weight | Δweight |
|---|---:|---:|---:|---:|
| real_rate_rank | 0.618 | 35.9% | 35.9% | 0.0000 |
| yield_curve_roc_rank | 0.606 | 32.3% | 32.3% | 0.0000 |
| vix_z90_rank | 0.541 | 12.5% | 12.5% | 0.0000 |
| rates_abs_stress_rank | 0.523 | 7.1% | 7.1% | 0.0000 |
| **fed_funds_stress_rank** | **0.505** | **3.0%** | **3.0%** | **0.0000** |
| spx_overext_rank | 0.432 | 3.0% | 3.0% | 0.0000 |
| hy_spread_roc_rank | 0.401 | 3.0% | 3.0% | 0.0000 |
| fx_stress_rank | 0.502 | 3.0% | 3.0% | 0.0000 |

**Why ship it anyway:** the prior is empirically contested (hold-out AUC 0.072 on the last 365 days, flagged in every monthly replay since Feb 2025 — see `health_check_history.csv`). The `no_flip` set should represent priors we still believe. Removing a prior that no longer reflects our conviction is hygiene, even when it produces a no-op today. If calibration AUC eventually crosses under 0.5, auto-flip kicks in automatically rather than being suppressed.

**Code diff:**

```diff
 PINNED_DIRECTION = {
     "spx_overext_rank",        # stretched equities precede risk-asset drawdowns
     "vix_z90_rank",            # high VIX = risk off
-    "fed_funds_stress_rank",   # tightening = risk-asset stress
     "hy_spread_roc_rank",      # widening credit = stress
 }
```

Modified file delivered as `build_macro_equities.py` alongside this memo. Ensemble weights (`weight_history_wf365_y_{60,30}.csv`), master CSVs, hypothesis CSVs all bit-identical on rerun.

## Part B — MIN_CALIB tightening experiment (REJECTED)

**Proposal:** Change `MIN_CALIB["macro_equities"]` in `common.py` from `2018-10-01` to `2021-06-30`, matching the other hypotheses.

**Hypothesis tested:** tightening removes dilution from pre-2020 macro data, giving stronger recent signals more weight. The concern (noted before testing): this would also give `fed_funds_stress_rank` — which now has catastrophic hold-out behavior — a 17% weight in the composite instead of 3%.

**Method:** built a new macro composite with `MIN_CALIB = 2021-06-30` and `PINNED_DIRECTION` = {spx_overext, vix_z90, hy_spread_roc} (i.e., with Part A applied). Re-fit ensemble walk-forward (monthly cadence, expanding training window, 46 refits) on the new macro composite. Control check: refit-with-production-macro reproduces the committed `ensemble_score` to 1e-6. Experimental deltas are apples-to-apples.

**Results (gross):**

| window | metric | production (refit) | MIN_CALIB=2021 | Δ |
|---|---|---:|---:|---:|
| full (2021-06-30 → 2026-04-17) | Sharpe (252d ann.) | 0.998 | 0.988 | **−0.010** |
| | total return | +534.4% | +552.8% | +18.3pp |
| | MaxDD | −38.1% | −36.4% | +1.7pp |
| | ensemble AUC | 0.737 | 0.732 | −0.005 |
| hold-out (2025-04-17 → 2026-04-17) | Sharpe | 0.989 | 0.789 | **−0.200** |
| | total return | +28.6% | +21.1% | **−7.5pp** |
| | MaxDD | −19.3% | −20.3% | −1.0pp |
| | ensemble AUC | 0.886 | 0.855 | **−0.031** |

Annualized at 365d (crypto convention): full Sharpe 1.20 → 1.19 (−0.010); hold-out 1.19 → 0.95 (−0.24).

**Today's call (2026-04-17):** regime=bear, production position 1.00, experiment position 0.43. The entire change in position is driven by the ensemble percentile shifting from 0.529 (well below the 0.55 long threshold) to 0.636 (inside the 0.55–0.70 taper zone).

**New sub-signal weights in macro under tighter MIN_CALIB:**

| signal | AUC (2021+) | new weight | old weight (2018+) | Δ |
|---|---:|---:|---:|---:|
| real_rate_rank | 0.755 | 48.6% | 35.9% | +12.7pp |
| yield_curve_roc_rank | 0.626 | 24.1% | 32.3% | −8.2pp |
| **fed_funds_stress_rank** | **0.591** | **17.3%** | **3.0%** | **+14.3pp** |
| fx_stress_rank (flipped) | 0.513 | 2.4% | 3.0% | −0.6pp |
| spx_overext_rank | 0.345 | 1.9% | 3.0% | −1.1pp |
| hy_spread_roc_rank | 0.401 | 1.9% | 3.0% | −1.1pp |
| rates_abs_stress_rank (flipped) | 0.504 | 1.9% | 7.1% | −5.2pp |
| vix_z90_rank | 0.480 | 1.9% | 12.5% | −10.6pp |

Two notable side-effects:
- Two previously-floor-weighted signals (rates_abs_stress_rank, fx_stress_rank) get auto-flipped under 2021+ calibration — their raw AUCs are 0.496 and 0.487, just below the auto-flip threshold. Production didn't flip them on 2018+ window. This is the "orientation churn near 0.5" warning sign.
- vix_z90_rank loses 10.6pp of weight because its calibration AUC drops from 0.541 (2018+) to 0.480 (2021+). Another pinned signal whose "obvious" prior is mostly not visible in the post-2021 window.

**Diagnosis — why the experiment fails on hold-out:**

The tighter calibration gives fed_funds_stress_rank 17.3% weight based on its 2021-onward training-window AUC of 0.591. But the signal's hold-out AUC is 0.072. A composite that leans 17% on fed_funds then inherits fed_funds's hold-out failure. Macro composite hold-out AUC drops from 0.631 (production) to roughly 0.55 (experimental estimate), and that propagates through the ensemble.

The 2018-onward calibration window was doing the right thing for the wrong reason. The "cross-decade stability" rationale originally given for macro's longer window happened to dilute fed_funds to floor weight, which happened to insulate the model against fed_funds's post-2024 breakdown. The insurance was incidental but real.

**Recommendation:** reject. Keep macro's `MIN_CALIB` at `2018-10-01`. Do not ship.

## Combined status

- **Part A (unpin fed_funds) — SHIP.** One-line change to `build_macro_equities.py`, no effect today, small prospective safety. File delivered.
- **Part B (tighten MIN_CALIB) — DO NOT SHIP.** Hold-out Sharpe −0.20, hold-out AUC −0.031, today's position drops 1.00 → 0.43. The tighter window un-does the accidental insurance the 2018+ window was providing.

## What to do about fed_funds_stress_rank going forward

Unpinning (Part A) removes the prior but doesn't remove the signal. On the next monthly refit, if its calibration AUC crosses below 0.5, auto-flip will activate. Until then it stays at floor weight, same as today.

Three options for further action (not proposed this session, listed for planning):

1. **Do nothing beyond Part A.** The 3% weight is at the floor already. If calib AUC drifts below 0.5 in future, auto-flip handles it. Cost: slightly stale sub-signal that's not doing harm.
2. **Drop the sub-signal entirely** from `build_macro_equities.py`. Removes 8th sub-signal → redistributes 3% across remaining 7. No impact on hold-out (floor already). Violates the "don't react to single bad period" discipline unless the degradation persists through 2+ more refits.
3. **Wait for 2+ more refits** and decide then based on whether the hold-out degradation persists. This matches open item #8's guidance.

Current recommendation: (3). Leave the unpinned-but-not-removed signal in place. Revisit if next refit's health-check shows the same pattern.

## Files produced

- `build_macro_equities.py` — modified. Remove `"fed_funds_stress_rank"` from `PINNED_DIRECTION`, docstring updated.
- `memo_v14_unpin_and_min_calib_test.md` — this memo.
- `min_calib_experiment.csv` — per-row comparison of production vs MIN_CALIB=2021 ensemble, percentile, position, strategy return. For auditability.
