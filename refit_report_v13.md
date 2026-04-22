# Refit Report v13 — NaN Renormalization at Ensemble Layer

**Session:** 13
**Date:** 2026-04-18
**Status:** Shipped. `build_robust.py`'s inlined `row_ensemble` now renormalizes active weights when a hypothesis is NaN on a given day, matching the convention already used at the hypothesis layer by `common.composite_score`. `common.composite_score_renorm` added as the v13+ preferred function for ensemble-layer code; `composite_score_no_renorm` retained for backward compat with diagnostic code.

---

## TL;DR

A single, isolated convention change at the ensemble layer. Fixes a regime-dependent compression of pre-2024 `ensemble_score` where ETF Flows was NaN (up to 33% compression in bear regime, smaller in others). On the **deployed wf365 canonical**, the hold-out window and today's call are **exactly unchanged** — the rolling-365 percentile window doesn't reach pre-ETF dates at the current date. On the sf730 reference variant, hold-out Sharpe improves +0.077 because rolling-730 reaches across the boundary. Full-window metrics shift in both directions on both variants.

**v13 canonical numbers (wf365, gross, 2026-04-17 data):**

| KPI | v12-fresh | v13 | Δ |
|---|---:|---:|---:|
| Full Sharpe | 1.150 | **1.201** | +0.051 |
| Hold-out Sharpe | 1.191 | **1.191** | +0.000 |
| Full Total | +428.8% | +534.4% | +105.6pp |
| Hold-out Total | +28.6% | +28.6% | +0.0pp |
| Full MaxDD | −29.1% | **−38.1%** | −9.0pp (worse) |
| Hold-out MaxDD | −19.3% | −19.3% | +0.0pp |
| Full AUC | 0.714 | 0.737 | +0.023 |
| Hold-out AUC | 0.886 | 0.886 | +0.000 |

**Today's position (2026-04-17):** wf365 unchanged at percentile 0.5288 → position 1.00. sf730 reference unchanged at percentile 0.6014 → position 0.49. Both variants' today-call is insensitive to the fix.

---

## 1. The fix

### 1.1 What changed

Two files, small edits:

**`common.py`** — `composite_score_renorm` added alongside existing `composite_score_no_renorm` (retained for backward compat with `build_nnls_diagnostic.py`). Implementation is identical to the hypothesis-layer `composite_score`: NaN-skip with renormalization of active weights on each row.

**`build_robust.py`** — `row_ensemble` (the inlined ensemble computation at line ~178) replaces its weighted-sum accumulator with `num / denom` where `denom` is the sum of non-NaN weights on that row. Zero other changes to `build_robust.py`. Walk-forward fitting, per-regime weight logic, percentile, position function — all unchanged.

### 1.2 The bug this fixes

When a hypothesis composite is NaN on a given day, the pre-v13 `row_ensemble` added `W[h] * 0` to the accumulator while the denominator stayed at the full weight sum. Result: pre-2024 `ensemble_score` (ETF Flows NaN throughout) was compressed by a regime-dependent factor of `(1 − w_etf_flows_for_that_fit)`:

- Bull regime: typically w_etf ≈ 0.04 → ~4% compression
- Neutral regime: typically w_etf ≈ 0.08 → ~8% compression
- Bear regime: typically w_etf ≈ 0.33 → ~33% compression (but 0 in pre-ETF regime where the calibration window had no ETF data — see §3)

The compression interacts with rolling-365d percentile: within-window rank shifts when some days are compressed and others aren't, pushing percentile values across the (0.55, 0.70) taper thresholds and changing positions.

### 1.3 Correct framing: convention change, not code bug

The pre-v13 behavior was documented in `common.composite_score_no_renorm`'s docstring ("per playbook §8.1"). It was a deliberate convention, not a slip. Changing it changes what `ensemble_score` means: pre-v13, scale depended on data availability; v13, scale is stable at the `[0, 1]` interpretation implied by `[0, 1]` hypothesis composites and regime weights summing to 1.

