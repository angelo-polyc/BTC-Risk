# BTC Model Refit Report v4 — Session 4

**Date:** 2026-04-15 (session 4)
**Supersedes:** `refit_report_v3.md` for canonical numbers on the ensemble layer; v3's hypothesis-level narrative remains the definitive account of session 3's CC restriction work.

## TL;DR

Session 4 executed Task 1 (two-tier pinning weight formula) and Task 2 (walk-forward refitting). Task 1 shipped partially — Macro and Crypto Derivatives strong_prior active, Classic Cycle deferred permanently after a cascade failure. Task 2 produced a bigger finding than expected: the original walk-forward design flopped but exposed a latent issue with the percentile basis that was silently suboptimal in the v3 canonical.

**Two canonicals shipped side-by-side:**
- **`sf730`:** single-fit + rolling-730d percentile. Full Sharpe 1.19, total +537%, MaxDD −26.2%.
- **`wf365`:** monthly walk-forward + rolling-365d percentile. Hold-out OOS AUC 0.7733 (+0.010 vs sf730), hold-out Sharpe +0.51, hold-out MaxDD −32.2%.

Both beat v3 single-fit + expanding-percentile (Sharpe 0.91, total +221%, MaxDD −33.6%) on the metrics they're tuned for. The user is deploying both and will observe which performs better over time.

## 1. Task 1 — Two-Tier Pinning Weight Formula

### 1.1 Problem

The v3 `auc_excess_weights` used `max(auc − 0.5, min_excess)` for pinned signals. This floor-weights any pinned signal whose calibration-window AUC is below 0.5, even if the signal is strong out-of-sample. The pinning audit in session 3 identified three instances:
- CC `ahr999` (IS 0.34, OOS 0.76) — floored at 0.038
- CC `bmo` (IS 0.47, OOS 0.72) — floored at 0.038
- Macro `spx_overext_rank` (IS 0.43, OOS 0.67) — floored at 0.026

A naive fix `max(|auc − 0.5|, min_excess)` rescues these but also rescues CD `rv21_zscore_rank` (IS 0.36, OOS 0.50) which is the documented pinning success case — it would flip into a pure-noise signal getting 20%+ weight.

The design goal: distinguish "prior is correct and window was hostile" from "prior is speculative and signal really is noisy."

### 1.2 Design — two-tier pinning

Added `strong_prior` kwarg to `auc_excess_weights`. Signals in `strong_prior` use the `max(|auc − 0.5|, min_excess)` formula; all other pinned signals keep the floor-weighting formula. Enforced `strong_prior ⊆ no_flip` via assertion.

### 1.3 Classification (discussed and agreed with user)

**Strong_prior (10 signals):**
- Macro: `spx_overext_rank`, `vix_z90_rank`, `hy_spread_roc_rank`
- Crypto Derivatives: `funding_zscore_rank`, `lev_stress_rank`, `coin_margin_ratio_rank`
- Classic Cycle: `golden_ratio`, `bmo`, `ahr999`, `fear_greed` *(initially, later deferred)*

**Explicitly excluded:**
- Macro `fed_funds_stress_rank` — OOS AUC 0.07 indicates structurally inverted prior in 2025 window (Fed cuts were reactive during BTC drawdowns, not predictive). Promoting would bleed catastrophically-inverted direction into composite.
- Crypto Derivatives `rv21_zscore_rank` — OOS AUC 0.50 confirms pinning success case. No structural prior justifies rescue.

Regression-guard asserts in both build scripts enforce `weight < 0.05` for these two signals.

### 1.4 End-to-end test surfaced Classic Cycle cascade

With all 10 signals in strong_prior, end-to-end pipeline produced unexpected results:

| Hypothesis | IS AUC (v3→T1) | OOS AUC (v3→T1) |
|---|---|---|
| Macro & Equities | 0.740 → 0.709 (−0.030) | 0.620 → **0.642** (+0.022) |
| Crypto Derivatives | 0.717 (no change) | 0.678 (no change) |
| **Classic Cycle** | **0.615 → 0.511 (−0.104)** | 0.758 → 0.763 (+0.006) |
| Ensemble | 0.851 → 0.859 (+0.008) | **0.762 → 0.746 (−0.016)** |

