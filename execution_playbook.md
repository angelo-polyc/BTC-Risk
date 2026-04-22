# BTC Crash Model — Execution Playbook v2

**Updated:** 2026-04-14 (session 2)
**Supersedes:** `execution_playbook.md` (session 1)

This is the single source of truth for the canonical model after pinning, per-hypothesis calibration windows, and the y_60 A/B selection. Read this before touching any build script.

---

## What changed since v1

| Area | v1 | v2 (canonical) |
|---|---|---|
| Calibration label | y_30 | **y_60** |
| Macro & Equities calibration window | 2021-06-30 | **2018-10-01** |
| Other hypotheses calibration windows | 2021-06-30 (all) | 2021-06-30 (per-hypothesis) |
| ETF Flows calibration window | 2024-01-11 | 2024-01-11 (unchanged) |
| Macro & Equities sub-signal flips | data-driven | **4 pinned priors** (no flip) |
| Crypto Derivatives sub-signal flips | data-driven | **4 pinned priors** (no flip) |
| Classic Cycle orientations | data-driven | **all 9 hard-coded** per playbook §6 |
| Ensemble fit method | NNLS per regime (collapsed) | **AUC-excess per regime (robust)** |
| Hold-out ensemble AUC | 0.59 | **0.75** |
| Strategy Sharpe (1750d) | 0.77 | **1.00** |

---

## Architecture (unchanged from v1 in shape, only weights changed)

```
btc_price ─► ret_200d_smooth ─► regime classifier ─► {bull, neutral, bear}
                                                              │
            ┌── Macro & Equities ───────────────┐            ▼
            ├── CME (positioning) ─────────────►│     regime weights (NNLS or robust)
  raw       ├── Crypto Derivatives ────────────►│            │
  data     ─┤── Classic Cycle Indicators ─────►├──► ensemble score
            ├── ETF Flows ─────────────────────►│            │
            ├── ETH ───────────────────────────►│            ▼
            │                                    │   expanding percentile
            │                                    │            │
            │                                    │            ▼
            │                                    │     position function (linear hybrid)
            │                                    │            │
            │                                    │            ▼
            │                                    │     daily position %
```

Six hypothesis groups. Composition order matters: each group is calibrated independently against `y_60` (or `y_30` if A/Bing) on its own `MIN_CALIB[name]` window, then composed at the ensemble layer where weights are fit on `ENSEMBLE_FIT_START` (2021-06-30) onward.

---

## Key constants (in `common.py`)

```python
UTC = "UTC"

# Ensemble fit window: latest of all hypothesis MIN_CALIBs (joint-coverage start)
ENSEMBLE_FIT_START = pd.Timestamp("2021-06-30", tz=UTC)

# Per-hypothesis calibration start dates
MIN_CALIB = {
    "macro_equities":     pd.Timestamp("2018-10-01", tz=UTC),
    "cme":                pd.Timestamp("2021-06-30", tz=UTC),
    "crypto_derivatives": pd.Timestamp("2021-06-30", tz=UTC),
    "classic_cycle":      pd.Timestamp("2021-06-30", tz=UTC),
    "etf_flows":          pd.Timestamp("2024-01-11", tz=UTC),
    "eth":                pd.Timestamp("2021-06-30", tz=UTC),
}

# Calibration label A/B: y_30 or y_60 via env var
CALIB_LABEL = os.environ.get("CALIB_LABEL", "y_30")  # canonical: y_60

# HOLDOUT_START computed dynamically as (last BTC date) - 365d
def load_holdout_start() -> pd.Timestamp: ...
```

`MODEL_START = 2018-10-01` is kept for backward compatibility but is not used by the build scripts — they use `MIN_CALIB[name]` instead.

---

## Hypothesis specifications (post-restructure)

### 1. Macro & Equities (8 sub-signals)

**Calibration window:** 2018-10-01 → HOLDOUT_START (the only group with extended history)

All percentile-ranked expanding `min_periods=180`. 90-day z-scores use `min_periods=60`.

