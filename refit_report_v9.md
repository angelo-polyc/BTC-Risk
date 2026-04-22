# Refit Report — v9 (Reproducibility + Sensitivity Audit)

**Date:** 2026-04-16
**Scope:** Full hypothesis-layer refit from raw data, diagnosis of a handover inconsistency, sensitivity characterization of the v8 position thresholds, and exploration of an alternative threshold candidate. **No canonical model state changed.**

> **⚠️ Note:** An earlier version of this report (drafted mid-session) made two incorrect claims that were corrected later the same session: (1) that the shipped `build_robust.py` had lost its walk-forward logic and could not regenerate the canonical, and (2) that the 252-line walk-forward file was a reimplementation from §13 spec rather than a recovery. Both claims were wrong. The actual story is below.

**Outcome:**
- **Reproducibility confirmed.** Hypothesis builders reproduce bit-exact from `raw_data_export.csv`. The 252-line `build_robust.py` reconstructs `master_daily_view_wf365.csv` bit-exact (ensemble_score max Δ 9.2e-7, percentile 5.0e-7, position 3.6e-6) when fed master's embedded hypothesis columns.
- **Handover inconsistency identified.** Standalone `hypothesis_*.csv` files are stale relative to master. Macro_equities diverges by up to 0.110 on 2,977 days; etf_flows by up to 0.337 on 405 days. The master CSV's embedded hypothesis columns are the authoritative pipeline inputs.
- **Sensitivity finding.** Small input drift → disproportionately large realized-strategy drift. Canonical hold-out Sharpe changes by 19% under one specific drift scenario, mediated primarily by the rolling-percentile transform (3× median / 123× 99th-percentile amplification of ensemble_score perturbations).
- **Alternative (0.40, 0.65) threshold explored and rejected for this session.** Wider taper zone reduces sensitivity by ~20× but fails v8's 4/4 hold-out-year criterion (wins 2/4). Documented as the primary candidate for the post-paper-trading refit.
- **No code or threshold change deployed.** v8 canonical state preserved.

---

## 1 — Motivation

Session opened with "refit the entire model from scratch and compare against the canonical." Path chosen: reconstruct the raw-data layer from the shipped `raw_data_export.csv`, run the foundation + six hypothesis builders + ensemble builder, diff against the canonical CSVs. No tuning was intended.

## 2 — Raw-layer reconstruction and hypothesis reproducibility

46 per-series parquets reconstructed from `raw_data_export.csv`. Velo data re-melted to long format with `velo_type` inferred from exchange name. CFTC renamed to builder-expected path. Row counts matched `data_inventory.csv` exactly.

Hypothesis builder outputs vs shipped `hypothesis_*.csv`:

| Hypothesis | Max \|Δ\| composite | Days \|Δ\|>1e-6 | Verdict |
|---|---:|---:|---|
| cme | 5.0e-7 | 0 | bit-perfect |
| classic_cycle | 9.5e-7 | 0 | bit-perfect |
| etf_flows | 5.0e-7 | 0 | bit-perfect |
| crypto_derivatives | 1.4e-5 | 2 | bit-perfect modulo edge drift |
| eth | 1.9e-4 | 2 | bit-perfect modulo edge drift |
| macro_equities | 8.4e-3 | 4008 | FRED revision drift |

The macro drift concentrates in three FRED series that get revised historically (T10Y2Y, DFF, BAMLH0A0HYM2). Sub-1 bp impact on recent ensemble scores.

## 3 — The `build_robust.py` confusion and its resolution

Shipped `/mnt/project/build_robust.py` (122 lines) contains no walk-forward logic and does not read `WALK_FORWARD`, `PERCENTILE_WINDOW`, or related env vars. `regenerate_canonicals.sh` exports those env vars but the shipped script silently ignores them.

**Mid-session, the wrong conclusion** was that walk-forward logic had been lost and needed to be recovered or rebuilt. A reimplementation (the 252-line walk-forward file) was tested and failed to bit-exactly reproduce the shipped canonical when fed the standalone hypothesis CSVs.

