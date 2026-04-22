# Refit Report v14 — Monitor + fed_funds Unpin + Wider Taper

**Session:** 14
**Date:** 2026-04-18
**Status:** Shipped. Four independent changes: wf365 position taper widened from (0.55, 0.70) to (0.45, 0.80); `fed_funds_stress_rank` unpinned in macro; `health_check.py` monitor added (reporting-only); `shadow_tracker.py` infrastructure added. `test_strong_prior.py` deleted. No changes to hypothesis builders, ensemble weights, calibration windows, or sub-signal weights.

---

## TL;DR

### Canonical numbers (v14, wf365, gross, 2026-04-17 data)

| KPI | v13 | v14 | Δ |
|---|---:|---:|---:|
| Full Sharpe (365d ann) | 1.201 | **1.242** | +0.041 |
| Hold-out Sharpe | 1.191 | **1.287** | +0.096 |
| Full total | +534% | +556% | +21.5pp |
| Hold-out total | +29% | +30.5% | +1.9pp |
| Full MaxDD | −38.1% | **−31.9%** | **+6.2pp** |
| Hold-out MaxDD | −19.3% | −18.2% | +1.1pp |
| Full AUC | 0.737 | 0.737 | unchanged |
| Hold-out AUC | 0.886 | 0.886 | unchanged |

**Today's position (2026-04-17):** regime=bear, ensemble 0.461, percentile 0.529, **position 0.77** (was 1.00 under v13). sf730 reference unchanged: position 0.49 at percentile 0.601.

Ensemble scores, percentile values, AUCs, and weight histories are all identical to v13. The improvements come from better threshold behavior, not better prediction.

---

## 1. Wider taper: (0.55, 0.70) → (0.45, 0.80)

### 1.1 What changed

Three files:

- `regenerate_canonicals.sh` — wf365 `POSITION_LONG_THR=0.45`, `POSITION_DEF_THR=0.80`. sf730 unchanged at (0.55, 0.65).
- `build_robust.py` — default env-var fallbacks updated to 0.45 / 0.80 so direct invocation without env vars gives the canonical. Position function docstring updated.
- `thresholds.csv` — wf365 row updated with v14 note.

Pipeline logic unchanged. Ensemble score, rolling percentile, weight_history, hypothesis composites — all bit-identical to v13. Only `position` and `strategy_return` in `master_daily_view_wf365.csv` change.

### 1.2 Rationale

The rolling-percentile amplification was an open item since v9, deferred through v10-v13 after four structural candidates were tested and rejected in v10. The v10 rejection was based on hold-out data through mid-2024. With extended data through 2026-04, re-testing showed the wider taper now cleanly improves multiple metrics without degrading any primary one.

Full evidence in `experiment_taper_sweep.md`. Seven taper configurations tested (narrow through extreme-wide plus full linear). Sharpe monotonically improves from narrow to very-wide then plateaus; MaxDD monotonically improves through extreme-wide; whipsaw is roughly flat across all clipped tapers and only changes significantly at full-linear (0, 1). Chose (0.45, 0.80) as the balance between "enough improvement to matter" and "not so wide that behavior becomes unintuitive."

### 1.3 Year-by-year trade

| year | BTC | prod Sh | wide Sh | Δ Sh | prod ret | wide ret | Δ ret | prod DD | wide DD | Δ DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | +60% | 1.03 | 0.96 | −0.07 | +59% | +51% | −8.2pp | −53% | −52% | +0.5pp |
| 2022 | −64% | −1.05 | −0.81 | **+0.24** | −30% | −24% | **+6.7pp** | −34% | −28% | **+5.8pp** |
| 2023 | +155% | 2.41 | 2.38 | −0.03 | +158% | +151% | −6.5pp | −20% | −20% | 0.0pp |
| 2024 | +121% | 1.81 | 1.73 | −0.07 | +95% | +86% | **−9.0pp** | −27% | −26% | +1.2pp |
| 2025 | −6% | 0.60 | 0.67 | +0.07 | +16% | +18% | +2.4pp | −26% | −25% | +1.1pp |
| 2026 | −12% | 1.06 | 1.24 | +0.18 | +5% | +5% | +0.5pp | −5% | −5% | +0.2pp |

Pattern: gives up modest upside capture in strong bull years (2021, 2023, 2024) to gain meaningful protection in bear/choppy years (2022, 2025, 2026). MaxDD is never worse under wide; in 2022 it is 5.8pp better. Cumulative total-return effect is +21.5pp.

### 1.4 Robustness check

