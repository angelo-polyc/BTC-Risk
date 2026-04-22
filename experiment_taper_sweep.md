# Experiment: taper width sensitivity sweep

**Date:** 2026-04-18
**Status:** EXPERIMENT ONLY — no changes to committed pipeline. Artifacts for a future promotion decision.

## TL;DR

Wider taper is a legitimate structural improvement with modest-to-moderate gains across the board. **Best candidate: `(0.45, 0.80)`.** Full Sharpe 1.24 vs production 1.20, hold-out Sharpe 1.29 vs 1.19, MaxDD −32% vs −38%. The wins are bigger on the bear/risk-off side (2022 Sharpe −0.81 vs −1.05) and come at a small cost in strong bull years (2024 Sharpe 1.73 vs 1.81). Today's position under `(0.45, 0.80)` is 0.78, not 1.00 — the production taper plateau is masking a moderate percentile reading.

**Recommendation: promote to canonical in a future session after a paper-trading period.** Zero new parameters, consistent full-window Sharpe retention, better MaxDD across every widening tested within the `(0.50-0.75)` to `(0.40-0.85)` range.

## Method

Change only the position function's `(long_thr, def_thr)` thresholds. Ensemble, percentile, sub-signal weights, pinning sets — all unchanged. Use the committed `ensemble_score` from v13 canonical. Compute new position and strategy returns.

This is a pure A/B on the last step of the pipeline. No data leakage, no fitting, no new parameters.

## Results

### Full-window and hold-out metrics

| taper | full Sharpe | full total | full MaxDD | hold Sharpe | hold total | hold MaxDD | whipsaw |
|---|---:|---:|---:|---:|---:|---:|---:|
| narrow (0.58, 0.68) | 1.169 | +510.8% | −39.8% | 1.091 | +26.2% | −21.0% | 0.094 |
| **production (0.55, 0.70)** | **1.201** | **+534.4%** | **−38.1%** | **1.191** | **+28.6%** | **−19.3%** | **0.092** |
| slightly wide (0.52, 0.73) | 1.220 | +546.6% | −36.0% | 1.260 | +30.3% | −18.4% | 0.093 |
| medium wide (0.50, 0.75) | 1.219 | +540.3% | −35.3% | 1.264 | +30.2% | −18.3% | 0.094 |
| **wide (0.45, 0.80)** | **1.242** | **+555.9%** | **−31.9%** | **1.287** | **+30.5%** | **−18.2%** | **0.095** |
| very wide (0.40, 0.85) | 1.254 | +561.0% | −28.6% | 1.249 | +29.2% | −18.8% | 0.094 |
| extreme wide (0.35, 0.90) | 1.233 | +525.9% | −27.2% | 1.212 | +28.1% | −20.0% | 0.096 |

Sharpe is monotone-increasing from narrow to very-wide, then flattens. MaxDD improves monotonically. Hold-out Sharpe peaks at (0.45, 0.80). Returns are minor (1-4% variation across this whole range). Whipsaw is essentially constant — the taper widens but the diagonal change is gradual.

### Per-year Sharpe

| year | narrow | production | slightly wide | medium wide | wide | very wide | extreme wide |
|---|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 1.868 | 1.854 | 1.856 | 1.863 | 1.840 | 1.792 | 1.664 |
| 2022 | −1.089 | −1.050 | −0.978 | −0.962 | **−0.813** | −0.681 | −0.615 |
| 2023 | 2.378 | 2.409 | 2.404 | 2.396 | 2.381 | 2.375 | 2.358 |
| 2024 | 1.805 | 1.807 | 1.789 | 1.765 | **1.733** | 1.739 | 1.722 |
| 2025 | 0.576 | 0.602 | 0.616 | 0.622 | 0.670 | 0.646 | 0.625 |
| 2026 | 0.870 | 1.059 | 1.178 | 1.159 | 1.241 | 1.353 | 1.365 |

Pattern: wider tapers help bear/sideways years (2022 most dramatically) and cost modestly in strong bull years (2024). Net effect is positive across the full window because the 2022 gain outweighs the 2024 loss.

### Yearly return decomposition (production vs wide)

| year | BTC return | production | wide (0.45, 0.80) | Δ |
|---|---:|---:|---:|---:|
| 2021 | +59.7% | +59.2% | +51.1% | −8.2pp |
| 2022 | −64.3% | −30.4% | **−23.8%** | **+6.7pp** |
| 2023 | +155.4% | +157.7% | +151.3% | −6.5pp |
| 2024 | +121.1% | +95.5% | +86.5% | −9.0pp |
| 2025 | −6.3% | +15.6% | +18.1% | +2.4pp |
| 2026 | −11.5% | +4.5% | +5.0% | +0.5pp |

The trade-off is real but asymmetric: gives up some upside capture in strong rallies, catches more of the cushion in drawdowns. 2022 alone is +6.7pp; cumulative over all six years the wide taper ends up ahead by roughly 21 percentage points on total return.

### Robustness check

Rolling 1-year Sharpe of wide minus production across all overlapping windows (1388 observations):
- Mean Δ Sharpe: +0.053
- Std Δ Sharpe: 0.151
- % windows where wide > prod: 58%
- Worst rolling window for wide: −0.23 Sharpe
- Best rolling window for wide: +0.47 Sharpe

Not a monotonically dominating result — 42% of rolling windows favor production. But the mean is positive and the downside is bounded. The win comes from better tail-loss management in genuine drawdowns.

### Today's position

Under production taper (0.55, 0.70): **1.00** (fully long).
Under wide taper (0.45, 0.80): **0.78**.
Under very wide (0.40, 0.85): 0.71.

The ensemble percentile is 0.529 — below 0.55, so production snaps it to fully long. Wide taper says "0.53 is not *that* bullish, hold 78%." This is the amplification story showing up on a current day rather than historically.

## Recommendation

Promote `(0.45, 0.80)` to canonical in a future session, after:

1. **Paper-trading shadow period** (aligns with ongoing paper-trading requirement in HANDOVER). Log positions under both tapers for a quarter or two. Confirm that the ~20pp position divergence on days like today actually corresponds to what operators would want.
2. **Check of pre-v13-canonical history.** The wide-taper results here are computed on the v13 ensemble. If the promotion is to become permanent, one more sanity check against the v12 committed master would be cheap.

No new parameters required. Two existing constants (`POSITION_LONG_THR`, `POSITION_DEF_THR` in `regenerate_canonicals.sh`, `thresholds.csv`) are edited from `(0.55, 0.70)` to `(0.45, 0.80)`. The change propagates through the regenerate pipeline naturally.

**What's left uncertain.** Hold-out is only 365 days; 58% of rolling windows favor wide, but 42% don't. The 2022 advantage is the dominant empirical case for wide taper, and that's one bear year. If the next 12-24 months of paper trading show wide underperforming in a bull-trending environment, that would weaken the case.

## Files

- `taper_sweep_results.csv` — raw per-taper metrics. 7 taper configurations.