This matters for the reporting framing: **the fix is principled, not a performance optimization.** The KPI improvements that land cleanly (hold-out AUC unchanged on wf365, +0.023 on full-window) are side-effects of a more interpretable convention. The costs (wf365 MaxDD worsens 9pp full-window, sf730 full Sharpe −0.116) are honest tradeoffs of the convention change, not bugs to fix elsewhere.

---

## 2. Validation gates (per handover playbook §2.3)

All four gates pass. Details:

**Gate 1 — All-present date invariance:** On dates where all 5 hypotheses are non-NaN (769 dates, 2024-03-10 onward), `v13_ensemble_score` equals `v12_ensemble_score` to machine epsilon (max abs diff 1.1e-16). Renormalization is a no-op when nothing is NaN. ✓

**Gate 2 — Pre-ETF ratio = 1/(1 − w_etf):** On the 2,132 dates where only ETF Flows is NaN, the ratio `v13 / v12` matches `1 / (1 − w_etf_for_that_fit_date)` to machine epsilon (max abs diff 2.2e-16). Per-regime median ratios:
- Bull: 1.2500 (w_etf ≈ 0.20 at backward-extended first-fit weights)
- Neutral: 1.2500 (ditto)
- Bear: 1.0000 (first-fit had no ETF data → weight 0 → no-op for pre-ETF bear dates)

The bear=1.0000 is initially surprising but correct: the first-fit training window (2022-07-01) covered [2021-06-30, 2022-07-01), during which ETF Flows was NaN throughout. `fit_weights` correctly assigned weight 0 to ETF Flows in that fit. Pre-ETF bear-regime dates backward-extend to that fit, so the NaN fix is a no-op there. ✓

**Gate 3 — Sharpe moves modestly:** Full-window wf365 Sharpe +0.051, hold-out 0.000, sf730 Sharpe +0.077 hold-out / −0.116 full. In the ±0.1 range the playbook predicted, with the exact pattern depending on the rolling-percentile window relative to the ETF Flows start date (see §3). ✓

**Gate 4 — Walk-forward weights UNCHANGED:** The handover playbook §2.3 claimed weight_history would change because "the calibration window's AUC-excess weight computation uses composite series." This is incorrect for this codebase. `fit_weights` in `build_robust.py` computes per-hypothesis AUCs directly against the label (lines 94–102), not via the ensemble composite function. So the ensemble composite convention is independent of weight fitting. Empirically: max weight diff across all 690 weight_history rows is **exactly 0**. The playbook's prediction was wrong. ✓

---

## 3. Why wf365 hold-out is exactly unchanged but sf730 hold-out improves

wf365 uses a rolling-365d percentile window. sf730 uses rolling-730d. The hold-out year starts 2025-04-17. Running the windows backward:

- **wf365 hold-out (2025-04-17 → 2026-04-17):** trailing window reaches back to 2024-04-17. All dates in the window have ETF Flows present (first non-NaN 2024-03-10). No dates in the window are affected by the NaN fix. → hold-out is exactly unchanged.
- **sf730 hold-out (same):** trailing window reaches back to 2023-04-17. About 10 months of the window has ETF Flows NaN. Those days' `ensemble_score` changes under the fix → their relative rank within the 730-day window changes → percentile for current dates shifts slightly → position shifts → Sharpe moves.

**Mechanical consequence:** under current data (ETF Flows live 2024-03-10), the wf365 canonical is insulated from this fix for hold-out evaluation and live-trading decisions. In 2026-03-10 onward, the trailing-365d window on wf365 will *fully* exit the pre-ETF era and can no longer be affected. sf730 will take until 2026-03-10 to fully exit.

This is not an accidental feature — rolling-window percentile by construction forgets old regimes. It's worth noting as context: if we were to ship this change specifically to improve hold-out Sharpe, wf365 gets nothing and sf730 gets +0.077. The reason to ship is convention, not score.

---

## 4. Full-window cost: 2022 MaxDD

The −9pp full-window MaxDD worsening on wf365 (−29.1% → −38.1%) comes primarily from 2022. Per-quarter Sharpe deltas:

| Quarter | v12-fresh | v13 | Δ |
|---|---:|---:|---:|
| 2022 Q2 | −2.218 | −2.872 | **−0.654** |
| 2022 Q3 | −0.027 | +0.559 | +0.587 |
| 2022 Q4 | −0.909 | −0.926 | −0.017 |
| 2023 Q4 | +4.342 | +4.330 | −0.012 |
| 2024 Q1 | +2.419 | +2.711 | +0.292 |
| 2024 Q2 | +1.406 | +0.770 | **−0.636** |

2022 Q2 and 2024 Q2 take the biggest hits. 2022 Q3 and 2024 Q1 benefit most. Net 2022 Sharpe: −0.825 → −1.050 (worse 0.226). Net 2024 Sharpe essentially unchanged. 2022's bear regime is where the NaN compression was structurally largest, so it's the year most reshaped by the fix — but the direction is mixed because compression was uniform within-regime, while the un-compression shifts *relative* percentile ranks across dates.

This is the honest cost of the fix. It's defensible on convention grounds and partially offset by better ensemble AUC (+0.023 on full-window), but it is not strictly better on all axes. An implementation that wanted to preserve 2022 behavior exactly would have to leave the convention in place, i.e. reject this fix.

---

## 5. What did NOT change

- `common.composite_score` (hypothesis layer) — already renormalizes; untouched.
- `common.composite_score_no_renorm` — retained for backward compat with `build_nnls_diagnostic.py`.
- Hypothesis builders (`build_macro_equities.py`, `build_cme.py`, `build_crypto_derivatives.py`, `build_classic_cycle.py`, `build_etf_flows.py`, `build_eth.py`). Hypothesis composites are identical.
- `build_foundation.py` — D2h regime classifier unchanged.
- `weights.csv`, `weight_history_wf365_y_{60,30}.csv` — exactly unchanged (Gate 4).
- `pull_all_raw_data.py`, `pull_artemis_etf.py`, `fix_parsers.py`.
- Thresholds (0.55, 0.70 for wf365; 0.55, 0.65 for sf730), calibration label y_60, MIN_CALIB per hypothesis, regime classifier, pinning sets.

---

## 6. Sanity-check targets (v13)

For a canonical rerun with data through 2026-04-17 or later:

| Metric | wf365 target | sf730 target |
|---|---|---|
| Full Sharpe | 1.20 ± 0.05 | 1.21 ± 0.05 |
| Hold-out Sharpe | 1.19 ± 0.05 | 1.76 ± 0.10 |
| Full MaxDD | −38% ± 3pp | −30% ± 3pp |
| Hold-out MaxDD | −19% ± 3pp | −12% ± 3pp |
| Full AUC | 0.74 ± 0.02 | 0.75 ± 0.02 |
| Hold-out AUC | 0.89 ± 0.02 | 0.89 ± 0.02 |
| Today's wf365 position (2026-04-17) | 1.00 | — |
| Today's sf730 position (2026-04-17) | — | 0.49 |

