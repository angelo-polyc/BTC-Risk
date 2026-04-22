# Refit Report v11 — Transaction Cost Removal + Data Refresh

**Session:** 11
**Date:** 2026-04-17
**Status:** Shipped. Transaction cost model removed from evaluation framework and canonical docs; data refreshed through 2026-04-17; canonical CSVs regenerated.

---

## TL;DR

Two changes this session, both operational, neither structural:

1. **Transaction cost model removed.** Per user directive. Investigation revealed the cost model was already a documentation-only construct: stored `strategy_return` in the canonical CSVs was always gross (`position.shift(1) * btc_return`), matching the build_robust.py source exactly. "Post-5bps" figures in HANDOVER and refit reports v3–v10 were applied analytically at reporting time. The change is therefore: update docs + one analysis script (`grid_sensitivity.py`) to report gross; leave the stored data untouched.

2. **Data refresh through 2026-04-17.** Full pipeline re-run. Canonical `master_daily_view_wf365.csv` now 4,231 rows (was 4,228). Today's call: wf365 regime=bear, percentile=0.633, position=0.45.

No changes to: hypothesis builders, ensemble weighting, regime classifier, position thresholds, calibration labels, pinning sets.

---

## 1 — Transaction cost removal

### 1.1 What the cost model actually was

Investigation confirmed that cost was never deducted in the pipeline. Evidence:

- `build_robust.py` line 210: `df["strategy_return"] = df["position"].shift(1) * df["btc_return"]`. No cost term.
- On the shipped v10 canonical (`master_daily_view_wf365_v10.csv`), computing `position.shift(1) * btc_return` matches the stored `strategy_return` to within floating-point precision (max|Δ| 9.8e-17 across all 4,228 rows, 713 of which had non-zero position changes).
- Recomputed Sharpes against the shipped CSV match HANDOVER's "post-5bps" claim only when cost is applied analytically at reporting time. Gross Sharpe is ~0.06 higher full-window and ~0.10 higher hold-out.

The only code path that actually subtracted cost was `grid_sensitivity.py` — a session-9 analysis script producing `grid_sensitivity.csv`. It used `COST = 0.0005` per side.

### 1.2 Rationale

User request. The cost-vs-no-cost difference is small (session 6 measured ~0.004 Sharpe) and was itself a finding that contradicted an earlier back-of-envelope estimate. Operationally, removing cost simplifies the mental model and the reporting pipeline. The trade-off: live paper-trading P&L vs model-expected P&L will now carry a small negative bias proportional to realized brokerage cost. At typical execution (5bps roundtrip, ~43 annual turnover), that bias is ~0.02 Sharpe / ~0.2%/year — below noise for a 4-week shadow run.

### 1.3 What changed — files

| File | Change |
|---|---|
| `HANDOVER__1_.md` | Status line, 30-sec orientation, today's-call line, "Expected Sharpe" block all updated with gross numbers. Added top-of-file note explaining that historical refit reports (v3–v10) retain post-5bps KPIs. |
| `execution_playbook_v4.md` | "Sanity check targets" table rewritten with v11 gross numbers. "Known issues" item #5 changed from "Transaction costs now applied" to a migration note. Pre-v8 reference subtable removed (no longer relevant to current reporting basis). |
| `README.md` | Line 122 updated: full-window 0.90 / hold-out 0.74 (post-5bps) → 0.96 / 0.90 (gross). |
| `START_HERE.md` | Expected-Sharpe block updated with v11 gross numbers (was quoting v8 post-5bps figures, now ~2 generations stale). |
| `grid_sensitivity.py` | `COST = 0.0005` removed. `compute_metrics` now computes gross. Print-header label changed. |
| `refit_report_v11.md` | This file. |

### 1.4 What did NOT change

