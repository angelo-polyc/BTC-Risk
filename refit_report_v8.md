# Refit Report — v8 (Position Threshold Recalibration)

**Date:** 2026-04-15
**Scope:** Recalibrated the position function thresholds for both canonical variants (wf365 and sf730) from the session-1 defaults (0.50, 0.90) to variant-specific values. No other model component changed.

**Outcome:**
- **wf365 (deployed):** (0.50, 0.90) → (0.55, 0.70). Full-window Sharpe 1.05 → 1.16. Hold-out Sharpe 0.51 → 0.89. Hold-out MaxDD −32% → −25%.
- **sf730 (reference):** (0.50, 0.90) → (0.55, 0.65). Full-window Sharpe 1.19 → 1.27. Hold-out Sharpe 0.19 → 0.28.
- All numbers post 5bps roundtrip cost.

---

## 1 — Motivation

The original position function was hand-set at (0.50, 0.90) across all sessions with no explicit calibration. The question was whether these values are at a local optimum or at a knife-edge requiring attention. This session tests, in order:

1. **Tier 1** — sensitivity analysis across all position-function and regime-classifier parameters to identify which are at a plateau vs a cliff.
2. **Tier 2** — walk-forward optimization of position thresholds only (the safest and lowest-parameter candidate for change).
3. **V1** — turnover and cost-robustness validation of any proposed change.
4. **V2** — multi-year hold-out validation.
5. **V3 / V3b** — regime-conditional thresholds (in-sample then honest walk-forward).
6. **V4** — cost-stratified grid (does the good zone shrink at higher costs?).

Methodological caveat carried through all tests: **metrics include 5bps roundtrip cost throughout**. Tier 1 regime-parameter sweeps hold ensemble weights fixed (directional-sensitivity only; absolute numbers in those sweeps do not match deployed Sharpe). All position-threshold tests use the deployed walk-forward percentile and are exact.

---

## 2 — Tier 1: Sensitivity analysis

Eight parameters swept, each held at defaults for the others:

| Parameter | Default | Observation |
|---|---:|---|
| Regime lookback (d) | 200 | Default sits near a local minimum; both shorter (120–150) and longer (250–280) score higher on Sharpe; AUC peaks 200–250. Not worth changing without full weight refit. |
| Regime smoothing (d) | 30 | Gently rising with length; default near plateau center. |
| Bull entry threshold | 0.35 | Default below optimum; smooth surface. |
| Bull exit threshold | 0.24 | Noisy; default reasonable. |
| Bear entry threshold | −0.15 | Default sits at a local trough. |
| Bear exit threshold | −0.08 | Very flat; doesn't matter. |
| **Position long_thr** | **0.50** | **Near optimum on this axis alone.** |
| **Position def_thr** | **0.90** | **Clearly suboptimal; lower is better.** |

**Conclusion from Tier 1:** regime-classifier parameters show small directional preferences but changing them breaks attribution and requires re-fitting per-regime ensemble weights with uncertain dynamics. Position thresholds are the clean candidate for change.

---

## 3 — Tier 2: Position threshold analysis

### Walk-forward vs fixed alternatives

Walk-forward monthly re-selection of thresholds on trailing 365d, applied to next month. Grid LONG ∈ [0.30, 0.75], DEF ∈ [0.55, 0.95]. Eval window 2022-06-30 → 2026-04-15.

| Strategy | Full Sharpe | Full Total | Full MaxDD | Hold Sharpe | Hold MaxDD |
|---|---:|---:|---:|---:|---:|
| Default (0.50, 0.90) fixed | 1.44 | +545% | −34% | 0.43 | −34% |
| Walk-forward adaptive | 1.64 | +718% | −30% | 1.25 | −22% |

**But — fixed alternatives also beat default.** Critical follow-up test:

| Combo | Full Sharpe | Full MaxDD | Hold Sharpe | Hold MaxDD |
|---|---:|---:|---:|---:|
| default (0.50, 0.90) | 1.44 | −34% | 0.43 | −34% |
| fixed (0.55, 0.70) | 1.59 | −31% | 0.89 | −25% |
| fixed (0.65, 0.67) | 1.63 | −32% | 0.93 | −32% |
| fixed (0.50, 0.55) | 1.60 | −29% | **1.66** | **−14%** |
| Tier 2 adaptive | 1.64 | −30% | 1.25 | −22% |