Hypothesis hold-out AUCs (unchanged from v12 since hypothesis builders didn't change):

| Hypothesis | Hold-out AUC |
|---|---:|
| macro_equities | 0.631 |
| cme | 0.696 |
| crypto_derivatives | 0.671 |
| classic_cycle | 0.748 |
| etf_flows | 0.479 |

These differ from v12 report's targets because of data-refresh drift (more days in backtest, FRED revisions). They are the correct reference for v13 and forward.

---

## 7. Committed-master provenance (forensic finding)

The committed `master_daily_view_wf365.csv` (file mtime 2026-04-17 21:56) does NOT exactly match the v12 pipeline when re-run on fresh data — it differs on `ensemble_score` by max 0.049, `percentile` max 0.36, `position` max 1.0 (taper-zone amplification). Sharpe: committed 1.086 full / 1.152 hold-out. v12-fresh: 1.150 full / 1.191 hold-out.

The original sessions producing the committed master used a slightly earlier raw-data snapshot. Several data sources have live revisions that propagate into earlier dates:

- FRED series (T10Y2Y, DFF, BAMLH0A0HYM2) revise historically
- Coinglass ETF endpoint was fixed 2026-04-17, schema change in parser (v10)
- CFTC adds one row per Friday; small set of historical entries occasionally revised

The committed master also sits ~6pp below fresh on Sharpe, larger than any expected daily revision, suggesting possibly one full refit pipeline run that happened in between v11 data snapshots and the v12 code push. This is consistent with the handover's "v12 note (2026-04-17)" language being added while the pipeline regeneration reused an earlier snapshot.

**Operational implication:** the v13 master in this report is a clean end-to-end run on data pulled 2026-04-18. The committed v12 master should be considered historical — not a bit-exact baseline. This also means the v12 report's headline numbers (1.09 / 1.15) should be read as approximate under slight data drift, not as fixed targets.

---

## 8. Files produced

New/updated in this session:

- `common.py` — added `composite_score_renorm`, retained `composite_score_no_renorm` with updated docstring.
- `build_robust.py` — `row_ensemble` now renormalizes; documentation comment added.
- `master_daily_view_wf365_v13.csv` — v13 canonical daily view (4,231 rows). Supersedes committed `master_daily_view_wf365.csv`.
- `master_daily_view_sf730_v13.csv` — v13 sf730 reference.
- `data/final/ensemble_wf365_y_60_v13.parquet`, `ensemble_sf730_y_60_v13.parquet` — versioned ensemble outputs.
- `data/final/weight_history_wf365_y_60_v13.csv` — weight_history, bit-identical to v12-fresh.

Versioned v12-fresh comparables retained at `*_v12.parquet` and `master_daily_view_wf365_v12_fresh.csv` for auditability.

---

## 9. Open items (updated)

1. **Cardinal calibration retry.** Unchanged.
2. **Operational comms runtime.** Unchanged.
3. **Operational hygiene.** Unchanged. Note added: when promoting v13 artifacts to `/mnt/project/`, ensure master and weight_history are from the same pipeline run (avoid the v11→v12 provenance ambiguity found in §7).
4. `OPERATIONS.md`. Unchanged.
5. **Annual health-check script.** Unchanged.
6. **Deferred: structural fix for rolling-percentile amplification.** Unchanged. The NaN fix doesn't address this.
7. **Automated roster monitor (playbook item 2).** NOT implemented this session. The uploaded playbook proposed a 10-trigger / 3-persistence-window / 4-CI-threshold state machine as a replacement for hardcoded `PINNED_DIRECTION` / `STRONG_PRIOR` sets. Deferred on project-norm grounds: "if a fix requires 3+ new parameters, reject it." The principle behind it (drift detection for individual sub-signals) is still useful, but the specific design needs a pass that starts from "what's the a priori minimum-viable version" rather than reverse-engineering a state machine to reproduce the v2 monitor's current findings. The playbook's §3.8 validation gate "matches v2 monitor's currently-active findings" is circular — pass by construction. Take up next session if needed; write a tighter spec first.

---

## 10. Record of caveats

- The pre-v13 convention was consistent with pre-v13 walk-forward weights (both were fit in a universe where `ensemble_score` had regime-dependent compression pre-2024). Un-compressing the series without refitting weights would be a real inconsistency; the refit is principled because walk-forward weights are **independent** of the composite convention (verified empirically — Gate 4). If the fitter DID depend on the ensemble composite series (which the playbook incorrectly assumed), this fix would introduce a circularity. It doesn't in this codebase.
- wf365's hold-out unchanging to 4 decimals is a structural consequence of rolling-365 having exited the pre-ETF era. If ETH is re-added in a future v-revision (reversibility note in v12 §9), the rolling-365 window on wf365 at some future date might regress into a NaN-affected region depending on ETH's availability timeline. Worth checking at re-add time.
- 2022 MaxDD cost (−9pp) is real. The fix is defensible on convention grounds, not on MaxDD grounds.
- The playbook's predicted "+5-10% relative Sharpe improvement on hold-out" (CF2 estimate) was accurate for sf730 (+6.5% relative) and inaccurate for wf365 (0%). The CF2 simulation in the playbook-author session was run on rolling-730 analog data; the conclusion generalizes to sf730, not to the deployed wf365 canonical.
- The playbook's predicted weight_history change was wrong. Verified empirically that weights are invariant under this fix.