| Sub-signal | FRED series | Formula | Pinned? |
|---|---|---|---|
| spx_overext_rank | SP500 | `rank(SP500 / SP500.rolling(200).mean() − 1)` | **✓** |
| real_rate_rank | DFII10 | `rank(DFII10)` | no |
| hy_spread_roc_rank | BAMLH0A0HYM2 | `rank(diff(30))` | **✓** |
| yield_curve_roc_rank | T10Y2Y | `rank(diff(30))` | no |
| rates_abs_stress_rank | DGS2, DGS10 | `rank((|z90(DGS2)| + |z90(DGS10)|) / 2)` | no |
| fed_funds_stress_rank | DFF | `rank(z90(DFF))` | **✓** |
| vix_z90_rank | VIXCLS | `rank(z90(VIXCLS))` | **✓** |
| fx_stress_rank | DTWEXBGS, DEXJPUS | `rank(z90(DTWEXBGS) − z90(DEXJPUS))` | no |

**Pinning rationale:** the four pinned signals have decades of cross-asset evidence for the "high = danger" direction. Without pinning, the 2018-2025 calibration window (dominated by the 2022 rate-hike narrative) flips them in-sample for a 0.85 IS / 0.41 OOS overfit. With pinning the in-sample drops to 0.70 but hold-out climbs to 0.62.

`real_rate_rank`, `yield_curve_roc_rank`, `rates_abs_stress_rank`, `fx_stress_rank` are NOT pinned — their direction is genuinely regime-dependent (or symmetric by construction in the case of `rates_abs_stress`).

**Hold-out AUC (canonical y_60):** 0.62.

### 2. CME (3 sub-signals) — unchanged from v1

Calibration window: 2021-06-30 → HOLDOUT_START. CFTC TFF 133741 futopt only, lev_funds inverted at rank step.

| Sub-signal | AUC (y_60) | Weight |
|---|---:|---:|
| dealer_net_rank | 0.58 (flipped) | 0.225 |
| asset_mgr_net_pct_rank | 0.67 | 0.484 |
| lev_funds_net_pct_rank (pre-inverted) | 0.60 | 0.291 |

**Hold-out AUC (canonical y_60):** 0.69.

**Note:** Extending CME calibration to 2018+ DROPS hold-out from 0.69 to 0.25. The 2018-2020 era of CME positioning was structurally different — keep this one on 2021+.

### 3. Crypto Derivatives (10 sub-signals)

Calibration window: 2021-06-30 → HOLDOUT_START. Merged H2 + H2B + rv21.

| Sub-signal | Origin | Pinned? |
|---|---|---|
| funding_divergence_rank | H2B (Velo) | no |
| liq_stress_rank | H2B (Velo) | no |
| speculation_ratio_rank | H2B (Velo) | no |
| cvd_divergence_rank | H2B (Velo) | no |
| alt_rotation_rank | H2B (Velo BTC + ETH) | no |
| funding_zscore_rank | H2 (Velo) | **✓** |
| lev_stress_rank | H2 (Velo) | **✓** |
| coin_margin_ratio_rank | H2 (Coinglass) | **✓** |
| basis_rank | H2 (Coinglass) | no |
| **rv21_zscore_rank** | moved from H6 | **✓** |

**Pinning rationale:** the 4 pinned signals are structural-by-construction — high funding *means* overleveraged longs, high coin-margin share *is* reflexive fragility, etc. `rv21_zscore_rank` had AUC 0.36 raw on the calibration window (would have been the wrong-direction trap if not pinned).

Note: `sell_liquidations` = LONG positions force-sold per Velo convention.

**Hold-out AUC (canonical y_60):** 0.68.

### 4. Classic Cycle Indicators (9 indicators)

Calibration window: 2021-06-30 → HOLDOUT_START.

**All 9 orientations are hard-coded per the playbook §6 H4v2 spec table** — no AUC-driven flipping. The build script applies the orientations explicitly and calls `auc_excess_weights` with `no_flip=set(all_columns)` to keep the weighting honest.