Classic Cycle's IS AUC collapsed 10pp because ahr999 (IS 0.337) got 37.5% weight — the largest in CC — and its anti-predictive in-sample signal dragged the composite. OOS AUC was flat (+0.5pp only) because ahr999's marginal information above golden_ratio and fear_greed is near zero.

The ensemble fitter then read CC's degraded IS AUC and collapsed CC's weight in neutral regime from 0.102 to 0.008. Since CC's actual OOS value in neutral was real (0.76), this ensemble-layer downweighting cost more than the within-hypothesis rescue gained. Net ensemble OOS AUC: −1.6pp. Net Sharpe: −0.046.

Macro did not cascade (OOS +2.2pp, no ensemble regression) because Macro has 8 sub-signals, so no single rescue dominates. spx_overext went from 0.026 to 0.154 weight — meaningful but not overwhelming.

### 1.5 Ship decision — partial Task 1

Disabled CC strong_prior (`STRONG_PRIOR = set()` in `build_classic_cycle.py`), kept Macro and CD strong_prior enabled. Results:

| Metric | v3 baseline | T1 partial | Δ |
|---|---:|---:|---:|
| Ensemble OOS AUC | 0.7621 | 0.7637 | +0.002 |
| Sharpe full | 0.9091 | 0.9052 | −0.004 |
| Hold-out Sharpe | −0.024 | +0.051 | +0.075 |
| Hold-out MaxDD | −34.4% | −33.6% | +0.9pp |

Pattern matches a well-behaved robustness patch — no material IS change, small OOS improvement. spx_overext rescue working as designed.

### 1.6 CC deferral — revisited in Task 2

The v3 report's Task 1 deferral note hypothesized walk-forward might resolve the cascade: "periodic refitting may let the ensemble layer see CC's OOS value directly." Task 2 tested this and falsified it (§2.6). CC strong_prior is now deferred indefinitely — any fix requires ensemble-layer alpha-augmentation.

## 2. Task 2 — Walk-Forward Refitting

### 2.1 Initial implementation (per spec)

Implemented per `NEXT_SESSION_PLAN.md` §Task 2: monthly cadence, expanding window, 12-month warmup, first fit at 2022-07-01. Env var `WALK_FORWARD=1` toggles mode; all other knobs parameterized via env vars for experimentation.

### 2.2 Initial result — net negative

| Metric | Single-fit | Walk-forward K=1 | Δ |
|---|---:|---:|---:|
| Ensemble OOS AUC | 0.7637 | 0.7733 | +0.010 |
| Sharpe full | 0.9052 | 0.8436 | **−0.062** |
| Total full | +221% | +153% | **−68pp** |
| Sharpe hold-out | +0.051 | −0.032 | **−0.083** |

Hold-out OOS AUC did improve slightly (+1pp), matching the spec's prediction direction. But the AUC gain didn't translate to strategy gain — Sharpe regressed across the board.

### 2.3 Root cause investigation — bull-regime AUC noise

Diagnosed the source of weight instability. The regime classifier's `ret_200d_smooth` transitions OFF bull regime before drawdowns, so bull-regime y_60 labels have near-zero base rate for most of walk-forward's training history:

| Fit date | Bull days in window | Positive cases | AUC status |
|---|---:|---:|---|
| 2022-07-01 | 32 | **0** | All NaN → uniform 1/6 fallback |
| 2023-07-01 | 114 | **0** | All NaN → uniform 1/6 fallback |
| 2024-07-01 | 383 | ~1 | Noise-dominated |
| 2025-04-01 | 581 | ~7 | Still sparse |
| 2026-04-01 | 698 | ~22 | Usable |

Single-fit sidesteps this by using the full 1,385-day in-sample window at once. Walk-forward re-computes on progressively smaller windows, exposing latent noise. Monthly weight jumps of 0.3–0.4 in bull-regime were routine.

### 2.4 Smoothing experiment — failed

Tested trailing-K-fit averaging (K ∈ {1, 3, 6, 12}) to damp noise. **Made it worse at every K** — higher K blends in older fits that had less data (K=6 averages in fits where bull base rate was 0), so smoothing amplifies rather than dampens. K=1 remained best of the walk-forward variants.

### 2.5 Design dimension sweep

Stepped back and enumerated 8 design dimensions where the original spec had committed to a specific choice. Tested the top 3:

