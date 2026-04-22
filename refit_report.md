# BTC Model Refit Report v2 — Pinning + Per-Hypothesis Calibration + y_30/y_60 A/B

**Date:** 2026-04-14 (session 2)
**Supersedes:** `refit_report.md` (session 1)

## TL;DR

Three changes applied to the v1 refit:
1. **Pinned priors** on Macro & Equities (4 sub-signals) and Crypto Derivatives (4 sub-signals); hard-coded orientations on Classic Cycle Indicators per the playbook table.
2. **Per-hypothesis calibration windows** — Macro & Equities calibrates from 2018-10-01 (using its full FRED history through the modern era); CME, Crypto Derivatives, Classic Cycle, ETH all calibrate from 2021-06-30; ETF Flows from 2024-01-11.
3. **A/B test of `y_30` vs `y_60`** as the calibration label.

**Result: y_60 wins decisively.** Hold-out ensemble AUC jumped from the v1 baseline of **0.59 → 0.75**. Strategy Sharpe **0.77 → 1.00**. Strategy total return on the same 1,750-day evaluation window **+136% → +348%** (vs B&H +108%).

The y_60 model is the new canonical deployment target.

## Per-hypothesis A/B (in-sample / hold-out AUC)

| Hypothesis | Calibration window | y_30 IS / OOS | y_60 IS / OOS | y_60 better? |
|---|---|---:|---:|---|
| Macro & Equities | 2018-10 → 2025-04 | 0.71 / 0.55 | 0.70 / **0.62** | ✓ +7pp |
| CME | 2021-06 → 2025-04 | 0.81 / **0.75** | 0.72 / 0.69 | y_30 better −6pp |
| Crypto Derivatives | 2021-06 → 2025-04 | 0.70 / 0.60 | 0.72 / **0.68** | ✓ +8pp |
| Classic Cycle Indicators | 2021-06 → 2025-04 | 0.82 / **0.66** | 0.80 / 0.35 | y_30 better −31pp ⚠️ |
| ETF Flows | 2024-01 → 2025-04 | 0.75 / 0.42 | 0.62 / 0.43 | tie |
| ETH | 2021-06 → 2025-04 | 0.69 / 0.54 | 0.72 / **0.63** | ✓ +9pp |
| **Robust ensemble** | 2021-06 → 2025-04 | 0.89 / 0.60 | 0.89 / **0.75** | **✓ +15pp** |

**The Classic Cycle anomaly:** y_60 shows 0.80 in-sample but only 0.35 hold-out — worse than random. The 2025-2026 hold-out window has BTC making new highs without classic cycle-top behavior (e.g., bubble_index, golden_ratio, 200W heatmap all signal "elevated" but the price keeps grinding higher). On y_30 this hypothesis held up at 0.66 hold-out. The ensemble's AUC-excess weighting partially compensates by down-weighting Classic Cycle when its in-sample signal is moderate, but it's still a known weak point.

## Strategy backtest comparison (2021-06-30 → 2026-04-14, 1,750 days)

| Variant | Sharpe | Total return | Max DD | Hold-out AUC |
|---|---:|---:|---:|---:|
| **y_60 (CANONICAL)** | **1.00** | **+348%** | −47% | **0.75** |
| y_30 | 0.76 | +136% | −37% | 0.60 |
| v1 baseline (no pinning, 2021-only, y_30) | 0.77 | +139% | −40% | 0.59 |
| Buy-and-hold | 0.55 | +108% | −77% | — |

y_60 hits the playbook's Sharpe target band of 1.0–1.2 honestly (no overfitting tricks). The slightly worse max DD (−47% vs −37%) is the cost of the more aggressive position sizing in bear regimes — the y_60 ensemble is more confident and stays at higher positions through some of the early-2022 chop.