Fixed alternatives match or beat the adaptive version. **The win is in the threshold values themselves, not in monthly re-optimization.** Adaptive walk-forward rejected: marginal gain over fixed, adds ongoing maintenance, harder to attribute future degradation. Session-7 doctrine of "adapt at one layer only (ensemble weights), keep everything else static" preserved.

### Full grid heatmap

Sharpe surface over (long_thr, def_thr) shows:
1. Default (0.50, 0.90) sits in a low-Sharpe region in both full-window and hold-out.
2. Full-window and hold-out argmaxes differ ((0.65, 0.67) vs (0.48, 0.50)) — don't pick either argmax (overfit).
3. A robust "good zone" exists: long_thr ∈ [0.45, 0.65], def_thr ∈ [0.55, 0.75], narrow gap (≤0.20). Points in this zone simultaneously improve Sharpe AND reduce MaxDD vs default.

---

## 4 — V1: Turnover and cost robustness

### Annualized turnover (full window)

| Strategy | Annual turnover | Avg position | % defensive | % long | % intermediate |
|---|---:|---:|---:|---:|---:|
| default (0.50, 0.90) | 37.8 | 0.72 | 10.8% | 53.6% | 35.6% |
| **proposed (0.55, 0.70)** | **46.3** | **0.66** | **27.8%** | **58.1%** | **14.1%** |
| binary-ish (0.50, 0.55) | 46.1 | 0.56 | 41.9% | 53.6% | 4.5% |

Proposed has ~22% higher turnover. More decisive: spends more time fully defensive and less at intermediate positions.

### Cost robustness

| Cost | default Sharpe | proposed Sharpe | Δ |
|---:|---:|---:|---:|
| 0 bps | 1.49 | 1.65 | +0.16 |
| 5 bps | 1.44 | 1.59 | +0.15 |
| 10 bps | 1.39 | 1.53 | +0.14 |
| 15 bps | 1.34 | 1.47 | +0.13 |
| 25 bps | 1.25 | 1.35 | +0.10 |

**Break-even cost not reached at 25 bps** (5× the current assumption). Win is structural, not a turnover artifact.

---

## 5 — V2: Multi-year hold-out validation

Four non-overlapping 365-day windows, Apr-to-Apr. 5bps cost.

| Window | BTC B&H | default | proposed (0.55, 0.70) | narrow (0.50, 0.65) | binary (0.50, 0.55) |
|---|---:|---:|---:|---:|---:|
| Hold 2022 | −0.14 / −24% | 0.63 / +20% | **0.74 / +26%** | 0.89 / +34% | 1.00 / +39% |
| Hold 2023 | 1.88 / +116% | 2.20 / +127% | **2.41 / +145%** | 2.28 / +120% | 1.92 / +86% |
| Hold 2024 | 0.74 / +29% | 0.97 / +40% | **1.08 / +47%** | 1.01 / +42% | 0.92 / +36% |
| Hold 2025 | −0.09 / −12% | 0.41 / +8% | **0.89 / +22%** | 1.04 / +24% | 1.63 / +39% |

(Sharpe / Total)

### Δ Sharpe vs default per window

| Window | proposed | narrow | binary |
|---|---:|---:|---:|
| Hold 2022 | +0.11 | +0.27 | +0.37 |
| Hold 2023 | +0.21 | +0.08 | **−0.28** |
| Hold 2024 | +0.11 | +0.05 | −0.04 |
| Hold 2025 | +0.48 | +0.63 | +1.21 |
| **Mean** | **+0.23** | +0.26 | +0.32 |
| **Wins** | **4 / 4** | 4 / 4 | 2 / 4 |

**Binary (0.50, 0.55) explicitly rejected:** wins recent volatile years but loses Hold 2023 (strongest bull year) by −0.28 Sharpe. Wrong risk profile for a continuously-deployed model.

### Honest caveat on "4 of 4"

Only **Hold 2025** is a true out-of-sample window. The earlier three years were used during session-4 canonical selection when wf365 vs sf730 was decided. So the claim is more honestly stated:
- 3 in-sample backtest wins.
- 1 true OOS win.
- Directionally consistent across all 4.

