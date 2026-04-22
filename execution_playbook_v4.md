# BTC Crash Model — Execution Playbook v4

**Updated:** 2026-04-15 (session 4)
**Supersedes:** `execution_playbook_v3.md` (session 3)

Single source of truth after session 4. Read this before touching any build script.

## What changed since v3

| Area | v3 | v4 (canonical) |
|---|---|---|
| Number of canonicals | 1 (single-fit, expanding percentile) | **2** (sf730 and wf365) |
| Pinning weight formula | `max(a − 0.5, min_excess)` for all pinned | **Two-tier**: `max(\|a − 0.5\|, min_excess)` for strong_prior signals, original formula for others |
| Strong_prior set | not applicable | Macro {spx_overext, vix_z90, hy_spread_roc}; CD {funding_zscore, lev_stress, coin_margin_ratio}; CC = ∅ (deferred) |
| Ensemble fit | Single fit on [2021-06-30, HOLDOUT_START) | Either single fit (sf730) or monthly walk-forward (wf365) |
| Percentile basis | `expanding(min_periods=180).rank(pct=True)` | Rolling window: 730d for sf730, 365d for wf365 |
| Macro composite OOS AUC | 0.620 | **0.642** (+2.2pp from spx_overext rescue) |
| Ensemble OOS AUC (best) | 0.7621 | **0.7733** (wf365) |
| Strategy Sharpe full (best) | 0.909 | **1.191** (sf730) |
| Strategy total full (best) | +225% | **+537%** (sf730) |
| Strategy max DD full (best) | −34.4% | **−26.2%** (sf730) |

Everything else unchanged from v3.

## Architecture (unchanged)

```
btc_price ─► ret_200d_smooth ─► regime classifier ─► {bull, neutral, bear}
                                                              │
            ┌── Macro & Equities ───────────────┐            ▼
            ├── CME (positioning) ─────────────►│     regime weights
  raw       ├── Crypto Derivatives ────────────►│            │
  data     ─┤── Classic Cycle (4 indicators) ──►├──► ensemble score
            ├── ETF Flows ─────────────────────►│            │
            ├── ETH ───────────────────────────►│            ▼
            │                                    │     rolling percentile
            │                                    │     (730d sf, 365d wf)
            │                                    │            │
            │                                    │            ▼
            │                                    │    linear-hybrid position fn
            │                                    │            │
            │                                    │            ▼
            │                                    │       daily position %
```

## Key constants (in `src/common.py`)

```python
UTC = "UTC"
ENSEMBLE_FIT_START = pd.Timestamp("2021-06-30", tz=UTC)

MIN_CALIB = {
    "macro_equities":     pd.Timestamp("2018-10-01", tz=UTC),
    "cme":                pd.Timestamp("2021-06-30", tz=UTC),
    "crypto_derivatives": pd.Timestamp("2021-06-30", tz=UTC),
    "classic_cycle":      pd.Timestamp("2021-06-30", tz=UTC),
    "etf_flows":          pd.Timestamp("2024-01-11", tz=UTC),
    "eth":                pd.Timestamp("2021-06-30", tz=UTC),
}

CALIB_LABEL = os.environ.get("CALIB_LABEL", "y_30")  # canonical: y_60
```

## build_robust.py environment variables (NEW in v4)

| Env var | Default | Canonical value |
|---|---|---|
| `WALK_FORWARD` | `0` | sf730: `0` \| wf365: `1` |
| `PERCENTILE_WINDOW` | `expanding` | sf730: `730` \| wf365: `365` |
| `WALK_CADENCE_MONTHS` | `1` | (only used when `WALK_FORWARD=1`) |
| `WALK_WINDOW_MONTHS` | `expanding` | `expanding` or integer N (rolling) |
| `WARMUP_MONTHS` | `12` | 12 |
| `WALK_SMOOTH_K` | `1` | 1 (no smoothing) |

## Hypothesis specifications

Sections unchanged from v3 except where noted below.

### 1. Macro & Equities (8 sub-signals) — UPDATED

PINNED_DIRECTION = {spx_overext_rank, vix_z90_rank, fed_funds_stress_rank, hy_spread_roc_rank}

**STRONG_PRIOR = {spx_overext_rank, vix_z90_rank, hy_spread_roc_rank}** (NEW in v4)

Explicitly NOT in strong_prior: `fed_funds_stress_rank` — OOS AUC 0.07 indicates regime-contingent prior. Regression guard asserts weight < 0.05.

Composite IS AUC: 0.709 (down from 0.740 in v3 baseline — expected from spx_overext rescue)
Composite OOS AUC: **0.642** (up from 0.620 in v3 baseline — the intended effect)

### 2. CME (3 sub-signals) — UNCHANGED

### 3. Crypto Derivatives (10 sub-signals) — UPDATED

PINNED_DIRECTION = {funding_zscore_rank, lev_stress_rank, coin_margin_ratio_rank, rv21_zscore_rank}