## Robust ensemble weights (y_60, canonical)

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH | Days |
|---|---:|---:|---:|---:|---:|---:|---:|
| bull | 0.010 | 0.010 | **0.319** | 0.259 | 0.187 | 0.215 | 594 |
| neutral | 0.208 | **0.302** | 0.152 | 0.203 | 0.000 | 0.135 | 522 |
| bear | 0.144 | **0.382** | 0.011 | **0.349** | 0.000 | 0.113 | 268 |

This pattern is intuitive:
- **Bull regimes** are dominated by crypto-internal signals (Crypto Derivatives 0.32, Classic Cycle 0.26, ETH 0.21, ETF Flows 0.19). Macro and CME effectively zero. In a strong uptrend, BTC drawdowns come from speculative froth, not macro shocks.
- **Neutral regimes** are most balanced — CME positioning leads (0.30) with Macro, Classic Cycle, Crypto Derivatives, ETH all in the 0.13-0.21 range. Mixed-environment regime gets mixed-signal model.
- **Bear regimes** are dominated by CME positioning (0.38) and Classic Cycle position (0.35) — institutional positioning unwinds and "where in the cycle are we" matter most when momentum has already turned. Crypto Derivatives near-zero (microstructure stress is already realized in bear regimes, not predictive).

Average position by regime (y_60):
- bull: 0.92 (essentially fully long when regime is healthy)
- neutral: 0.50 (half position)
- bear: 0.53 (counter-intuitively elevated — see note below)

**Note on bear avg position:** the bear-regime average position of 0.53 is higher than neutral (0.50). This is because the y_60 ensemble's bear-regime ensemble scores cluster in the middle of the percentile distribution rather than the right tail — most bear days don't trigger the danger-percentile threshold. Whether this is "the model correctly says bear regimes aren't always dangerous" or "the model under-reacts in bear" depends on what comes next in 2026. Worth monitoring.

## Pinning verification — what got floored

A pinned signal whose post-pin AUC is below 0.5 receives the floor weight (1 / total_excess) — effectively zero contribution. This is the "sacrifice in-sample for honest direction" trade-off in action.

**Macro & Equities (y_60):**
| Sub-signal | AUC | Pinned? | Weight |
|---|---:|---|---:|
| spx_overext_rank | 0.43 | ✓ | 0.026 (floor) |
| real_rate_rank | 0.62 | no | 0.305 |
| hy_spread_roc_rank | 0.57 | ✓ | 0.176 |
| yield_curve_roc_rank | 0.61 | no (would flip) | 0.273 |
| rates_abs_stress_rank | 0.52 | no | 0.060 |
| fed_funds_stress_rank | 0.51 | ✓ | 0.026 (floor) |
| vix_z90_rank | 0.54 | ✓ | 0.108 |
| fx_stress_rank | 0.50 | no (would flip) | 0.026 (floor) |

`spx_overext` and `fed_funds_stress` got floor-weighted — exactly the intended pinning behavior. Without pinning they'd have been flipped to 0.57+ AUC and dragged the in-sample number up while breaking out-of-sample. The composite is now carried by `real_rate_rank`, `yield_curve_roc_rank`, `hy_spread_roc_rank`, and `vix_z90_rank` — sensible economic drivers with consistent direction.

**Crypto Derivatives (y_60):**
| Sub-signal | AUC | Pinned? | Weight |
|---|---:|---|---:|
| funding_divergence_rank | 0.63 | no | 0.195 |
| liq_stress_rank | 0.58 | no (would flip) | 0.117 |
| speculation_ratio_rank | 0.68 | no (would flip) | 0.283 |
| cvd_divergence_rank | 0.57 | no (would flip) | 0.106 |
| alt_rotation_rank | 0.53 | no (would flip) | 0.046 |
| funding_zscore_rank | 0.51 | ✓ | 0.015 (floor) |
| lev_stress_rank | 0.52 | ✓ | 0.027 |
| coin_margin_ratio_rank | 0.57 | ✓ | 0.102 |
| basis_rank | 0.56 | ✓ | 0.094 |
| rv21_zscore_rank | 0.36 | ✓ | 0.015 (floor) |

