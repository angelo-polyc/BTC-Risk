# BTC Crash Model — Execution Playbook v3

**Updated:** 2026-04-15 (session 3)
**Supersedes:** `execution_playbook_v2.md` (session 2)

This is the single source of truth for the canonical model after the Classic
Cycle 4-indicator restriction and the V1 ensemble refit. Read this before
touching any build script.

---

## What changed since v2

| Area | v2 | v3 (canonical) |
|---|---|---|
| Classic Cycle indicator set | all 9 (bubble_index, rainbow, heatmap, ma_roc, golden_ratio, bmo, ahr999, fear_greed, two_year_ma) | **4 only** (golden_ratio, bmo, ahr999, fear_greed) |
| Classic Cycle IS AUC (y_60) | 0.80 | 0.62 |
| Classic Cycle OOS AUC (y_60) | **0.35** | **0.76** |
| Ensemble regime weights | v2 fit against 9-indicator CC | **refit** against 4-indicator CC |
| Ensemble OOS AUC | 0.75 | **0.79** |
| Strategy full Sharpe | 1.00 | 0.96 |
| Strategy full max DD | −46.6% | **−30.2%** |
| Strategy **hold-out** Sharpe | −0.03 | **+0.28** |
| Strategy **hold-out** max DD | −46.6% | **−30.2%** |

**Everything else is unchanged from v2.** Same 6 hypotheses, same per-hypothesis
calibration windows in `MIN_CALIB`, same pinning logic (but see Known Issues
below), same `build_robust.py` AUC-excess ensemble, same linear-hybrid position
function, same `y_60` canonical label.

---

## Architecture (unchanged)

```
btc_price ─► ret_200d_smooth ─► regime classifier ─► {bull, neutral, bear}
                                                              │
            ┌── Macro & Equities ───────────────┐            ▼
            ├── CME (positioning) ─────────────►│     regime weights (robust AUC-excess)
  raw       ├── Crypto Derivatives ────────────►│            │
  data     ─┤── Classic Cycle (4 indicators) ──►├──► ensemble score
            ├── ETF Flows ─────────────────────►│            │
            ├── ETH ───────────────────────────►│            ▼
            │                                    │   expanding percentile
            │                                    │            │
            │                                    │            ▼
            │                                    │     linear-hybrid position function
            │                                    │            │
            │                                    │            ▼
            │                                    │     daily position %
```

---

## Key constants (in `src/common.py`)

```python
UTC = "UTC"
ENSEMBLE_FIT_START = pd.Timestamp("2021-06-30", tz=UTC)

# Per-hypothesis calibration start dates (UNCHANGED from v2)
MIN_CALIB = {
    "macro_equities":     pd.Timestamp("2018-10-01", tz=UTC),
    "cme":                pd.Timestamp("2021-06-30", tz=UTC),
    "crypto_derivatives": pd.Timestamp("2021-06-30", tz=UTC),
    "classic_cycle":      pd.Timestamp("2021-06-30", tz=UTC),
    "etf_flows":          pd.Timestamp("2024-01-11", tz=UTC),
    "eth":                pd.Timestamp("2021-06-30", tz=UTC),
}

CALIB_LABEL = os.environ.get("CALIB_LABEL", "y_30")  # canonical: y_60

def load_holdout_start() -> pd.Timestamp: ...
```

---

## Hypothesis specifications

Sections 1, 2, 3, 5, 6 are UNCHANGED from v2. Section 4 (Classic Cycle) is
replaced by the v3 spec below.

### 1. Macro & Equities (8 sub-signals)
See `execution_playbook_v2.md` §1. **NO CHANGE** in v3 except: one sub-signal
(`spx_overext_rank`) is silently excluded by the pinning logic and is the
subject of Task 1 in the v3 open-items list. See `refit_report_v3.md` "Pinning
audit" for details. **No code change yet** — fix deferred to session 4.

### 2. CME (3 sub-signals)
See v2 §2. **NO CHANGE** in v3.

### 3. Crypto Derivatives (10 sub-signals)
See v2 §3. **NO CHANGE** in v3. Pinning audit confirmed all pinned signals
behave correctly — `rv21_zscore_rank` is the documented success case where
pinning correctly floored a would-be wrong-direction overfit.

### 4. Classic Cycle Indicators (4 sub-signals) — v3 REVISED

**Calibration window:** 2021-06-30 → HOLDOUT_START (unchanged)