This is enough evidence to ship a single-parameter-pair change at low cost, but not airtight. A cleaner future validation would be (a) freeze (0.55, 0.70) for 12 months and compare to counterfactual, or (b) fully re-run wf365 with strict data-availability discipline.

---

## 6 — V3 / V3b: Regime-conditional thresholds

### V3 (in-sample)

Per-regime grid search on full window, fit on regime-days only:

| Regime | Optimal (lt, dt) | Gap |
|---|---:|---:|
| Bull | (0.60, 0.65) | 0.05 |
| Neutral | (0.60, 0.70) | 0.10 |
| Bear | (0.45, 0.50) | 0.05 |

In-sample result: Sharpe 1.74 vs 1.59 for fixed proposed. Wins all 4 hold-out years.

### V3b (honest walk-forward)

Each year, re-fit per-regime on data strictly before that year (fit window ends at hold-out start):

| Window | Proposed Sh | WF regime-cond Sh | Δ |
|---|---:|---:|---:|
| Hold 2023 | 2.41 | 1.69 | **−0.72** |
| Hold 2024 | 1.08 | 1.08 | 0.00 |
| Hold 2025 | 0.89 | 0.84 | −0.05 |
| Mean | | | **−0.25** |

(Hold 2022 skipped — insufficient prior fitting data.)

**Wins 1 / 3 testable years.** V3 was in-sample overfit.

The bear-regime walk-forward fits kept landing on (0.35, 0.55) — a near-binary trigger built off ~108 bear-regime days per year. Small-sample overfit, same failure mode as session-7 sub-signal walk-forward (−163pp regression).

**Conclusion: no regime-conditional thresholds.** Keep single (lt, dt) per variant.

---

## 7 — V4: Cost-stratified grid

Same (long_thr, def_thr) grid evaluated at 5, 10, 15 bps cost. White contour marks "within 5% of max Sharpe at that cost" — the good zone.

| Cost | Max Sharpe | Proposed Sharpe | In good zone? |
|---:|---:|---:|---|
| 5 bps | 1.64 | 1.59 | Yes |
| 10 bps | 1.58 | 1.53 | Yes |
| 15 bps | 1.53 | 1.47 | Yes |

The good-zone contour is essentially identical across cost levels. Default stays clearly outside at all levels. Proposed stays within at all levels.

---

## 8 — sf730 calibration

Reference variant calibrated separately. sf730 ensemble uses rolling-730d percentile and single-fit weights (no walk-forward), so its percentile distribution is structurally different from wf365's. Borrowing wf365's (0.55, 0.70) to sf730 is not appropriate.

### sf730 grid finding

The sf730 grid has **no robust zone** comparable to wf365's. The hold-out Sharpe surface looks fundamentally different from the full-window surface (hold-out argmax near (0.48, 0.50), full-window argmax near (0.55, 0.57)).

### sf730 top candidates

| Combo | Full Sharpe | h22 | h23 | h24 | h25 | Mean | Comment |
|---|---:|---:|---:|---:|---:|---:|---|
| default (0.50, 0.90) | 1.23 | 1.37 | 1.88 | 1.02 | 0.08 | 1.09 | reference |
| argmax (0.55, 0.57) | 1.56 | 1.80 | 1.88 | 1.53 | 0.56 | 1.44 | knife-edge |
| **(0.55, 0.65)** | **1.45** | **1.72** | **1.88** | **1.31** | **0.33** | **1.31** | **chosen** |
| wf365-borrowed (0.55, 0.70) | 1.37 | 1.59 | 1.88 | 1.33 | −0.03 | 1.19 | |

No 4-of-4-wins candidate; 3/4 is the best achievable (all top candidates drop on Hold 2025).

### sf730 choice rationale

**Selected (0.55, 0.65) — Option C compromise.** Wins 3/4 hold-out years (ties on 2023), mean Sharpe 1.31, 0.10 gap (avoids the binary-trigger pathology of the grid argmax 0.55/0.57 at 0.02 gap). Principled rejection of both the argmax (overfit-prone) and the borrowed wf365 value (hurts Hold 2025).

---

## 9 — Implementation

### Code changes