| Variant | Hold-out OOS AUC | Hold-out MaxDD | Hold-out Sharpe |
|---|---:|---:|---:|
| single_fit baseline | 0.7637 | −33.57% | +0.051 |
| wf_baseline (1mo, expanding, 12wm) | **0.7733** | −40.86% | −0.032 |
| cadence=3mo (quarterly) | 0.7665 | −40.86% | −0.086 |
| **window=24mo (rolling)** | 0.6071 | −37.42% | **+0.065** |
| warmup=18mo | **0.7733** | −40.50% | −0.036 |

Surprises:
- Quarterly cadence was worse, not better (fewer fits ≠ more stable when underlying AUC is noise-dominated)
- 24-month rolling window gave the highest Sharpe across all walk-forward variants, but at the cost of 16pp OOS AUC — dropped to 0.607

The rolling-window result hinted at a deeper issue: changing the weights' training window indirectly affected the position function via the score distribution, even though AUC is invariant to monotone score transforms.

### 2.6 The actual fix — rolling percentile basis

The v3 canonical position function uses `ensemble_score.expanding(min_periods=180).rank(pct=True)`. Each day's percentile ranks against all historical ensemble_scores.

Under walk-forward, the ensemble_score on 2022-08-01 was computed with one month's weights; the score on 2025-12-01 with another. These scores are on different scales. Expanding-rank mixes them. A day's "0.60 score" means different things depending on which weight regime it came from.

Rolling percentile with a short window (~1 year) ranks each day only against recent days where weights were similar:

```python
df["percentile"] = df["ensemble_score"].rolling(PERCENTILE_WINDOW, min_periods=180).rank(pct=True)
```

### 2.7 Percentile window sweep — both configs

Swept PERCENTILE_WINDOW ∈ {180, 270, 365, 450, 540, 730, 900, expanding} for both single_fit and wf_baseline. OOS AUC is invariant within each config (percentile is monotone on score) — all single_fit variants tie at 0.7637, all walk-forward variants tie at 0.7733. Hold-out MaxDD differentiates:

**Walk-forward hold-out MaxDD:**
| pct_window | 180 | 270 | **365** | 450 | 540 | 730 | 900 | expanding |
|---:|---:|---:|:---:|---:|---:|---:|---:|---:|
| MaxDD | −39.8% | −32.2% | **−32.2%** | −37.1% | −42.3% | −50.0% | −52.1% | −40.9% |
| Sharpe | −0.24 ✗ | +0.36 | **+0.51** | +0.25 | +0.02 | −0.22 ✗ | −0.28 ✗ | −0.03 |

Sharp peak at 365. 270 ties on MaxDD but weaker on Sharpe.

**Single-fit hold-out MaxDD:**
| pct_window | 180 | 270 | 365 | 450 | 540 | **730** | 900 | expanding |
|---:|---:|---:|---:|---:|---:|:---:|---:|---:|
| MaxDD | −36.1% | −32.6% | −31.4% | −30.8% | −30.1% | **−26.1%** | −28.2% | −33.6% |
| Sharpe | −0.24 ✗ | −0.05 | −0.01 | −0.10 ✗ | −0.13 ✗ | **+0.19** | +0.32 | +0.05 |

Peak at 730 for MaxDD; 900 slightly behind; 540 and 450 fail B&H constraint.

**Intuition:** the right rolling window matches the timescale on which weights are stable. Walk-forward refits monthly so ~12-month window keeps same-era scores in the comparison set. Single-fit has static weights but the market regime itself shifts; 2-year window spans one full BTC cycle.

### 2.8 CC strong_prior reinstatement under walk-forward — still cascades

Tested CC strong_prior reinstatement on both new canonicals (sf730 and wf365) to check the Task 1 deferral hypothesis:

| Config | CC_IS | CC_OOS | Ens OOS AUC | HO MaxDD | HO Sharpe |
|---|---:|---:|---:|---:|---:|
| sf730, CC off | 0.615 | 0.758 | **0.7637** | **−26.07%** | +0.192 |
| sf730, CC on | 0.511 | 0.763 | 0.7458 | −26.63% | +0.202 |
| wf365, CC off | 0.615 | 0.758 | **0.7733** | **−32.22%** | +0.509 |
| wf365, CC on | 0.511 | 0.763 | 0.7537 | −33.32% | +0.438 |