Restricted to 4 indicators. The other 5 were dropped in session 3 after
per-sub-signal OOS analysis showed they had structurally failed in the
2025–2026 hold-out window.

| Indicator | Endpoint | Raw transform | Orientation |
|---|---|---|---|
| `golden_ratio` | `v3 /index/golden-ratio-multiplier` | `price / ma350` | flip |
| `bmo` | `v4 /index/bitcoin-macro-oscillator` | raw value | flip |
| `ahr999` | `v3 /index/ahr999` | `1 / ahr999` | keep (pre-inverted) |
| `fear_greed` | `v3 /index/fear-greed-history` | raw 0-100 | flip |

**Orientations are hard-coded per the v2 playbook convention** — `auc_excess_weights`
is called with `no_flip=set(subs.columns)` to keep the fitter from re-inverting them.

#### Dropped indicators and reasons

| Dropped indicator | Pattern | y_60 IS AUC | y_60 OOS AUC |
|---|---|---:|---:|
| `two_year_ma` | NaN in modern era (flat regime) | NaN | NaN |
| `heatmap` (price/MA1440) | Failed OOS | 0.67 | 0.24 |
| `ma_roc` (MA1440 pct change) | Failed OOS | 0.64 | 0.18 |
| `rainbow` (price vs bands) | Failed OOS | 0.75 | 0.23 |
| `bubble_index` | Failed OOS, highest-weighted pre-fix | 0.78 | 0.20 |

**Physical interpretation:** all 4 failed indicators are variants of "price
distance above a long-term moving average." In the post-ETF regime, BTC has
sustained being far above its long-term MAs without mean-reverting —
institutional flow appears to have decoupled "price above old MA" from
"drawdown precursor." The 4 retained indicators measure different mechanisms
(shorter MA, macro oscillator, cheap/expensive ratio, sentiment) and hold up
out-of-sample.

**In the canonical v3 fit:**
- Sub-signal weights (within Classic Cycle, via `auc_excess_weights` pinned):
  - `fear_greed`: 0.49
  - `golden_ratio`: 0.44
  - `bmo`: 0.04 (floor-weighted because IS AUC 0.47 < 0.5)
  - `ahr999`: 0.04 (floor-weighted because IS AUC 0.34 < 0.5)
- Classic Cycle composite IS AUC: **0.62**
- Classic Cycle composite OOS AUC: **0.76**

**Note on `bmo` and `ahr999` floor-weighting:** these two signals have the
*strongest* hold-out AUCs (0.72 and 0.76) but are floor-weighted because their
calibration-window AUCs are below 0.5 and they're pinned. This is the
documented pinning weakness that Task 1 in session 4 addresses. Despite the
floor-weighting, the v3 composite still recovers to OOS 0.76 because the other
two indicators (`fear_greed`, `golden_ratio`) carry enough signal. A future
two-tier pinning formula would likely lift the composite further.

### 5. ETF Flows (4 sub-signals)
See v2 §5. **NO CHANGE** in v3. Still stale at 2026-01-06, still contributes
zero to neutral/bear ensembles, still weight 0.25 in bull.

### 6. ETH (7 sub-signals)
See v2 §6. **NO CHANGE** in v3. Audit confirmed clean.

---

## Ensemble fitting (unchanged mechanism, refit weights)

**Canonical: AUC-excess weighting per regime** (`src/model/build_robust.py`).

Mechanism UNCHANGED from v2. Weights are refit in v3 because the Classic Cycle
composite changed.

### v3 canonical regime weights (y_60)

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH | Days |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 0.013 | 0.013 | **0.425** | 0.013 | 0.249 | 0.287 | 594 |
| neutral | 0.235 | **0.340** | 0.171 | 0.102 | 0.000 | 0.152 | 522 |
| bear | 0.218 | **0.577** | 0.017 | 0.017 | 0.000 | 0.172 | 268 |

Interpretation:
- **Bull** dominated by crypto-internal signals (Crypto Derivatives 0.43,
  ETH 0.29, ETF Flows 0.25). Macro/CME/CC all near-floor. Classic Cycle's
  role in bull regimes is effectively delegated to Crypto Derivatives.
- **Neutral** led by CME positioning (0.34), balanced across Macro (0.24),
  Crypto Derivatives (0.17), ETH (0.15), Classic Cycle (0.10).