Across 1388 rolling 1-year windows: wide > production in 58% of windows. Mean Δ Sharpe +0.053, std 0.151. Worst window: −0.23 Sharpe; best: +0.47. Not monotone dominance, but a bounded positive bias with the expected shape — wide wins in bears, loses modestly in sustained bulls.

### 1.5 What was NOT re-investigated

The v10 investigation tested sigmoid position functions, widened taper, and multi-window percentile. This session tested only widened linear-hybrid taper. Sigmoid and multi-window variants remain as-rejected from v10 and not considered here.

---

## 2. `fed_funds_stress_rank` unpinned

### 2.1 What changed

`build_macro_equities.py` — removed `"fed_funds_stress_rank"` from the `PINNED_DIRECTION` set. Comment in the source notes this is a v14 change and why. Four other macro signals (spx_overext_rank, vix_z90_rank, hy_spread_roc_rank) remain pinned.

### 2.2 Effect today: none

Pinning in `auc_excess_weights` only changes behavior when raw calibration AUC < 0.5. Then it prevents auto-flip. Otherwise pinned and unpinned produce identical outputs.

`fed_funds_stress_rank`'s raw calibration AUC on the production 2018-onward window is 0.5054 — just above 0.5. Auto-flip does not trigger whether pinned or unpinned.

Verified empirically: max weight difference across all macro sub-signals is exactly 0.0000, zero flips change, zero AUCs change. The macro composite is bit-identical. All downstream numbers identical.

### 2.3 Why ship a no-op

Prospective safety. If `fed_funds_stress_rank`'s calibration AUC drifts below 0.5 in a future refit (its hold-out AUC is already 0.07, so this is plausible), the pre-change code would have continued floor-weighting it in an assumed direction. The post-change code will auto-flip it so it contributes in whatever direction the data says.

The underlying prior — "tightening = risk-asset stress" — is empirically contested on recent data. Removing it from `no_flip` reflects that we no longer confidently hold that prior. Details in `memo_v14_unpin_and_min_calib_test.md`.

### 2.4 What was tested and rejected

Tightening macro's `MIN_CALIB` from 2018-10-01 to 2021-06-30 was tested alongside the unpin. Rejected: hold-out Sharpe 1.19 → 0.95, today's position 1.00 → 0.43. The 2018-onward calibration window was accidentally keeping fed_funds at floor weight (3%) rather than giving it 17.3% weight based on its 2021+ AUC of 0.59. Accidental but real insurance. Do not tighten. Full analysis in the memo.

---

## 3. Health-check monitor (`health_check.py`)

### 3.1 What changed

New file: `health_check.py`. Reads `master_daily_view_wf365.csv` + the six `hypothesis_*.csv` files. Computes per-signal in-sample / hold-out / rolling AUCs against `y_60`. Flags signals with hold-out AUC ≤ 0.50 or IS-OOS delta > 0.15. Writes `health_check.csv`.

**Read-only.** Does not mutate `PINNED_DIRECTION` / `no_flip` / pipeline state. Three parameters (rolling window days, AUC threshold, delta threshold), all a priori-justified, at the 3-parameter budget.

### 3.2 Validation on v14 canonical

Flags 10 of 42 signals: etf_flows composite (OOS 0.479), sub_fed_funds_stress_rank (OOS 0.072), and 8 others. Correctly does NOT flag classic_cycle pinned-contrarian sub-signals (where pinning was correct — IS<0.5 but OOS>0.5). Correctly does NOT flag macro_equities composite (IS-OOS delta 0.122 stays below 0.15 threshold by design).

### 3.3 Decision-system backtest: rejected automation

Attempted to backtest the monitor as an auto-action system — drop flagged sub-signals each month and renormalize. Initial 24-month test looked promising for an `oos_persist2` rule (Sharpe 1.05 → 1.36 appeared). Extended to 40 months with proper train/test OOS rule selection: no rule beat baseline on train; forced train-winner (`naive`) lost 0.60 Sharpe on test.

The apparent persist-2 edge was a 2025-2026 phenomenon not visible on the earlier 2023-2024 half of the window, and a taper-amplification investigation further showed that much of it was interaction with the rolling-percentile taper rather than signal quality. See `oos_rule_selection_memo.md`, `backtest_monitor_report.md`, and `session_synthesis_and_recommendation.md` for full rationale.

**Do not automate.** Monitor ships as reporting-only. Shadow-track candidate rules for 2+ quarters before revisiting.

---

## 4. Shadow tracker (`shadow_tracker.py` + `shadow_state.csv`)

### 4.1 What changed

New files. `shadow_tracker.py` is a monthly-runnable script that computes what three candidate decision rules (`naive`, `oos_persist2`, `oos_persist3`) would have decided at each historical monthly refit, and writes the ensemble score, percentile, and position (at both production-taper and wide-taper variants) for each rule alongside baseline. Read-only with respect to pipeline state.