**Correct conclusion** (after another session's diagnosis): the 252-line file IS the correct pre-v11 `build_robust.py`. The shipped 122-line file is an earlier version that predates the walk-forward addition. The reason the 252-line file appeared not to reproduce the canonical was that it was being fed stale hypothesis inputs (see §4). When fed master's embedded hypothesis columns — which are the same inputs the shipped canonical was built from — it reconstructs to CSV-rounding precision.

**Required project action:** `/mnt/project/build_robust.py` should be replaced with the 252-line walk-forward file. This session ships that replacement as a deliverable.

## 4 — Stale standalone hypothesis CSVs

The shipped `master_daily_view_wf365.csv` and the shipped standalone `hypothesis_*.csv` files are **not self-consistent**. They were produced by different pipeline runs at different times:

| File | Diff vs master embedded col | Days with \|Δ\|>0.01 |
|---|---:|---:|
| hypothesis_macro_equities.csv | max 0.110 | 2,977 |
| hypothesis_etf_flows.csv | max 0.337 | 405 |
| hypothesis_crypto_derivatives.csv | max 0.014 | 1 |
| hypothesis_cme.csv | max 0.000 | 0 |
| hypothesis_classic_cycle.csv | max 0.000 | 0 |
| hypothesis_eth.csv | max 0.001 | 0 |

Plus: master ends 2026-04-15 (with a gap at 2026-04-14); standalone files end 2026-04-14 (no gap). That's a one-day calendar mismatch.

**Practical consequence.** If the standalone files are fed into `build_robust.py` as pipeline inputs, the resulting model diverges from canonical on 5.3% of days at the position level, with full-window Sharpe 1.069 vs canonical 1.168 (−8.5%) and hold-out Sharpe 0.789 vs 0.916 (−14%). This is not cosmetic.

**Operational rule:** master's embedded `<hyp>_score` columns are the authoritative pipeline inputs. Standalone `hypothesis_*.csv` files are **reference/diagnostic artifacts for sub-signal attribution**, not inputs. They should be marked as such in HANDOVER.md.

## 5 — Sensitivity analysis: how much does realized strategy drift under input perturbation?

Using master-embedded columns vs standalone files as two "versions of the truth":

**KPI comparison (post-5bps, y_60, wf365):**

| KPI | Scenario A (standalone) | Scenario B (master) | Absolute Δ | Relative Δ |
|---|---:|---:|---:|---:|
| Full Sharpe | 1.069 | 1.168 | +0.099 | +9.3% |
| Hold-out Sharpe | 0.789 | 0.916 | +0.127 | +16.1% |
| Total return (full) | +389.8% | +487.0% | +97.2 pp | +24.9% |
| Max DD (full) | −38.8% | −38.5% | +0.3 pp | +0.8% |
| Days w/ position Δ > 0.1 | 455 / 4,227 = 10.8% | — | — | — |
| Days w/ opposite direction | 185 / 4,227 = 4.4% | — | — | — |

**AUC comparison (ranking quality, not strategy return):**

| Window | Scenario A | Scenario B | Δ% |
|---|---:|---:|---:|
| Full | 0.7118 | 0.7112 | −0.1% |
| In-sample | 0.8205 | 0.8190 | −0.2% |
| Hold-out | 0.7873 | 0.7733 | −1.8% |

**The key finding.** AUC — the model's prediction quality — is essentially unchanged (1.8% max delta on hold-out). But strategy Sharpe changes by 16% on hold-out. There's a ~9–30× amplification between prediction-layer drift and action-layer drift.

## 6 — Amplification mechanism

Stage-by-stage analysis of how ensemble_score perturbations propagate:

| Stage | Mean \|Δ\| | Median amplification | 99th amplification |
|---|---:|---:|---:|
| ensemble_score | 0.011 | — | — |
| percentile (rolling-365) | 0.037 | **3.0×** | **123×** |
| position (piecewise linear) | 0.048 | 1.3× | 6.67× |

The **rolling percentile transform** is the amplifier, not the threshold function. On a typical day, 1 unit of ensemble_score change produces 3 units of percentile change; on 1% of days, 123 units. The piecewise-linear position function behaves exactly as designed (max slope 1/(dt−lt) = 6.67 in the taper zone).

Kendall tau between scenario A and scenario B ensemble_score within rolling 365-day windows has median 0.937 — meaning 6–7% of pairwise day rankings flip between scenarios. When those flips land in the taper zone (0.55–0.70 percentile), positions change materially.

Position disagreement concentrates in the taper zone:

| B-percentile band | n days | Mean \|Δ position\| |
|---|---:|---:|
| <0.50 (safely long) | 785 | 0.013 |
| 0.50–0.55 (long boundary) | 65 | 0.076 |
| **0.55–0.70 (taper)** | **229** | **0.214** |
| 0.70–0.80 (def boundary) | 175 | 0.051 |
| >0.80 (safely defensive) | 495 | 0.022 |

294 of 1,749 days (17%) sit in or adjacent to the taper zone. Those are where the realized P&L divergence concentrates.

**Structural diagnosis.** AUC is rank-invariant over the full dataset. The rolling-365 percentile is rank-dependent within a window. A score shift that's monotone in the full dataset can substantially change within-window ranks. Consequence: the quality metric (AUC) and the decision rule (rolling-percentile threshold) don't respond to the same invariances in the underlying signal. This is a structural design issue, not a bug. Fix classes considered: (a) widen taper zone, (b) ensemble multiple percentile windows, (c) sigmoid position on absolute ensemble_score, (d) calibrate ensemble_score to drawdown probability. All deferred behind paper trading.

## 7 — Threshold sensitivity grid + alternative candidate

Swept (long_thr ∈ {0.40, 0.45, 0.50, 0.55}) × (def_thr ∈ {0.65, 0.70, 0.75, 0.80, 0.85, 0.90}), 24 points. Grid saved at `grid_sensitivity.csv`.

Multi-year hold-out Sharpe (canonical inputs, post-5bps):

| long | def | width | Full | h2023 | h2024 | h2025 | h2026 | Wins/4 |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 0.55 | 0.70 | 0.15 | **1.168** | 0.735 | **2.408** | **1.080** | 0.916 | — (current) |
| 0.55 | 0.65 | 0.10 | 1.126 | 0.836 | 2.376 | 1.050 | 0.790 | 1/4 |
| 0.40 | 0.65 | 0.25 | 1.069 | 0.932 | 2.090 | 0.874 | **1.078** | 2/4 |
| 0.50 | 0.90 | 0.40 | 1.002 | 0.629 | 2.198 | 0.966 | 0.434 | 0/4 (pre-v8) |

Sensitivity (|ΔSharpe|) across candidates:

| Candidate | \|ΔS_full\| | \|ΔS_hold-out\| |
|---|---:|---:|
| Canonical (0.55, 0.70) | 0.096 | 0.176 |
| Alt mild (0.55, 0.65) | 0.038 | 0.055 |
| Alt big (0.40, 0.65) | **0.004** | **0.036** |

**Alt big (0.40, 0.65) characteristics:**
- Sharpe cost: 1.168 → 1.069 on full-window (−8.5%); 0.916 → 1.078 on hold-out (+18%)
- Sensitivity: 20× reduction full-window, 5× reduction hold-out
- Hold-out wins: 2/4 (h2023 and h2026, i.e. the weakest years for canonical)
- Per-regime: canonical wins bull (0.88 vs 0.62) and neutral (2.31 vs 2.14); alt big wins bear (0.39 vs 0.26)
- Position behavior: 19% partial days vs canonical's 12%; less binary

**Decision: do not ship.** Reasons:
1. Fails v8's 4/4 hold-out criterion (wins 2/4).
2. Loses full-window Sharpe 8.5% — real cost against regimes that have historically been dominant.
3. Paper trading isn't yet done. HANDOVER's "no real capital before 4+ weeks of clean paper trading" still stands; a threshold change now would force paper-trading the new threshold, which extends the deployment timeline.
4. The root cause (rolling-percentile amplification) is structural. Widening the taper zone is a mitigation, not a structural fix. If a structural fix is warranted, a sigmoid on absolute ensemble_score is probably the right move, not a grid-searched threshold.

**Alt big is documented as the primary candidate** for the next scheduled refit session (post-paper-trading), contingent on paper trading showing canonical's sensitivity materializing in live P&L.

## 8 — What did NOT change this session

- No changes to `build_foundation.py`, hypothesis builders, `common.py`, or the regime classifier.
- No weight changes, no threshold changes, no label changes.
- No regeneration of canonical CSVs. `master_daily_view_*.csv` and `weights.csv` untouched.
- No tuning against hold-out. Grid results are characterization, not optimization targets.
- No fresh API pull. `credentials.md` was supplied but Path-B not executed.

## 9 — Deliverables

1. **`build_robust.py` (252 lines, pre-v11 recovered)** — replaces the shipped 122-line file. Verified to reconstruct `master_daily_view_wf365.csv` bit-exact.
2. **`HANDOVER_PATCH.md`** — specific edits to `HANDOVER.md`.
3. **`NEXT_SESSION_PLAN.md`** — guidance for the visualization-only next session.
4. **`grid_sensitivity.csv`** — full 24-point threshold grid with performance and sensitivity metrics.
5. **Analysis scripts** preserved: `grid_sensitivity.py`, `full_comparison.py`, `chart_positions_v2.py`. Reusable in next refit session.
6. **`btc_position_comparison.png`** — canonical vs alt-big position visualization, for reference in the next visualization session.

## 10 — Open items (revised)

Unchanged from HANDOVER.md except item 7 is retracted:

1. Paper trading shadow run (4+ weeks) — still blocking real deployment.
2. Cardinal calibration retry (session-8 plan in `refit_report_v7.md` §3).
3. Operational comms runtime (scheduler, trigger evaluator, event-alert charts, dedup, agent wiring).
4. Operational hygiene. **Add:** regenerate standalone hypothesis CSVs from the same run that produces master, or mark them reference-only; consolidate `fix_parsers.py` into `pull_all_raw_data.py`; refresh stale ETF premium endpoint (last data 2026-01-06); data-freshness monitoring.
5. `OPERATIONS.md` — daily/weekly runbook.
6. Annual health-check script. **Add:** include a FRED-revision check on macro sub-signals; include a standalone-vs-master drift check.
7. ~~Recover or rebuild walk-forward logic in `build_robust.py`.~~ **Retracted** — file recovered; swap shipped in this session's deliverables.

**New for future consideration** (not urgent, deferred behind paper trading):

- Structural fix for rolling-percentile amplification. Candidates: widened threshold (alt big: 0.40/0.65), multi-window percentile average, sigmoid on absolute ensemble_score, probability calibration. First clean refit opportunity after paper trading completes.

## 11 — Security note

`credentials.md` (uploaded mid-session) is in project knowledge and searchable. Consider deleting the uploaded file and/or rotating the keys to env vars per the file's own security section.