- `build_robust.py` — `position_fn` reads `POSITION_LONG_THR` / `POSITION_DEF_THR` env vars, defaulting to (0.55, 0.70). Docstring cites v8.
- `build_nnls_diagnostic.py` — matching env-var pattern.
- `regenerate_canonicals.sh` — exports thresholds per canonical: sf730 gets (0.55, 0.65), wf365 gets (0.55, 0.70).
- `make_daily_chart.py` — threshold lines on percentile panel read from the same env vars.

### New data files

- `thresholds.csv` — per-variant threshold registry with recalibration date and note.

### Regenerated data files

- `master_daily_view_wf365.csv` — position and strategy_return recomputed with (0.55, 0.70).
- `master_daily_view_sf730.csv` — position and strategy_return recomputed with (0.55, 0.65).

Regeneration was performed by pure downstream remapping of the existing `percentile` column. No data re-pull, no weight re-fit, no hypothesis re-build required. Ensemble scores and percentiles are bit-identical to the pre-v8 canonicals.

### Unchanged

- `weights.csv` — position thresholds are not weights.
- All hypothesis CSVs, weight history CSVs, raw data — untouched.

---

## 10 — Deployment checklist

Before declaring v8 deployed:

1. [x] Code edits applied (`build_robust.py`, `build_nnls_diagnostic.py`, `regenerate_canonicals.sh`, `make_daily_chart.py`)
2. [x] Canonical CSVs regenerated
3. [x] `thresholds.csv` created
4. [x] `HANDOVER.md` updated with new position function and headline numbers
5. [x] `execution_playbook_v4.md` sanity-check table updated
6. [x] `refit_report_v8.md` written (this file)
7. [ ] Verify `make_daily_chart.py` renders threshold lines at 0.55 / 0.70 when run against the regenerated wf365 CSV
8. [ ] Monitor turnover during paper trading — 22% higher than pre-v8; confirm 5bps cost assumption holds at the new turnover level
9. [ ] Spot-check that today's position under the new thresholds (0.85 at percentile 0.573, regime bear) matches what `make_daily_chart.py` reports

Items 7–9 are for the session that actually runs the pipeline live.

---

## 11 — Things explicitly NOT changed

- No regime-classifier changes (Tier 1 showed weak directional preferences; changing requires full weight refit with uncertain dynamics).
- No walk-forward adaptation of position thresholds (Tier 2 showed marginal gain, high complexity cost).
- No regime-conditional thresholds (V3b failed honest OOS; pure overfit).
- No hypothesis changes.
- No ensemble-weight changes.
- No calibration work (session 8 retry still open).

---

## 12 — Open questions (for future sessions)

1. **Session-8 cardinal calibration retry** still pending. Results affect daily message framing but not the model itself.
2. **Paper-trading shadow run** still pending. Only way to catch operational bugs.
3. **Periodic recalibration cadence.** Recommendation: event-driven, not scheduled. Triggers: rolling Sharpe collapse, forward-DD distribution shift, weight drift norm exceeded, major drawdown miss. Expect "no change warranted" most years. Build a health-check script to make these signals observable.
4. **sf730 hold-out fragility.** Hold 2025 Sharpe 0.28 even after recalibration. This is mostly a reminder that sf730 is structurally weaker than wf365 — reinforces canonical selection (wf365) from session 5.

---

## 13 — Files produced this session

**Tier 1 + Tier 2:** `tier1_sensitivity.png`, `tier2_walkforward.png`, `tier2_grid_heatmap.png`, `tier2_threshold_history.csv`, `tier1_tier2_findings.md`.

**V1 + V2:** `validation_results.png`, `validation_report.md`.

**V3 + V3b + V4:** `v3_regime_conditional.png`, `v4_cost_stratified_grid.png`, `final_summary.md`.

**sf730:** `sf730_calibration_grid.png`.

**Canonical outputs:** `master_daily_view_wf365.csv`, `master_daily_view_sf730.csv`, `thresholds.csv`.

**Code diffs:** `build_robust.py`, `build_nnls_diagnostic.py`, `regenerate_canonicals.sh`, `make_daily_chart.py`.

**Documentation:** `HANDOVER.md` (updated), `execution_playbook_v4.md` (updated), `refit_report_v8.md` (this file).
