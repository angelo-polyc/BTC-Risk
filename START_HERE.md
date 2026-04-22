# START HERE — BTC Model v8 Starter Package

**Created:** 2026-04-16
**State:** Clean v8 canonical. All v11 D2h work archived separately. Ready to drop into a fresh project folder.

This package is everything you need to have a working BTC model that:
- Predicts daily BTC position (0–1) based on 6 hypothesis-group ensemble with regime-conditional weights
- Matches the performance documented in `refit_report_v8.md` bit-for-bit
- Has no v9/v10/v11 cruft

---

## Setup (one time, ~5 minutes)

### 1. Drop the entire contents of this folder into your new project folder

That's it. The model is already in working state — `master_daily_view_wf365.csv` has today's position (0.85 long, regime bear, percentile 0.573).

### 2. (Optional) Install dependencies if running pipeline from source

```bash
pip install pandas numpy scipy scikit-learn pyarrow matplotlib \
            yfinance fredapi velodata cot_reports requests
```

### 3. Verify

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('master_daily_view_wf365.csv', parse_dates=['date']).set_index('date')
last = df.dropna(subset=['position']).iloc[-1]
print(f'Date: {last.name.date()}  Regime: {last.regime}')
print(f'Percentile: {last.percentile:.4f}  (expect 0.5726)')
print(f'Position:   {last.position:.4f}  (expect 0.8493)')
"
```

Expected output:
```
Date: 2026-04-15  Regime: bear
Percentile: 0.5726  (expect 0.5726)
Position:   0.8493  (expect 0.8493)
```

If that matches, you're done with setup. The model is live.

---

## Daily use

### To see the current model state

```bash
python3 -c "
import pandas as pd
df = pd.read_csv('master_daily_view_wf365.csv', parse_dates=['date']).set_index('date')
print(df.tail(1).T)
"
```

### To generate today's chart

```bash
python3 make_daily_chart.py
# Produces today.png with 3-panel view: BTC price, percentile, hypothesis composites
```

### To interpret the output

- `position` ∈ [0, 1] — target long BTC weight. **0.85 today.**
- `percentile` — rolling-365 rank of ensemble_score. Determines position via (0.55, 0.70) thresholds.
- `regime` ∈ {bull, neutral, bear} — selects which hypothesis weights apply.
- 6 `{hypothesis}_score` columns — each hypothesis's drawdown-risk view.

Read `HANDOVER.md` for the full orientation.

---

## What's in this package (44 files)

### Code — pipeline

The pipeline has two layers. Layer 1 pulls raw data and builds per-hypothesis scores. Layer 2 combines them into the ensemble + position.

**Layer 1 — one per hypothesis group:**
- `pull_all_raw_data.py` — single script that pulls FRED, CFTC, Coinglass, Velo, Yahoo (~30-40 min due to Velo rate limits)
- `fix_parsers.py` — post-pull patches for known parser bugs (must run after `pull_all_raw_data.py`)
- `build_foundation.py` — regime classifier (200d/30d momentum + hysteresis) + forward DD labels
- `build_macro_equities.py`, `build_cme.py`, `build_crypto_derivatives.py`, `build_classic_cycle.py`, `build_etf_flows.py`, `build_eth.py` — hypothesis builders

**Layer 2 — the ensemble:**
- `build_robust.py` — canonical ensemble (AUC-excess weighting per regime, configurable position thresholds via `POSITION_LONG_THR` / `POSITION_DEF_THR` env vars)
- `build_nnls_diagnostic.py` — diagnostic NNLS variant (do not deploy; kept for historical comparison)
- `run_full_pipeline.py` — one-shot runner (foundation + 6 hypotheses + ensemble)
- `regenerate_canonicals.sh` — regenerates both canonicals (sf730 + wf365) × both labels (y_60 + y_30) with per-canonical position thresholds
- `export_csvs.py` — writes `master_daily_view.csv` and `weights.csv` from pipeline parquets. **⚠ Stale relative to the multi-canonical structure** — it writes `master_daily_view.csv` (singular) not the suffix variants. Useful for debugging; not used by `regenerate_canonicals.sh`.

**Helpers & supporting:**
- `common.py` — shared constants (`MIN_CALIB`, `ENSEMBLE_FIT_START`, `MODEL_START`), `auc_excess_weights()` with two-tier pinning support
- `make_daily_chart.py` — daily 3-panel chart (BTC price, percentile, hypotheses)
- `calibration_test.py` — session-7 cardinal calibration experiment (failed; kept as reference for session-8 retry plan)
- `test_strong_prior.py` — unit tests for two-tier pinning (not wired to CI)

### Data — canonical state

- **`master_daily_view_wf365.csv`** — the deployed canonical. One row per day, all model state. THIS is the file you open to see "what is the model saying today."
- `master_daily_view_sf730.csv` — reference variant (single-fit, rolling-730d percentile)
- `weights.csv` — latest regime × hypothesis weight matrix per variant
- `thresholds.csv` — per-variant position thresholds (wf365: 0.55/0.70, sf730: 0.55/0.65)
- `hypothesis_*.csv` (×6) — sub-signal ranks per hypothesis, used as inputs to the ensemble

### Data — reference / audit

- `data_inventory.csv` — every raw data series with start/end dates
- `pinning_audit_findings.csv` — session-3 pinning audit
- `raw_data_export.csv` — full wide-format raw data (12MB; optional; referenced by `make_daily_chart.py` for BTC prices)

### Documentation — current (read in order)

1. **`HANDOVER.md`** — orientation. Start here when onboarding a new instance.
2. **`execution_playbook_v4.md`** — current model spec, hypothesis definitions, runbook, sanity-check targets.
3. **`refit_report_v8.md`** — most recent substantive work (session 7 — position threshold recalibration).

### Documentation — historical (paper trail)

Retained because they explain design decisions:
- `refit_report.md` (session 1), `refit_report_v3.md` (3), `_v4.md` (4), `_v5.md` (5, crisis validation), `_v6.md` (6, transaction cost analysis), `_v7.md` (7, canonical selection + failed experiments)
- `execution_playbook.md` (original), `_v3.md` (interim)
- `raw_data_export_report.md`, `raw_data_export_addendum.md` — data docs

### Files NOT included (deliberately)

- `NEXT_SESSION_PLAN.md` — had a v11-era version that's no longer accurate. Open questions are captured in `HANDOVER.md` under "Recommended next steps."
- `README.md` — session 7→8 handover package README, not for new projects.
- `weight_history_wf365_y_60.csv` — pure audit artifact, regenerated on next pipeline run.
- All v11 artifacts: `refit_report_v11.md`, `v11_validation_memo.md`, D2h master data files, regime plots. These are preserved separately in `/mnt/user-data/outputs/rollback_to_v8/v11_archive/`. Move them somewhere safe if you want the v11 history accessible; otherwise ignore.

---

## To rebuild from scratch (sanity check or fresh data)

```bash
# 1. Pull raw data (~30-40 min)
python3 pull_all_raw_data.py --source all --out-dir data/raw