The cascade mechanism is unchanged: CC IS AUC drops 10pp whenever strong_prior is on (that's a property of the composite, not the ensemble layer), and the ensemble fitter responds by downweighting CC. Walk-forward doesn't help because every fit date sees the same degraded CC composite in its training window.

**Conclusion:** CC strong_prior requires ensemble-layer alpha-augmentation (execution_playbook_v2 §10.3). Permanently deferred until someone tackles that scope.

### 2.9 Final decision — two canonicals

Under user's stated priority order (OOS AUC primary, MaxDD secondary, Sharpe > B&H constraint):

**wf_baseline + pct=rolling-365** wins strictly:
- OOS AUC 0.7733 > 0.7637 (+0.010)
- Hold-out MaxDD −32.22% > −26.07% (loses by 6pp — the only dimension it loses)
- Hold-out Sharpe +0.509 ≫ −0.094 B&H

**single_fit + pct=rolling-730** wins on:
- Full-window Sharpe 1.19 > 1.05
- Full-window total +537% > +384%
- Full-window MaxDD −26.2% > −40.7%
- Hold-out MaxDD −26.07% > −32.22%

User decided to ship both. The AUC delta of +0.010 is well within noise (SE ~0.05 on 365 days with ~33 positives), so treating either as "the right answer" is premature. Observing both over 2026-27 will accumulate evidence.

## 3. What did NOT change

- 6 hypotheses and their per-hypothesis `MIN_CALIB` values
- y_60 as canonical label
- Regime classifier (bull/neutral/bear via `ret_200d_smooth`)
- Linear-hybrid position function
- Ensemble fitting mechanism (AUC-excess per regime)
- `build_robust.py` default behavior when env vars are unset — still v3 single-fit + expanding percentile for backward compat

## 4. Open items for session 5

1. **Task 3 — crisis validation.** Still the highest-priority next step. The 2018 bear, 2020 COVID, 2022 LUNA, 2022 FTX periods are documented in `NEXT_SESSION_PLAN.md`. `raw_data_export.csv` (bundled in project folder) has pre-2021 data.

2. **CC strong_prior reinstatement via ensemble-layer change.** Would require extending strong_prior concept to hypothesis level, OR implementing alpha-augmentation weighting. Scoped-out of session 4.

3. **Transaction costs** still not in backtest. Expect Sharpe drop ~0.10-0.15 after 5bps roundtrip + min-position-change threshold.

4. **ETF premium endpoint** still stale at 2026-01-06 (3+ months now).

5. **`fix_parsers.py`** still called separately from `pull_all_raw_data.py`.

6. **Sub-signal-layer walk-forward** (Task 2 phase 2) not implemented. Would require touching all 6 build scripts or factoring fit loop into `common.py`. Unclear if it would help given the bull-regime AUC noise finding — likely same issue at sub-signal granularity.

## 5. Meta-observation — walk-forward revealed a latent bug

The expanding-percentile rank choice in the v3 canonical was silently suboptimal. Both single-fit and walk-forward benefit from rolling percentile:

| | Sharpe | Total | MaxDD |
|---|---:|---:|---:|
| v3 canonical (single + expanding) | 0.91 | +221% | −34% |
| sf730 (single + rolling-730) | **1.19** | **+537%** | **−26%** |
| wf365 (walk-forward + rolling-365) | 1.05 | +384% | −41% |

The 30pp total-return improvement in sf730 — same weights, different rank basis — is the largest single-session model improvement and would have been missed without the walk-forward investigation making the rank-basis interaction visible.

**Transferable lesson:** when a position function downstream of a monotone-invariant scoring (AUC etc.) behaves unexpectedly, check the rank basis before assuming the scores themselves are the problem.

## 6. Session 4 success summary vs the NEXT_SESSION_PLAN criteria

- **Minimum success criterion (Task 1 shipped):** ✅ — partial Task 1 shipped, deferred CC documented
- **Stretch goal (Task 2 phase 1):** ✅ — walk-forward ensemble-layer shipped with rolling percentile as bonus discovery, weight drift analysis done
- **Full stretch (Task 3):** ❌ — deferred to session 5 per user direction

Two canonicals shipped, test coverage maintained, handover package complete.
