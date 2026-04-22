# Refit Report v12 — ETH Hypothesis Removed from Ensemble

**Session:** 12 (same calendar session as v11)
**Date:** 2026-04-17
**Status:** Shipped. ETH hypothesis removed from canonical ensemble. `build_eth.py` and `hypothesis_eth.csv` retained for per-hypothesis health-check monitoring; the ensemble_score in `master_daily_view_wf365.csv` now sums across 5 hypotheses instead of 6.

---

## TL;DR

User-directed structural change: drop the ETH hypothesis from the ensemble. Motivation is structural, not backtest-driven — the hypothesis's predictive contribution during 2024–2025 came dominantly from ETH-rotation dynamics (ETH euphoria pulling capital from BTC), which is a period-specific phenomenon rather than a stable crypto-structure feature.

v12 canonical numbers (wf365, 5-hypothesis, gross, data through 2026-04-17):

| KPI | v11 (6-hyp) | v12 (5-hyp) | Δ |
|---|---:|---:|---:|
| Full Sharpe | 0.96 | **1.09** | +0.13 |
| Hold-out Sharpe | 0.90 | **1.15** | +0.26 |
| Full total | +288% | +359% | +71pp |
| Hold-out total | +20% | +28% | +8pp |
| Full MaxDD | −36.7% | −30.2% | +6.5pp (better) |
| Hold-out MaxDD | −19.8% | −21.0% | −1.3pp (worse) |
| Full AUC | 0.727 | 0.720 | −0.007 |
| Hold-out AUC | 0.899 | 0.884 | −0.015 |
| Annual turnover | 42.5 | 33.2 | −9.3 |

**Today's position (2026-04-17):** wf365 goes from 0.45 to **1.00** (fully long). sf730 reference also flips from 0.00 to 1.00. Regime remains bear (D2h classifier unchanged).

## 1 — Reasoning

### 1.1 The structural argument

ETH hypothesis sub-signals measure derivatives-market stress in ETH: funding divergence, speculation ratio, liquidation stress, funding z-score, CVD divergence, BTC dominance, basis compression. These were originally motivated as leading indicators of broader crypto deleveraging — when ETH derivatives got stressed, BTC typically followed within 30–60 days.

Through 2024 and into 2025, a different mechanism dominated: ETH ran a distinct bull cycle while BTC traded sideways or drew down. The ETH derivatives signals elevated not because of systemic crypto stress but because of ETH-specific euphoria (high funding, high speculation, compressed basis). The hypothesis correctly predicted BTC drawdowns in this period — but via the asset-rotation mechanism, not the systemic-deleveraging mechanism it was designed around.

Asset-rotation dynamics between specific cryptos are period-specific. The 2024–2025 "ETH season" is not a structural feature of crypto markets — it's a phase of relative flow. Training the ensemble to rely on this relationship going forward is teaching it a correlation that may not recur.

### 1.2 The backtest evidence (and its honest caveats)

**What dropping ETH improves:**
- Full-window Sharpe +0.13 (0.96 → 1.09)
- Hold-out Sharpe +0.26 (0.90 → 1.15)
- Full MaxDD improves 6.5pp (−36.7% → −30.2%)
- Annual turnover drops 22% (42.5 → 33.2)
- Calendar-year wins: 5 of 6 years (2021-H2, 2022, 2024, 2025 loss, 2026-YTD)
- Apr-to-Apr window wins: 3 of 4 years (h2023, h2024, h2026; loses h2025)

**What dropping ETH costs:**
- **h2025 Sharpe drops 0.36** (1.14 → 0.78). This is the single biggest honest-caveat data point. The 2024-04 → 2025-04 window was a 64%-bull-regime year with BTC flat-to-down and a late-period drawdown. The 6-hyp model's ETH-derived defensive signals timed that drawdown well; the 5-hyp model missed it.
- In-sample AUC drops 0.027 (0.747 → 0.720), hold-out AUC drops 0.015 — small but real losses in prediction quality.
- 2025-calendar MaxDD worsens 12pp (−14.5% → −26.1%).

**Why we're accepting the h2025 cost:** the user's structural argument implies h2025's ETH benefit was coincidental (ETH rotation correlating with BTC softness), not mechanism-based. If that's true, h2025's ETH signal wasn't predictive of what ETH signals will do in future rotations. Going forward, the 5-hyp model's Sharpe-superior behavior in 2022 (100% bear), h2023 (partial bear), 2024 (100% bull-neutral), and 2026-YTD (100% bear) is more representative of typical regimes than h2025's specific rotation dynamic.

**Where this reasoning could be wrong:** if ETH rotation is a recurring feature of crypto cycles (not period-specific), then we've thrown away a useful predictor. The only way to find out is live observation. This is reflected in the open-items update below.