# 2. Patch parser bugs
python3 fix_parsers.py

# 3. Regenerate both canonicals × both labels
bash regenerate_canonicals.sh
```

Expected Sharpe (gross, 5-hypothesis; ETH removed from ensemble in v12, 2026-04-17). wf365 canonical with data through 2026-04-17:
- Full window (2021-06-30 →): 1.09
- Hold-out year (2025-04-17 →): 1.15

See `execution_playbook_v4.md` §"Sanity check targets" for the full target table.

---

## Known open items (carried over)

From `HANDOVER.md` recommended next steps, in priority order:

1. **Paper trading shadow run** — blocking prerequisite for real-capital deployment. Run pipeline on live data 4+ weeks, diff daily outputs against expectations, catch ops bugs.
2. **Cardinal calibration retry** — session-7 attempt failed; session-8 plan has specific architectural fixes.
3. **Operational comms runtime** — dashboard + daily chart prototypes exist; scheduler + trigger evaluator + agent wiring still needed.
4. **Operational hygiene** — fold `fix_parsers.py` into `pull_all_raw_data.py`; add data-freshness monitoring; refresh stale ETF premium endpoint (last data 2026-01-06).
5. **`OPERATIONS.md`** — interpretation guide, override procedures, escalation criteria.
6. **Annual health-check script** — drift detection for future model-drift monitoring.

### Explicit non-goals
- V2 architecture rebuild
- More hypotheses
- Re-tuning position thresholds for ~2–3 years or until a health-check trigger fires
- Real capital before 4+ weeks of clean paper trading

---

## Package integrity

Checksums if you want to verify nothing got corrupted during transfer:

```bash
sha256sum master_daily_view_wf365.csv build_foundation.py build_robust.py HANDOVER.md refit_report_v8.md
```

(Run this after setup and save the output somewhere. If files change unexpectedly in the future, you'll know.)

---

## If something's wrong

- **Today's position isn't 0.85:** check you copied `master_daily_view_wf365.csv` from this package, not an older version.
- **`python3 make_daily_chart.py` errors:** ensure `raw_data_export.csv` is in the same folder (the chart reads BTC prices from it).
- **`bash regenerate_canonicals.sh` fails:** check that raw data has been pulled (Step 1 above) and parsers patched (Step 2).
- **Numbers don't match `refit_report_v8.md`:** `refit_report_v8.md` was computed against a data snapshot from 2026-04-15. If you pull fresh data, expect some drift as the hold-out window rolls forward.

---

## The 30-second mental model

Six hypothesis groups each produce a [0,1] drawdown-risk score daily. Regime classifier (bull/neutral/bear) determines which hypothesis weights apply. Weighted sum → ensemble_score → rolling-365 percentile → piecewise-linear position function (full long below 0.55, full defense above 0.70, linear between) → daily long BTC allocation. Strategy return = yesterday's position × today's BTC return.

That's the whole thing.