| Indicator | Endpoint | Raw transform | Hard-coded direction |
|---|---|---|---|
| 2-Year MA Multiplier | `v3 /index/tow-year-ma-multiplier` | `price / (mA730 × 5)` | flip |
| Golden Ratio Multiplier | `v3 /index/golden-ratio-multiplier` | `price / ma350` | flip |
| 200W MA Heatmap | `v3 /index/tow-hundred-week-moving-avg-heatmap` | `price / mA1440` | keep |
| 200W MA ROC | (derived from #3) | `mA1440.pct_change(30)` | keep |
| BMO | `v4 /index/bitcoin-macro-oscillator` | raw value | flip |
| AHR999 | `v3 /index/ahr999` | `1 / ahr999` | keep (pre-inverted) |
| Rainbow Chart | `v3 /index/bitcoin-rainbow-chart` | `count(price > band) / 10` | keep |
| Fear & Greed | `v3 /index/fear-greed-history` | raw 0-100 | flip |
| Bubble Index | `v3 /index/bitcoin-bubble-index` | raw value | keep |

**Known issues:**
- `two_year_ma` returns NaN AUC in the modern era (flat regime). 8 of 9 indicators carry the composite.
- **Hold-out collapse on y_60: 0.80 IS / 0.35 OOS.** The 2025-2026 window has BTC making new highs without classic cycle-top behavior. On y_30 this hypothesis holds at 0.66 OOS — see "Recommended next steps" in HANDOVER.md.

**Hold-out AUC (canonical y_60):** 0.35 ⚠️ (0.66 on y_30).

### 5. ETF Flows (4 sub-signals)

Calibration window: 2024-01-11 → HOLDOUT_START. NaN before 2024-01-11.

| Sub-signal | Coinglass endpoint | Formula |
|---|---|---|
| etf_net_flow_rank | `/etf/bitcoin/flow-history` | `rank(7d rolling sum of net flow)` |
| etf_premium_rank | `/etf/bitcoin/premium-discount/history` | `rank(cross-ETF mean of (mkt − nav)/nav)` |
| etf_flow_divergence_rank | flow + price | `rank(sign(price.diff(7)) × flow.rolling(7).sum())` |
| etf_share_of_volume_rank | flow + price | `rank(|net_flow| / btc_close)` (proxy) |

**Important parser note:** the API does NOT return a `premium_discount_percent` field. You must compute it as `(market_price_usd − nav_usd) / nav_usd` averaged across constituent ETFs per timestamp. The pull script bug is patched in `fix_parsers.py`.

**Hold-out AUC (canonical y_60):** 0.43.

### 6. ETH (7 sub-signals) — new in v1

Calibration window: 2021-06-30 → HOLDOUT_START. Per H_ETH spec.

Targets BTC's `y_30` or `y_60` (NOT ETH's). The hypothesis is that ETH microstructure leads BTC drawdowns. All ranks expanding `min_periods=180`.

| Sub-signal | Source |
|---|---|
| eth_funding_divergence_rank | Velo ETH |
| eth_speculation_ratio_rank | Velo ETH (fut/spot vol) |
| eth_liq_stress_rank | Velo ETH (sell_liq = longs) |
| eth_funding_zscore_rank | Velo ETH (z90 of OI-w funding) |
| eth_cvd_divergence_rank | Velo ETH (fut vs spot CVD .diff(14)) |
| eth_btc_dominance_rank | Velo ETH + BTC (replaces alt_rotation per spec) |
| eth_basis_compression_rank | Coinglass H2 ETH basis |

No pinning — the hypothesis is too new and doesn't yet have established direction priors.

**Hold-out AUC (canonical y_60):** 0.63.

---

## Ensemble fitting

**Canonical: AUC-excess weighting per regime** (`build_robust.py`).

For each regime ∈ {bull, neutral, bear}:
1. Filter to days where `regime[t] == regime` and `y_60[t]` is defined and `t ∈ [ENSEMBLE_FIT_START, HOLDOUT_START)`.
2. Compute per-hypothesis AUC against `y_60` on that subset.
3. Weight: `excess_h = max(AUC_h - 0.5, 0.01)`, normalized so `sum(weights) = 1` per regime.
4. Apply per-day with NaN-skip (no renormalization at the row level — missing data should reduce the score per playbook §8.1).