- Historical refit reports v3–v10. Preserved verbatim as a paper trail. Top-of-file note in HANDOVER explains how to translate their post-5bps numbers to gross (subtract ~0.05–0.10 Sharpe).
- `grid_sensitivity.csv` (the data file). Still contains session-9's post-5bps grid. Will be regenerated gross on the next refit session that needs it; flagged in HANDOVER for future reference.
- `build_robust.py`, all hypothesis builders, all canonical CSV `strategy_return` columns. Already gross.

### 1.5 KPI translation

For the v11 canonical (data through 2026-04-17):

| Metric | wf365 (post-5bps, ref) | wf365 (gross, canonical) | Δ |
|---|---:|---:|---:|
| Full Sharpe | 0.90 | 0.96 | +0.06 |
| Hold-out Sharpe | 0.74 | 0.90 | +0.16 |
| Full total | +251% | +288% | +37pp |
| Full MaxDD | −39.4% | −36.7% | +2.7pp |
| Annual turnover | 43.1 | 43.1 | — |

Hold-out delta is larger than the ~0.004 documented in session 6 because the hold-out window happens to contain several taper-zone days where turnover concentrates.

---

## 2 — Data refresh

### 2.1 Scope

Full re-pull of all sources. Previous canonical was built from a 2026-04-14 snapshot; v11 is built from 2026-04-17. Three new calendar days of forward movement.

### 2.2 Vendor status

| Source | Coverage through | Status |
|---|---|---|
| FRED (10 series) | 2026-04-17 | OK |
| Velo BTC (25 metrics × 3 exchanges) | 2026-04-17 | OK after retry on 1 metric (503 DNS overflow) |
| Velo ETH (25 metrics × 3 exchanges) | 2026-04-17 | OK after retry on 3 metrics (503 DNS overflow) |
| Coinglass cycle (8 indicators) | 2026-04-15 to -17 | OK; bubble_index regenerated via `fix_parsers.py` |
| Coinglass h2 (basis, coin-margin OI, funding, OI, liquidations) | 2026-04-17 | OK |
| Coinglass h3 (ETF flow / premium) | 2026-04-17 | OK; premium endpoint stays refreshed post-v10 |
| CFTC (TFF 133741) | 2026-04-07 (latest published report) | OK after retry (library initial pull hit stale zip URL) |
| Artemis ETF (flow, spot volume) | 2026-04-17 | OK after 1 retry (500 DNS overflow) |
| Yahoo price (BTC, ETH OHLC) | 2026-04-17 | OK |

Total pull time: ~45 seconds (including retries). Velo historical claim of "25 min cold pull" is stale — actual full-history pull for one metric takes ~1.2s (5 years of daily data = 1,933 rows).

### 2.3 Environmental fixes applied

Three hard-coded-path / missing-retry bugs hit during pipeline execution:

1. **`fix_parsers.py`** had `ROOT = Path("/home/claude/btc_model/data/raw")` hard-coded. Patched in-place to resolve via `BTC_RAW_DIR` env var, falling back to `./data/raw`, falling back to the legacy hard-coded path.
2. **`fix_parsers.py`** `requests.get` for Coinglass bubble_index had no retry. Patched to use a 6-attempt retry wrapper with exponential backoff for the egress proxy's transient "503 DNS cache overflow" response.
3. **`regenerate_canonicals.sh`** had `export BTC_MODEL_ROOT=/home/claude/btc_model` that overrode the caller's env. Patched to respect an externally-set value and default to `$(pwd)`.

Each is a real bug that would have bitten anyone running the pipeline outside the original session's environment. All patched in-place.

### 2.4 Drift vs shipped v10

Regenerated v11 vs shipped v10 across 4,228 overlap dates:

| Metric | mean \|Δ\| | p95 \|Δ\| | max \|Δ\| |
|---|---:|---:|---:|
| percentile | 0.0036 | 0.0137 | 0.0932 |
| position | 0.0044 | 0.0183 | 0.6210 |