**STRONG_PRIOR = {funding_zscore_rank, lev_stress_rank, coin_margin_ratio_rank}** (NEW in v4)

Explicitly NOT in strong_prior: `rv21_zscore_rank` — IS 0.36, OOS 0.50, documented pinning success case. Regression guard asserts weight < 0.05.

Composite IS/OOS AUCs: unchanged from v3 (strong_prior signals all have IS > 0.5, so formulas are mathematically identical on current data). Latent no-op; earns its keep if IS drifts below 0.5 in a future window.

### 4. Classic Cycle Indicators (4 sub-signals) — UNCHANGED

KEEP_SET still `{golden_ratio, bmo, ahr999, fear_greed}`.

**STRONG_PRIOR = set()** — permanently deferred. See the comment at the STRONG_PRIOR assignment site in `build_classic_cycle.py` for extensive rationale (Task 1 cascade, Task 2 walk-forward test that didn't resolve it, proposed ensemble-layer fix).

### 5. ETF Flows (4 sub-signals) — UNCHANGED

Still stale at 2026-01-06.

### 6. ETH (7 sub-signals) — UNCHANGED

No pinning (data-driven orientation).

## Ensemble fitting

Two canonicals, both in `src/build_robust.py`, selected via env vars.

### Canonical sf730 (single-fit + rolling-730d percentile)

```bash
CALIB_LABEL=y_60 WALK_FORWARD=0 PERCENTILE_WINDOW=730 python build_robust.py
```

Weights computed once on [2021-06-30, HOLDOUT_START) window, frozen thereafter. Position function reads rolling-730d percentile of ensemble_score.

#### sf730 canonical regime weights (y_60)

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH | Days |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 0.014 | 0.014 | **0.459** | 0.014 | 0.187 | 0.311 | 595 |
| neutral | 0.240 | **0.338** | 0.170 | 0.101 | 0.000 | 0.151 | 522 |
| bear | 0.211 | **0.582** | 0.017 | 0.017 | 0.000 | 0.173 | 268 |

### Canonical wf365 (walk-forward + rolling-365d percentile)

```bash
CALIB_LABEL=y_60 WALK_FORWARD=1 WALK_CADENCE_MONTHS=1 \
    WALK_WINDOW_MONTHS=expanding WARMUP_MONTHS=12 \
    PERCENTILE_WINDOW=365 python build_robust.py
```

Weights refit on day 1 of each month using expanding window [2021-06-30, fit_date). First fit at 2022-07-01 (after 12-month warmup). Each day's ensemble score uses the most-recent-fit-date ≤ that day. Pre-warmup period uses the first fit's weights backward-extended (acknowledged minor leak, consistent with single-fit baseline).

Position function reads rolling-365d percentile. All 46 monthly fits' weight history saved to `data/final/weight_history_robust.csv`.

#### wf365 latest-fit weights (2026-04-01)

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH |
|---|---:|---:|---:|---:|---:|---:|
| bull | 0.140 | 0.013 | **0.441** | 0.041 | 0.062 | 0.302 |
| neutral | 0.238 | 0.269 | 0.189 | 0.127 | 0.008 | 0.169 |
| bear | 0.130 | **0.343** | 0.045 | 0.045 | 0.045 | **0.391** |

Differences vs sf730: bear regime shifts weight from CME (0.582 → 0.343) to ETH (0.173 → 0.391) because the hold-out year's bear-regime behavior saw ETH outperform CME as a drawdown predictor. This reflects the walk-forward's adaptivity.

### Position function (recalibrated 2026-04-15 — v8)

Thresholds recalibrated from (0.50, 0.90) per-variant. wf365 validated on 4 hold-out years (wins 4/4); sf730 validated on 4 hold-out years (wins 3/4 with one tie). See `refit_report_v8.md` for full validation. Thresholds set via `POSITION_LONG_THR` and `POSITION_DEF_THR` env vars in `regenerate_canonicals.sh`.

**wf365 canonical (deployed):**
```
position(p) = 1.0                            if p ≤ 0.55
            = 1.0 − (p − 0.55)/0.15          if 0.55 < p < 0.70
            = 0.0                            if p ≥ 0.70
```

**sf730 reference:**
```
position(p) = 1.0                            if p ≤ 0.55
            = 1.0 − (p − 0.55)/0.10          if 0.55 < p < 0.65
            = 0.0                            if p ≥ 0.65
```

### Backtest (unchanged mechanics)

`strategy_return[t] = position[t-1] × btc_return[t]`

Equity curve computed over post-`ENSEMBLE_FIT_START` window (2021-06-30 → today, ~1,750 days).

## Runbook

### Full pipeline (both canonicals)

```bash
cd btc_model
python src/pull_all_raw_data.py --source all --out-dir data/raw
python src/fix_parsers.py
python src/build_foundation.py
CALIB_LABEL=y_60 python src/run_full_pipeline.py   # runs default single-fit expanding canonical
bash src/regenerate_canonicals.sh                   # regenerates BOTH sf730 and wf365
```

### Just re-run ensemble for both canonicals (hypotheses already built)

```bash
bash src/regenerate_canonicals.sh
```

## File schemas

**Canonical CSV outputs (per canonical):**
- `master_daily_view_sf730.csv` — one row per day, columns: date, regime, y_30, y_60, fwd_30d_max_dd, fwd_60d_max_dd, 6 hypothesis_score columns, ensemble_score, percentile, position, btc_return, strategy_return
- `master_daily_view_wf365.csv` — same schema

**Weights:**
- `weights.csv` — variant ∈ {y_60_sf730, y_60_wf365_latest, y_30_sf730, y_30_wf365_latest} × 3 regimes × 6 hypothesis weights (12 rows total). The y_60 variants are deployed canonicals; the y_30 variants are reference-only (following v3's `y_30_comparison` convention — y_30 has OOS AUC ~0.58 vs y_60's ~0.77, so y_60 remains the canonical deployment label).
- `weight_history_wf365_y_60.csv` — fit_date, regime, hypothesis, weight (828 rows = 46 fits × 3 regimes × 6 hypotheses) for the y_60 walk-forward canonical
- `weight_history_wf365_y_30.csv` — same structure for the y_30 reference variant

## Sanity check targets (v12 — 2026-04-17, gross, **5-hypothesis** ensemble)

For a canonical run with `CALIB_LABEL=y_60` on data pulled 2026-04-17 or later, with position thresholds (wf365: 0.55/0.70; sf730: 0.55/0.65), ETH removed from ensemble (v12):

| Metric | sf730 target | wf365 target |
|---|---|---|
| Hypothesis hold-out AUCs (5) | Macro 0.513, CME 0.788, CD 0.632, CC 0.752, ETF 0.337 | same (hypotheses shared) |
| ETH hypothesis hold-out AUC (reference-only) | 0.683 | same |
| Ensemble IS AUC | 0.78 ± 0.02 | 0.72 ± 0.02 |
| Ensemble OOS AUC | 0.88 ± 0.03 | 0.88 ± 0.03 |
| Strategy Sharpe (full, ~1750d) | 1.28 ± 0.05 | 1.09 ± 0.05 |
| Strategy total (full) | +612% ± 40pp | +359% ± 40pp |
| Strategy max DD (full) | −30% ± 3pp | −30% ± 3pp |
| Strategy Sharpe (hold-out 365d) | 1.37 ± 0.10 | 1.15 ± 0.10 |
| Strategy max DD (hold-out) | −12% ± 3pp | −21% ± 3pp |
| B&H comparison (full) | Sharpe 0.56, +111%, −77% | same |

Note: v12 numbers are gross, 5-hypothesis. Prior v11 numbers were gross 6-hypothesis; see `refit_report_v12.md` §4 for year-by-year comparison.

## Known issues (v4-open)

1. **CC strong_prior permanently deferred.** See `build_classic_cycle.py` comment at STRONG_PRIOR assignment. Requires ensemble-layer alpha-augmentation to fix.
2. **Bull-regime AUC noise.** Regime classifier turns off bull before drawdowns → low bull-regime y_60 base rate → noisy AUCs in walk-forward fits. Mitigated by rolling-365 percentile.
3. **Pull script parser bugs** patched post-pull by `fix_parsers.py`. Fold in when convenient.
4. **ETF premium endpoint** stale at 2026-01-06 — **resolved in v10** (Coinglass refreshed upstream; schema change handled by patched `fix_parsers.py`).
5. **Transaction cost model removed** in v11 (2026-04-17). All reported Sharpe/return numbers in v11+ are gross; stored `strategy_return` column was always gross. See `refit_report_v11.md`.
6. **ETH hypothesis removed from ensemble** in v12 (2026-04-17). `build_eth.py` still runs for reference/health-check monitoring; `ensemble_score` no longer includes ETH. See `refit_report_v12.md` for structural reasoning and h2025 caveat.
7. **Single hold-out year** still thin evidence — Task 3 (crisis validation) addresses this.
8. **Velo data revisions** happen silently — see HANDOVER §Data-refresh drift.

## Session history

- v1 (session 1): initial NNLS ensemble
- v2 (session 2): robust AUC-excess ensemble, pinning, canonical y_60 label
- v3 (session 3): Classic Cycle restricted to 4 indicators after post-ETF-regime failure on 9-indicator version
- v4 (session 4): two-tier pinning (Macro + CD only), rolling percentile basis discovery, two canonicals shipped
- v5–v8 (sessions 5–7): crisis validation, transaction-cost analysis, canonical selection, threshold recalibration
- v9 (session 9): reproducibility audit, sensitivity characterization, build_robust recovery
- v10 (session 10): D2h regime classifier, fresh Coinglass premium, ETF Flows V4 hybrid
- v11 (this session): transaction-cost model removed; data refreshed through 2026-04-17
- **v12 (this session): ETH hypothesis removed from ensemble; canonical is now 5-hypothesis**

See `refit_report_v12.md` for the most recent narrative.