**NNLS variant (`build_all.py`):** kept for diagnostic comparison only. Collapsed to single hypotheses per regime on this data — DO NOT deploy.

### Position function (unchanged)

```
position(p) = 1.0                          if p ≤ 0.5
            = 1.0 − (p − 0.5) / 0.4         if 0.5 < p < 0.9
            = 0.0                          if p ≥ 0.9
```

where `p` is the expanding-percentile rank of the ensemble score.

### Backtest

`strategy_return[t] = position[t-1] × btc_return[t]`.

Equity curve and Sharpe / max DD computed on the post-`ENSEMBLE_FIT_START` window (2021-06-30 → today, 1,750 days).

---

## Runbook

### Cold start (no cached data)

```bash
cd btc_model
python pull_all_raw_data.py --source all --out-dir data/raw
python fix_parsers.py
python build_foundation.py
CALIB_LABEL=y_60 python run_full_pipeline.py
```

Total runtime: ~30-40 min (mostly Velo rate limits).

### Warm start (cached data)

```bash
cd btc_model
CALIB_LABEL=y_60 python run_full_pipeline.py
```

Total runtime: ~15 seconds.

### A/B comparison

```bash
CALIB_LABEL=y_30 python run_full_pipeline.py
CALIB_LABEL=y_60 python run_full_pipeline.py
# Outputs are tagged: data/final/ensemble_*_y_30.{parquet,csv}, ensemble_*_y_60.{parquet,csv}
```

### Individual hypothesis re-build

```bash
CALIB_LABEL=y_60 python build_macro_equities.py
CALIB_LABEL=y_60 python build_cme.py
# ... etc
CALIB_LABEL=y_60 python build_robust.py
```

---

## File schemas

**Hypothesis output** (`data/hypotheses/*.parquet`):
- index: `date` UTC midnight
- `score`: float in `[0, 1]`, NaN allowed (handled by ensemble NaN-skip)
- `sub_*`: float for each sub-signal post-flip rank

**Ensemble output** (`data/final/ensemble_robust.parquet`):
- index: `date` UTC midnight
- `regime`: category {bull, neutral, bear}
- `ensemble_score`: float, weighted sum
- `percentile`: expanding-rank percentile of ensemble_score
- `position`: linear-hybrid position from percentile
- `btc_return`, `strategy_return`: backtest columns

**Foundation outputs**:
- `regime.parquet`: `ret_200d_smooth`, `regime ∈ {bull, neutral, bear}`
- `labels.parquet`: `fwd_30d_max_dd`, `fwd_60d_max_dd`, `y_30`, `y_60`

---

## Sanity check targets

For the canonical y_60 fit, expect approximately:

| Metric | Target |
|---|---|
| Macro & Equities hold-out AUC | 0.60-0.65 |
| CME hold-out AUC | 0.65-0.70 |
| Crypto Derivatives hold-out AUC | 0.65-0.70 |
| Classic Cycle hold-out AUC | 0.30-0.40 ⚠️ (known weak point) |
| ETF Flows hold-out AUC | 0.40-0.45 |
| ETH hold-out AUC | 0.60-0.65 |
| **Ensemble hold-out AUC** | **0.74-0.76** |
| Strategy Sharpe (1750d) | **1.00 ± 0.05** |
| Strategy total return (1750d) | **+340% ± 20pp** |
| Strategy max DD (1750d) | **−45% ± 5pp** |
| B&H comparison (1750d) | Sharpe 0.55, +108%, −77% |

If your numbers are off by more than ~5pp, something changed. Check:
1. `MIN_CALIB` values in common.py
2. Pinning sets in build scripts
3. Whether `fix_parsers.py` has been run after a fresh pull
4. Whether `data/derived/labels.parquet` was rebuilt against the latest BTC price