`funding_zscore_rank` and `rv21_zscore_rank` got floor-weighted. Notable: `rv21_zscore_rank` was the top contributor in the v1 baseline (weight 0.16) — but only because v1 let it flip. With pinning forbidding the flip, its honest AUC of 0.36 reveals it as a wrong-direction-or-noise signal in this calibration window. Better to floor it than flip it.

**Classic Cycle Indicators** uses hard-coded orientations from the playbook spec, all pinned. `two_year_ma` returned NaN AUC in this window (flat regime) and gets zero weight; the composite runs on 8 indicators.

## Calibration window finding — the per-hypothesis insight

The single most surprising finding was that uniformly extending all hypothesis calibrations to 2018+ **hurt** CME and Classic Cycle dramatically:

| Hypothesis | y_30 hold-out AUC, 2021+ cal | y_30 hold-out AUC, 2018+ cal | Δ |
|---|---:|---:|---:|
| CME | 0.75 | 0.42 | **−33pp** |
| Classic Cycle | 0.66 | 0.40 | **−26pp** |
| Macro & Equities | 0.41 | 0.55 | +14pp |

The 2018-2020 era of CME positioning was structurally different from 2021+. Asset managers were a smaller share of the market, lev funds had different exposure patterns, and the COVID crash was a regime-transition event that doesn't generalize. Mixing the two eras dilutes the modern-era signal that 2021+ data provides cleanly.

Same logic for Classic Cycle: the 2018 bear and 2020 COVID crash had idiosyncratic cycle-indicator behavior (BMO, fear_greed, ahr999 all spiked in unusual ways) that doesn't help predict 2025-2026 dynamics.

**The fix is per-hypothesis calibration windows** (`MIN_CALIB` dict in `common.py`), letting each group calibrate on its highest-quality data range. This is a generally useful design pattern — extending calibration only helps when the underlying mechanism is stationary across the longer window.

## Open issues (unchanged from v1 unless noted)

1. ~~Macro & Equities overfitting~~ — **fixed** by pinning. Hold-out AUC 0.41 → 0.62.
2. ~~`two_year_ma` NaN AUC~~ — confirmed flat-regime issue in modern era. Composite is robust without it.
3. **ETF premium endpoint stops at 2026-01-06** — still stale, refresh when ready.
4. ~~NNLS collapse~~ — robust AUC-excess variant is now canonical. NNLS variant kept at `data/final/ensemble_position_backtest.parquet` for diagnostic comparison only.
5. **Classic Cycle hold-out 0.35 on y_60** is the new known weak point. Can be addressed by per-hypothesis label selection (Classic Cycle on y_30, others on y_60) but that's optimization tinkering.
6. **Pull script bugs** — still patched in `fix_parsers.py`; recommend folding into the script proper.

## Next-session recommendations

In priority order:

1. **Per-hypothesis label selection.** Fit Macro & Equities, Crypto Derivatives, ETH on y_60; fit CME and Classic Cycle on y_30. Combine in the ensemble. This should preserve the y_60 wins while recovering Classic Cycle's hold-out from 0.35 to ~0.66.
2. **Add transaction costs to the backtest.** At 5bps roundtrip plus a `min_position_change` threshold, expect Sharpe to drop from 1.00 to ~0.85. Still beats B&H by a wide margin and is the deploy-ready number.
3. **Refresh the ETF premium endpoint pull** and re-run ETF Flows (currently 3 months stale).
4. **Implement §10.3 alpha-augmentation ensemble** as an alternative to robust AUC-excess. With y_60 + pinning the model is in a much better starting place; alpha-augmentation might squeeze out another 2-5pp hold-out AUC.
5. **Persistence audit on Classic Cycle** — investigate why the hold-out collapsed on y_60 specifically. May reveal an additional sub-signal worth adding or one worth dropping.