### 1.3 Why not drop ETF Flows instead

ETF Flows has lower hold-out AUC (0.337) than ETH (0.683), making it the first candidate for removal on AUC grounds. But v10's session notes explicitly kept ETF Flows on structural grounds: real ETF volume share is a genuine post-2024 BTC-market-structure feature with vendor-independent data flow (Artemis + Coinglass V4 hybrid). ETF Flows stays weak on AUC but represents a structurally durable signal; ETH was strong on AUC via a structurally questionable mechanism. The criterion for removal is structural defensibility, not AUC rank.

## 2 — Implementation

### 2.1 Code change

One line in `build_robust.py` — the `HYPOTHESES` list now excludes `("eth", "eth.parquet")`. The ensemble's AUC-excess weight fitting, normalization, and per-regime application all work through the reduced list automatically. No other code change required.

### 2.2 What still runs

- `build_eth.py` still runs in `regenerate_canonicals.sh` and produces `data/hypotheses/eth.parquet`.
- `hypothesis_eth.csv` is still regenerated by the pipeline run.
- `eth_score` column is still embedded in `master_daily_view_wf365.csv` and `master_daily_view_sf730.csv` by `export_csvs.py`.
- All ETH sub-signals continue to be computed.

Purpose: per-hypothesis health-check monitoring (open item #6). If ETH's hold-out AUC stabilizes or improves in future observations (suggesting the rotation mechanism is stable), the hypothesis can be re-added with a one-line change. If it continues to degrade or behave erratically, the removal is vindicated.

### 2.3 What does not run / is absent

- ETH no longer contributes to `ensemble_score`.
- `weights.csv` now has 5 hypothesis columns (macro_equities, cme, crypto_derivatives, classic_cycle, etf_flows) × 3 regimes × 2 variants × 2 labels = 12 rows (same row count as v11).
- `weight_history_wf365_y_{60,30}.csv` now 690 rows (was 828 — 46 fits × 3 regimes × 5 hypotheses).

## 3 — Weight redistribution

Bear regime (where today's call lives), wf365 y_60:

| Hypothesis | v11 (6-hyp) | v12 (5-hyp) | Δ |
|---|---:|---:|---:|
| macro_equities | 0.015 | 0.022 | +0.007 |
| cme | 0.348 | 0.495 | **+0.147** |
| crypto_derivatives | 0.015 | 0.022 | +0.007 |
| classic_cycle | 0.092 | 0.130 | +0.038 |
| etf_flows | 0.233 | 0.331 | **+0.098** |
| eth | 0.297 | — | removed |

ETH's 29.7% bear-regime weight primarily migrated to CME positioning (+14.7pp) and ETF Flows (+9.8pp). CME had bear-regime hold-out AUC 0.788 (strongest single-hypothesis predictor); ETF Flows is structurally the "real BTC-specific" feature. Both are defensible recipients of the weight transfer.

Bull regime weights are almost unchanged since ETH carried less weight there. Neutral regime redistribution is also small. The change is concentrated in bear regime, which is where today lives — hence today's sharp position flip.

## 4 — Annual breakdown (v12 canonical vs v11 baseline)

| Year | Regime mix | v11 Sharpe / Total | v12 Sharpe / Total | Δ Sharpe |
|---|---|---:|---:|---:|
| 2021 H2 | 18/25/56 | 0.56 / +7% | **1.13 / +20%** | +0.56 |
| 2022 | 0/0/100 | −0.98 / −28% | **−0.70 / −19%** | +0.28 |
| 2023 | 35/31/34 | 2.36 / +155% | 2.27 / +135% | −0.09 |
| 2024 | 50/50/0 | 1.23 / +51% | **1.51 / +68%** | +0.29 |
| **2025** | **64/24/12** | **1.05 / +35%** | **0.57 / +14%** | **−0.48** |
| 2026 YTD | 0/0/100 | −0.54 / −3% | **1.04 / +5%** | +1.59 |

2025 is the lone meaningful loss and is the honest caveat this decision stands on.

## 5 — Position behavior

### Today's call (2026-04-17)

| Variant | Regime | Percentile | Position |
|---|---|---:|---:|
| wf365 v11 (6-hyp) | bear | 0.633 | 0.45 |
| **wf365 v12 (5-hyp, CANONICAL)** | bear | **0.406** | **1.00** |
| sf730 v11 (6-hyp) | bear | 0.720 | 0.00 |
| sf730 v12 (5-hyp) | bear | 0.473 | 1.00 |

Both variants agree on fully-long positioning on the v12 model. Regime classifier unchanged (D2h, bear). ETH was driving ~23 percentile points on wf365 and ~25 points on sf730 today — removing it flips both variants' percentile under the 0.55 long threshold.

### Full-window position behavior

| Metric | v11 (6-hyp) | v12 (5-hyp) |
|---|---:|---:|
| Avg position | 0.560 | 0.559 |
| % days fully defensive | 39.5% | 34.6% |
| % days fully long | 51.8% | 57.0% |
| % days in taper zone | 8.7% | 8.4% |

v12 spends ~5pp more days fully long and 5pp fewer days fully defensive. Taper-zone time is essentially unchanged — the amplification problem isn't resolved by this change.

## 6 — Sanity check targets (v12)

For a canonical run with `CALIB_LABEL=y_60` on data pulled 2026-04-17 or later, wf365 thresholds (0.55/0.70), sf730 thresholds (0.55/0.65), gross:

| Metric | sf730 target | wf365 target |
|---|---|---|
| Hypothesis hold-out AUCs (5) | Macro 0.513, CME 0.788, CD 0.632, CC 0.752, ETF 0.337 | same |
| Ensemble IS AUC | 0.78 ± 0.02 | 0.72 ± 0.02 |
| Ensemble OOS AUC | 0.88 ± 0.03 | 0.88 ± 0.03 |
| Strategy Sharpe (full) | 1.28 ± 0.05 | 1.09 ± 0.05 |
| Strategy total (full) | +612% ± 40pp | +359% ± 40pp |
| Strategy max DD (full) | −30% ± 3pp | −30% ± 3pp |
| Strategy Sharpe (hold-out) | 1.37 ± 0.10 | 1.15 ± 0.10 |
| Strategy max DD (hold-out) | −12% ± 3pp | −21% ± 3pp |
| B&H (full) | Sharpe 0.56, +111%, −77% | same |

## 7 — What did NOT change

- `build_foundation.py` (D2h regime classifier).
- The five retained hypothesis builders (`build_macro_equities.py`, `build_cme.py`, `build_crypto_derivatives.py`, `build_classic_cycle.py`, `build_etf_flows.py`).
- `build_eth.py` — still runs; feeds reference-only `hypothesis_eth.csv` for health-check monitoring.
- `common.py`, position thresholds, calibration labels, pinning sets, ensemble weighting formula.
- `pull_all_raw_data.py`, `pull_artemis_etf.py`, `fix_parsers.py`.
- Data — pulls from v11 session still current.

## 8 — Open items (updated)

1. **Paper trading shadow run — CLOCK RESETS.** v12 is a structural change. The 4+ week paper-trading requirement for real-capital deployment starts over at 2026-04-17 on the 5-hypothesis wf365 canonical. Both variants (wf365 canonical, sf730 reference) should be logged in parallel.
2. **Cardinal calibration retry.** Unchanged.
3. **Operational comms runtime.** Unchanged.
4. **Operational hygiene.** Unchanged from v11. v12 adds: monitor per-hypothesis ETH AUC over time — if it stabilizes in the 0.5–0.6 range across rolling windows, the hypothesis is producing random noise and removal is vindicated; if it ticks back up consistently in non-rotation periods, the removal deserves reconsideration.
5. `OPERATIONS.md`. Unchanged.
6. **Annual health-check script.** Add: per-hypothesis AUC drift monitoring specifically for ETH (removed but observable), with trigger logic for re-adding if AUC behavior suggests the 2024–2025 rotation was anomalous.
7. **Deferred: structural fix for rolling-percentile amplification.** Unchanged. ETH removal doesn't address this; the taper-zone amplification mechanism is still present.

## 9 — Reversibility

Adding ETH back is a one-line change: uncomment the `("eth", "eth.parquet")` line in `build_robust.py`'s `HYPOTHESES` list and re-run `regenerate_canonicals.sh`. All v11 outputs bit-reproducible since build_eth.py still runs. Decision criterion for re-adding: if ETH's rolling 365-day hold-out AUC stays >0.6 across 3+ future quarters in non-rotation periods.

## 10 — Record of caveats

For the next session instance, the honest case against this change:

- h2025 Sharpe loss of −0.36 is the single cleanest OOS test the model had, and 6-hyp won it. The justification for doing this anyway rests entirely on the structural argument that h2025's ETH signal was period-specific rotation rather than generalizable. If that's wrong, v12 is wrong.
- Hold-out AUC dropped 0.015. Not much in absolute terms, but the ensemble's prediction quality is measurably (if modestly) lower.
- Removing a hypothesis based on one session's reasoning is the kind of structural change the project's "prefer a priori / convention-based parameter choices" discipline is cautious about. This is not that discipline failing — it's the discipline being overridden by a specific structural argument. The override deserves to be evaluated against paper-trading results.
- Today's 0.45 → 1.00 position flip is consequential. If the bear regime extends and drawdowns occur, v12's more aggressive positioning will underperform. Paper trading is the only way to observe this honestly.