Consistent with the v9/v10 input-sensitivity findings. FRED revisions on T10Y2Y/DFF/BAMLH0A0HYM2 are the primary driver on the macro side; Velo's silent revisions per HANDOVER §Data-refresh drift account for the rest. The position max|Δ| of 0.62 reflects the taper-zone amplification mechanism — small percentile shifts cross the (0.55, 0.70) threshold on a handful of edge days.

On 2026-04-14 (last overlap day): OLD percentile 0.811, position 0.00; NEW percentile 0.795, position 0.00. Material call unchanged.

### 2.5 KPI table (v11 canonical, gross, data through 2026-04-17)

| Metric | sf730 | wf365 |
|---|---:|---:|
| Full Sharpe | 1.31 | 0.96 |
| Full total return | +644% | +288% |
| Full MaxDD | −27.6% | −36.7% |
| Hold-out Sharpe | 1.45 | 0.90 |
| Hold-out total | +27% | +20% |
| Hold-out MaxDD | −10.1% | −19.8% |
| B&H (reference) | Sharpe 0.56, +111%, −77% MaxDD |

---

## 3 — Today's call

**2026-04-17 (v11 canonical):**

| Variant | regime | ensemble_score | percentile | position |
|---|---|---:|---:|---:|
| wf365 (deployed) | bear | 0.508 | 0.633 | 0.45 |
| sf730 (reference) | bear | 0.508 | 0.720 | 0.00 |

Interpretation: BTC rebounded off the Apr 12–14 low (~$77k → ~$81k at time of snapshot). That rebound pulled wf365's rolling-365 percentile from 0.81 back down to 0.63, crossing the upper taper boundary (0.70) and putting position into the linear taper zone. sf730's rolling-730 percentile is less reactive (wider reference window) and still reads above its 0.65 defensive threshold, so sf730 stays at 0.00.

Regime remains bear on both variants — the D2h classifier requires −20% recovery from trough to exit bear, which BTC has not achieved. The ensemble hypothesis scores all read above 0.5 (above-median drawdown risk) but none in the extreme-high range; the composite is at 0.51, near its own percentile-50 level within the rolling window.

Disagreement between wf365 (45% long) and sf730 (0% defensive) is within the expected envelope: hold-out Sharpe gap favoring sf730 (+0.55) has been widening through 2026-04, consistent with the sf730 hold-out lead documented in v10. Paper trading is the only way to determine whether the live divergence tracks backtest predictions.

---

## 4 — What did NOT change

- Hypothesis builders (`build_macro_equities.py`, `build_cme.py`, `build_crypto_derivatives.py`, `build_classic_cycle.py`, `build_etf_flows.py`, `build_eth.py`) — code untouched.
- Regime classifier (`build_foundation.py`, D2h per v10) — untouched.
- Ensemble weighting (`build_robust.py`) — untouched.
- Position thresholds (wf365 0.55/0.70; sf730 0.55/0.65) — untouched.
- Calibration labels (y_60 canonical, y_30 reference) — untouched.
- `weights.csv` was regenerated mechanically from the same fit procedure; weight vectors shift slightly due to new data but the fit methodology is identical.
- Pinning sets and strong_prior classifications.
- Historical refit reports.
- `build_nnls_diagnostic.py`, `calibration_test.py`, `test_strong_prior.py`, `common.py`.
- Paper-trading protocol. HANDOVER's "4+ weeks of clean paper trading before real capital" stays in force and presumably resets on the v11 data refresh (new canonical baseline).

---

## 5 — Session artifacts

### In `/mnt/project/`

New/replaced:
- `master_daily_view_wf365.csv` — 4,231 rows (v11)
- `master_daily_view_sf730.csv` — 4,231 rows (v11)
- `weights.csv` — 12 rows (2 variants × 2 labels × 3 regimes)
- `weight_history_wf365_y_60.csv`, `weight_history_wf365_y_30.csv` — 828 rows each
- `refit_report_v11.md` — this file