Cosmetic note: the dual-taper output (production 0.55/0.70 and wide 0.45/0.80) was added when the taper change was still experimental. With v14 shipping the wide taper, the "production" column in the shadow output IS the wide taper and the "wide" column is redundant. Cosmetic cleanup for a future session; does not affect correctness.

### 4.2 Purpose

Accumulate forward out-of-sample evidence on whether any decision rule earns promotion from "reporting" to "acting." The OOS rule-selection test this session used a fixed 40-month window; extending with 6+ more months of forward data would meaningfully strengthen or kill the case for any rule.

---

## 5. `test_strong_prior.py` deleted

Dead scaffolding for a two-tier pinning formula (`strong_prior` kwarg) that was proposed but never adopted into production. Test crashed on current `common.py` with `TypeError: auc_excess_weights() got an unexpected keyword argument 'strong_prior'`. Codebase-wide grep for `STRONG_PRIOR` returned matches only inside the test file itself. Deletion is clean and safe.

---

## 6. Files modified / added / removed

### Modified
- `build_macro_equities.py` — fed_funds unpinned, docstring updated
- `build_robust.py` — default threshold fallbacks 0.55/0.70 → 0.45/0.80, docstring updated
- `regenerate_canonicals.sh` — wf365 thresholds 0.55/0.70 → 0.45/0.80 with v14 comment
- `thresholds.csv` — wf365 row updated with v14 note
- `master_daily_view_wf365.csv` — regenerated under new taper (position + strategy_return columns change; everything else identical)
- `HANDOVER.md` — status, sanity-check targets, today's call, position function, open items, package integrity all updated

### Added
- `health_check.py`, `health_check.csv` (example output)
- `shadow_tracker.py`, `shadow_state.csv` (example output)
- `refit_report_v14.md`
- `memo_v14_unpin_and_min_calib_test.md`, `experiment_taper_sweep.md`, `experiment_shrinkage.md`, `oos_rule_selection_memo.md`, `backtest_monitor_report.md`, `session_synthesis_and_recommendation.md` (context memos)
- `health_check_history.csv`, `health_check_history_extended.csv`, `monitor_backtest.csv`, `min_calib_experiment.csv`, `taper_sweep_results.csv`, `shrinkage_results.csv`, `shrinkage_v2_results.csv` (experiment data)
- `taper_sweep.py`, `shrinkage_experiment.py`, `shrinkage_v2.py` (experiment code, auditability)

### Removed
- `test_strong_prior.py`

### Not modified (reaffirmed)
- `common.py`, `build_foundation.py`, other `build_*.py` hypothesis builders, `pull_all_raw_data.py`, `pull_artemis_etf.py`, `export_csvs.py`, `run_all.sh`, `weights.csv`, `master_daily_view_sf730.csv`, all `weight_history_*.csv`, all `hypothesis_*.csv`

---

## 7. Open items (updated)

See HANDOVER.md for the authoritative list. Summary of changes this session:

- Item "rolling-percentile amplification" (was implicit in v13 §9) **resolved via wider taper**.
- Item "automated roster monitor" (#7) **MVP shipped** as reporting-only; decision-system automation **decisively rejected** via OOS rule-selection test.
- Item "test_strong_prior.py" **resolved** (deleted).
- Item "sub_fed_funds_stress_rank" (#8) **partial resolution** — unpinned as dead-prior cleanup (no-op today), watch over next 2-3 refits.
- New item: **shadow-track decision rules for 2+ quarters** before reconsidering monitor automation.
- New item: **static sub-signal weights** — structural question flagged; multiple fixes attempted and rejected this session (MIN_CALIB tightening, shrinkage, walk-forward refit sketch). Future approaches include annual refit or a calibrated probabilistic replacement. Multi-session project.

---

## 8. Caveats

- **Wide taper validated on 4.8 years of data**, with hold-out = 1 year. The 2022 protection is the single biggest empirical case; if a future bear year shows the pattern does not generalize, revisit.
- **58% of rolling 1-year windows favor wide** — not dominant, but consistent positive mean with bounded downside.
- **Sub-signal weights remain static.** Known issue, flagged but not fixed this session. Attempted fixes rejected on evidence. Future structural project.
- **Shadow-tracker dual-taper output** is cosmetically redundant now that wide is production. Cleanup for next session.
- **Paper-trading validation** is an open item in HANDOVER and remains a precondition for real capital deployment. The v14 changes shipping here affect that validation because today's position changes 1.00 → 0.77. Operators should note this.