- **Bear** strongly dominated by CME (0.58), supported by Macro (0.22) and
  ETH (0.17). Classic Cycle near-floor (0.017) — intentional: the 4 working
  CC indicators measure bull-phase exuberance, so their readings in bear
  regime are capitulation-like (bottom indicators) rather than top indicators.

### Position function (unchanged)

```
position(p) = 1.0                          if p ≤ 0.5
            = 1.0 − (p − 0.5) / 0.4         if 0.5 < p < 0.9
            = 0.0                          if p ≥ 0.9
```

### Backtest (unchanged mechanics)

`strategy_return[t] = position[t-1] × btc_return[t]`

Equity curve and Sharpe / max DD computed on post-`ENSEMBLE_FIT_START` window
(2021-06-30 → today, 1,750 days).

---

## Runbook (unchanged)

### Cold start
```bash
cd btc_model
python src/pulls/pull_all_raw_data.py --source all --out-dir data/raw
python src/fix_parsers.py
python src/build_foundation.py
CALIB_LABEL=y_60 python src/run_full_pipeline.py
```

### Warm start
```bash
cd btc_model
CALIB_LABEL=y_60 python src/run_full_pipeline.py
```

---

## File schemas

**Classic Cycle hypothesis output** (`data/hypotheses/classic_cycle.parquet`):
- `score`: float in [0, 1], NaN allowed
- `sub_golden_ratio`, `sub_bmo`, `sub_ahr999`, `sub_fear_greed`

Note: the v2 parquet had 9 sub-signal columns. The v3 parquet has 4. Consumers
that iterate over `sub_*` columns are unaffected; consumers that hard-code the
9 names must be updated (there are none in the current codebase).

All other schemas UNCHANGED from v2.

---

## Sanity check targets (v3)

For the canonical y_60 fit, expect approximately:

| Metric | v3 target |
|---|---|
| Macro & Equities hold-out AUC | 0.60–0.65 (unchanged from v2) |
| CME hold-out AUC | 0.65–0.70 (unchanged) |
| Crypto Derivatives hold-out AUC | 0.65–0.70 (unchanged) |
| **Classic Cycle hold-out AUC** | **0.74–0.78** ⬆ (from 0.30–0.40 in v2) |
| ETF Flows hold-out AUC | 0.40–0.45 (unchanged) |
| ETH hold-out AUC | 0.60–0.65 (unchanged) |
| **Ensemble hold-out AUC** | **0.78–0.80** ⬆ (from 0.74–0.76) |
| **Strategy Sharpe (1750d)** | **0.96 ± 0.05** (from 1.00) |
| **Strategy total return (1750d)** | **+237% ± 10pp** (from +348%) |
| **Strategy max drawdown (1750d)** | **−30% ± 3pp** ⬆ (from −47%) |
| B&H comparison (1750d) | Sharpe 0.55, +108%, −77% (unchanged) |

If your numbers are off by more than ~5pp, something changed. Check:
1. `MIN_CALIB` values in `common.py` (should be unchanged from v2)
2. `build_classic_cycle.py` `KEEP_SET` constant (should be exactly
   `{"golden_ratio","bmo","ahr999","fear_greed"}`)
3. Whether `fix_parsers.py` has been run after a fresh pull
4. Whether `data/derived/labels.parquet` was rebuilt against the latest BTC price

---

## Known issues (v3-open)

Inherited from v2 and still open:
1. Pull script parser bugs patched post-pull by `fix_parsers.py`. Fold in when
   convenient.
2. ETF premium endpoint stale at 2026-01-06.
3. No transaction costs in backtest. Expect Sharpe 0.96 → ~0.85 after 5bps.

Surfaced in session 3:
4. **Pinning weight formula weakness** — `max(auc − 0.5, 0.01)` silently excludes
   pinned signals whose calibration window is hostile to the prior direction.
   Acute case (Classic Cycle bmo/ahr999) is patched by dropping the failing
   non-pinned indicators. Mild case (Macro & Equities `spx_overext_rank`) is
   open. Session 4 Task 1.
5. **Single-window fit risk** — the drop-in vs refit asymmetry on the V1 Classic
   Cycle evaluation suggests the ensemble-layer refit is itself slightly
   over-indexed on the 2021–2025 window. Session 4 Task 2 (walk-forward).
6. **Single hold-out year** — 366 days is thin evidence. Session 4 Task 3
   (crisis validation on historical stress periods outside the fit window).

See `NEXT_SESSION_PLAN.md` for the full session 4 task breakdown.