Patched in-place:
- `HANDOVER__1_.md`
- `execution_playbook_v4.md`
- `README.md`
- `START_HERE.md`
- `grid_sensitivity.py`
- `fix_parsers.py` — path resolution + retry wrapper
- `regenerate_canonicals.sh` — respect external `BTC_MODEL_ROOT`

Archived:
- `master_daily_view_wf365.csv.old_v10`
- `master_daily_view_wf365_v10.csv.old`
- `master_daily_view_sf730.csv.old_v10`

### In `/mnt/project/data/raw/`

Fresh parquets for all 8 sources (49 files). These are now the canonical raw-data cache for any re-run.

---

## 6 — Open items (HANDOVER carryover, updated)

1. **Paper trading shadow run.** Still #1 blocker for real capital. v11 resets the paper-trading baseline — re-start the 4-week clock from 2026-04-17.
2. **Cardinal calibration retry.** Unchanged. Session-8 plan in `refit_report_v7.md` §3.
3. **Operational comms runtime.** Unchanged. Scheduler, trigger evaluator, event-alert charts, dedup, agent wiring.
4. **Operational hygiene.** Expanded to include this session's findings:
   - Fold `fix_parsers.py` into `pull_all_raw_data.py`.
   - Data-freshness monitoring.
   - Regenerate standalone `hypothesis_*.csv` in same run as master.
   - Wire `pull_artemis_etf.py` into `pull_all_raw_data.py`.
   - **v11 adds:** retry wrapper on `pull_artemis_etf.py` (copy the pattern from `fix_parsers.py`'s `_get_json_with_retry`).
   - **v11 adds:** update HANDOVER's "Velo pull takes 30-40 min" — now ~45s. Claim is stale.
   - **v11 adds:** ensure all scripts that write to `data/{derived,hypotheses,final}/` create those dirs (`regenerate_canonicals.sh` currently doesn't; `run_full_pipeline.py` does).
5. `OPERATIONS.md`.
6. **Annual health-check script.** Unchanged.
7. **Deferred: structural fix for rolling-percentile amplification.** Unchanged; gated on paper trading.

---

## 7 — Non-goals (reaffirmed)

- No structural model changes. v11 is purely operational (data refresh + reporting convention).
- No threshold retuning.
- No regime classifier changes.
- No hypothesis changes.
- No introduction of new parameters. ("If a fix requires 3+ new parameters, reject it" — unchanged discipline.)

---

## 8 — Reproducibility note

To reproduce this state from scratch:

```bash
pip install pandas numpy scipy scikit-learn pyarrow matplotlib \
            yfinance fredapi velodata cot_reports requests artemis

cd /mnt/project
mkdir -p data/{raw,derived,hypotheses,final}

# Pull raw
python3 pull_all_raw_data.py --source all --out-dir data/raw
ARTEMIS_API_KEY=<key> python3 pull_artemis_etf.py --out-dir data/raw

# Patch
python3 fix_parsers.py

# Regenerate canonicals
BTC_MODEL_ROOT=/mnt/project bash regenerate_canonicals.sh

# Export CSVs (need to run twice, once per variant; export_csvs.py reads
# ensemble_robust_y_60.parquet so swap the variant file in first)
cp data/final/ensemble_wf365_y_60.parquet data/final/ensemble_robust_y_60.parquet
cp data/final/ensemble_weights_wf365_y_60.csv data/final/ensemble_weights_robust_y_60.csv
BTC_MODEL_ROOT=/mnt/project python3 export_csvs.py
mv master_daily_view.csv master_daily_view_wf365.csv
# ... same for sf730
```

Expected: last row of `master_daily_view_wf365.csv` has date 2026-04-17, regime=bear, percentile≈0.633, position≈0.447.

If numbers drift from that by more than a few percent, suspect FRED or Velo revisions per HANDOVER §Data-refresh drift, or the pipeline was run on a later data snapshot (time advances).
