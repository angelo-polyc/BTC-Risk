# Refit Report v7 — Canonical Selection, Failed Experiments, Comms Strategy

**Session:** 7
**Date:** 2026-04-15
**Status:** wf365 selected as canonical. Sub-signal walk-forward test FAILED (regression −163pp full-window total return). Calibration test FAILED OOS (Brier worse than constant base rate; magnitude Spearman destroyed by GBM). Comms layer designed. Strategy for session 8 calibration retry documented.

---

## Part 1 — Canonical selection: wf365 over sf730

### Decision

**wf365 is the deployed canonical going forward. sf730 is preserved as a frozen baseline reference but no longer ships in primary outputs.**

### Selection criteria

Three criteria, equally weighted:

1. **Robustness** — does the model behave reasonably across regimes and through the historical periods most likely to inform whether it generalizes
2. **Accuracy** — hold-out AUC, Sharpe, and crisis-period catch behavior
3. **Scalability into the future** — does the model adapt to new regimes via its architecture, or does it depend on fitted weights from a specific historical window

### Comparison

| Dimension | sf730 | wf365 | Winner |
|---|---|---|---|
| Full-window Sharpe (post-cost) | 1.175 | 1.022 | sf730 |
| Hold-out Sharpe (post-cost) | 0.394 | 0.461 | wf365 |
| Full-window total return | +519% | +364% | sf730 |
| Hold-out total return | +9.1% | +9.6% | wf365 |
| Max drawdown (full window) | −26.7% | −40.9% | sf730 |
| Crisis period catches | 3 of 4 | 4 of 4 | wf365 |
| Worst crisis miss | FTX (−16% vs −20% B&H, +3pp) | FTX (−7% vs −20% B&H, +13pp) | wf365 |
| Currently in drawdown | yes, 250d ongoing (worst ever) | no | wf365 |
| Behavior in 2025-26 hold-out | weakening | stable | wf365 |
| Architectural adaptation | none — single fit | monthly walk-forward | wf365 |

### Why sf730's apparent edge is an artifact

sf730 wins on **full-window** metrics (Sharpe 1.175 vs 1.022, total +519% vs +364%, MaxDD −26.7% vs −40.9%). Three reasons this is misleading:

1. **In-sample bias.** sf730's weights were fit on data through 2024-04 and applied to 2021-2024. The same window appears in both training and evaluation. Its full-window Sharpe is partially measuring fit quality, not predictive quality.
2. **LUNA timing luck.** sf730's bear-regime weights happened to be ETH-derivatives-heavy in a way that captured the LUNA 2022 crash particularly well. This is a single-event windfall, not generalizable.
3. **wf365's hold-out includes 2024-2025**, a period sf730 doesn't see during training. Apples-to-apples comparison must be done on the hold-out year, where wf365 wins on every metric.

### Why the architectural argument matters

sf730 is a snapshot. Its weights froze in 2024-04 and will degrade as market structure evolves. Six months from now, sf730's bull-regime weights might be increasingly inappropriate for what bull-regime conditions look like in late 2026.

wf365 refits monthly. The same-day prediction in November 2026 will use weights computed on data through October 2026, capturing whatever has changed in the intervening period. The architecture is what makes the model evergreen — the wf365 framework can run for 5 years without code changes.

This is the criterion-3 win that ties the decision.

### What "frozen baseline" means for sf730

`master_daily_view_sf730.csv` and the sf730 weights remain in the project. They are useful as:
- A historical comparison point (this is what a single-fit looked like in 2024)
- An empirical control for any future model variant (if a new variant beats wf365 but not sf730, that's signal something is wrong)
- A backup deployable in case wf365 has an unexpected operational issue

But sf730 should not appear in daily comms, position decisions, or risk reporting. Single source of truth = wf365.

---

## Part 2 — Sub-signal walk-forward: failed test

### What was tested

The original session-4 plan had a phase-2 extension to push walk-forward refitting *down* one level — from the ensemble layer (where it currently lives) to the sub-signal layer inside each hypothesis. The hypothesis: if the ensemble adapts via walk-forward, the hypothesis composites might also benefit from per-month sub-signal weight refits.

### Implementation

Built a generalized `walkforward_subsig()` helper that mirrors ensemble walk-forward at the sub-signal layer. Pre-orient sub-signals using the existing single-fit flips, then refit magnitude weights monthly on expanding window with 18-month warmup. ETF Flows used 12-month warmup due to limited history.

Per-hypothesis: 40-73 monthly fits depending on data availability. ETF: 15 fits.

### Apples-to-apples results vs canonical wf365

Same pipeline applied to canonical composites (baseline) vs walk-forward composites (treatment):

| Window | Metric | Baseline | Sub-sig WF | Δ |
|---|---|---:|---:|---:|
| Full 1750d | Sharpe | 1.026 | 0.772 | **−0.254** |
| Full 1750d | Total | +363% | +200% | **−163pp** |
| Full 1750d | MaxDD | −41.0% | −51.3% | **−10.3pp** |
| Hold-out year | Sharpe | 0.517 | 0.389 | **−0.128** |
| Hold-out year | Total | +11.1% | +7.3% | −3.8pp |
| Hold-out year | MaxDD | −29.3% | −33.4% | −4.1pp |

Ensemble OOS AUC: 0.7733 → 0.7158 (**−5.8pp**, large regression).

### Crisis behavior

| Period | Variant | Max %ile | Mean position | Period total |
|---|---|---:|---:|---:|
| 2020 COVID | baseline | 0.953 | 0.549 | +21.1% |
| 2020 COVID | sub-sig WF | 0.981 | 0.578 | **+4.4%** |
| 2022 LUNA | baseline | 0.992 | 0.483 | −27.9% |
| 2022 LUNA | sub-sig WF | 0.926 | 0.607 | −30.8% |
| 2022 FTX | baseline | 0.904 | 0.635 | −6.9% |
| 2022 FTX | sub-sig WF | **0.652** | **0.975** | **−11.3%** |

Sub-signal walk-forward sat at **97% long mean position** through the entire FTX crisis. Max percentile only 0.65 — never came near the 0.9 trigger. **Completely missed FTX** despite the canonical catching it.

### Specific diagnostic: spx_overext rescue test

The original session-4 hypothesis was that walk-forward at sub-signal level might naturally rescue the `spx_overext_rank` signal (IS AUC 0.43, OOS AUC 0.67) by letting hostile early calibration windows roll off as new data is added.

| Fit date | spx_overext weight | AUC |
|---|---:|---:|
| 2020-04-01 | 0.0150 | 0.483 |
| 2022-04-01 | 0.0228 | 0.468 |
| 2024-04-01 | 0.0223 | 0.431 |
| 2026-04-01 | 0.0251 | 0.464 |

Weight stayed at floor (~0.025) **across every single one of 73 monthly fits**. AUC stayed below 0.5 across every single fit. The expanding-window walk-forward **never let the hostile early period age out** — it just kept adding to it. spx_overext's good 2024-25 OOS performance got drowned by the long hostile history that dominated the expanding training set.

### Architectural failure mode

The deeper problem: when sub-signal weights walk forward, the hypothesis composite series **stops being a stable signal**. The ensemble layer fits weights based on hypothesis composite IS AUC. But when sub-signal weights refit monthly, the composite series itself changes character at every fit boundary. The composite for 2023-Q1 was computed with one set of sub-signal weights; the composite for 2023-Q2 was computed with a different set.

The ensemble's IS AUC then measures "AUC of a frankenstein time series" instead of "AUC of a stable signal." Per-hypothesis IS AUC dropped 4-11pp across 5 of 6 hypotheses (macro −10.9, eth −8.9, classic_cycle −6.2, crypto_derivatives −3.9, etf_flows −4.2; only cme +1.2). Those degraded IS AUCs are the inputs to ensemble weight calculation. Noisy inputs → bad ensemble weights → ensemble OOS AUC dropped 5.8pp → strategy regressed across every metric.

**Cascading walk-forwards is the failure mode.** Walk-forward at one layer is fine. Walk-forward at two layers couples them in a way that breaks the AUC-excess fitting machinery.

### Verdict

Reject. The current architecture (frozen sub-signal weights, walk-forward ensemble) is the correct configuration. Adaptation happens at the layer where it works (ensemble), not at the layer where it breaks signal stability the upper layer needs (sub-signals).

The "scalable into the future" argument from session 4 was wrong about needing two-layer walk-forward. Single-layer walk-forward at the ensemble is sufficient.

---

## Part 3 — Cardinal calibration: failed test

### What was tested

Bolt-on calibrators to convert `ensemble_score` into:
- **Drawdown probability:** P(drawdown ≥20% in 60d) via isotonic regression and Platt scaling
- **Drawdown magnitude:** 10/50/90 percentile of `fwd_60d_max_dd` via GBM quantile regression

Walk-forward with monthly cadence on expanding window, matching the ensemble layer.

### Validation gates

For probability:
- Brier score better than constant-base-rate prediction (in-sample base rate = 0.197, hold-out base rate = 0.344)
- Expected Calibration Error (ECE) < 0.10

For magnitude:
- Spearman correlation > 0.30 OOS
- (q10, q90) coverage in [70%, 90%]

### Probability results

| Calibrator | Window | Brier | ECE | Mean predicted | Realized rate |
|---|---|---:|---:|---:|---:|
| Isotonic | IS | 0.121 | 0.109 | 0.267 | 0.197 |
| Isotonic | OOS | **0.297** | 0.320 | **0.058** | 0.344 |
| Platt | IS | 0.130 | 0.147 | 0.310 | 0.197 |
| Platt | OOS | **0.265** | 0.237 | 0.111 | 0.344 |
| Constant base rate | OOS | 0.226 | — | — | — |

**Both calibrators fail OOS.** Both have Brier scores **worse than constant base-rate prediction**. Mean predicted probability of 6% (isotonic) vs realized 34% — off by 6×.

### Magnitude results

| Predictor | IS Spearman | OOS Spearman | OOS Coverage |
|---|---:|---:|---:|
| Raw ensemble_score | −0.376 | **−0.475** | — |
| GBM q50 | +0.455 | **+0.055** | 59.7% |

(Negative Spearman is correct direction: high ensemble_score → more negative realized DD. Positive predicted q50 → more negative magnitude. Sign convention: |Spearman| matters, signs depend on what's being correlated.)

**The GBM quantile prediction destroyed the rank signal that already existed in raw ensemble_score.** Raw `ensemble_score` had OOS Spearman 0.475; GBM q50 had 0.055. The fancy modeling made the prediction worse than no modeling.

(q10, q90) coverage 60% vs 80% target — **bands too narrow, undercover tail risk**.

### The structural reason both failed

The hold-out year (2025-04 → 2026-04) had a **structural shift in drawdown frequency**:
- In-sample (2022-2024): y_60 base rate 0.197
- Hold-out (2025-26): y_60 base rate 0.344

The hold-out includes the 2025-26 BTC drawdown from $115k to $65k, which produced lots of high-magnitude `fwd_60d_max_dd` values that nothing in the training distribution prepared the calibrator for.

**Critically, the position model itself handled this correctly** — wf365 went defensive Sept-Feb during exactly when the realized drawdown was building. The position function's percentile basis is *regime-relative* (rolling-365), so it adapts to recent base rates by construction. The probability calibrator is *regime-absolute* — it locks predictions to historical base rates.

That asymmetry is the structural problem. **Adding calibrated absolute probabilities onto a regime-relative model breaks specifically when you'd want them most: in regime-shift periods.**

### Strategy for session 8 retry

Three architectural moves address the failure mode without changing the model:

1. **Switch to rolling-window calibration** (trailing 365d, matching the percentile window). Makes calibration regime-relative. Lower sample size per fit but adapts to recent base rates.
2. **Replace GBM with linear Huber regression** for magnitude. Huber is robust to outliers and preserves the rank ordering of ensemble_score that GBM destroyed. Output as `predicted ± σ_huber` band.
3. **Validation gate before shipping.** Same gates as above. If rolling-window also fails, **do not iterate further** — ship the contextualized percentile fallback (Option 1 in session 7 strategy memo).

If session 8 calibration retry fails: ship "today's percentile is X, days at this percentile in the trailing 365d had drawdowns on Y% of follow-up windows, median magnitude −Z%" instead of an absolute probability. Same qualitative information without a falsifiable absolute claim.

### Verdict

The session 7 attempt failed and should not be deployed. Session 8 retry has a specific design that addresses the failure mode. If retry also fails, the cardinal output is not achievable from this architecture and the contextualized-percentile fallback is the correct ship.

---

## Part 4 — Comms layer design

### Use case

Model output is consumed for **discretionary input + risk overlay** decisions. The user reads the model daily, combines with personal judgment, and makes decisions about (a) directional view of the cycle, (b) hedging an existing BTC position. Not systematic; not delegated.

### Communication cadences

**Daily snapshot** — fires every day, even quiet ones.
- 5-line text message: position + delta from yesterday, regime, top 2 contributors with weights, distance to thresholds, pipeline health
- Daily chart attached
- Designed for 15-second glance

**Weekly digest** — Monday morning.
- ~20-line message: position trajectory, hypothesis changes, walk-forward weight updates, operational health
- Weekly chart attached
- Skipped if truly quiet week (no events, position barely moved)

**Event alerts** — fired on triggers, not scheduled.
- Triggers: position |Δ| > 0.3, position crosses 0.0/1.0, percentile crosses 0.9, regime transitions, hypothesis composite spike >2σ, walk-forward weight |Δ| > 0.1, pipeline failures
- Each alert has its own focused chart format
- State file for dedup — don't fire same trigger twice for same event
- Target: 5-15 alerts per year (not weekly)

### Daily chart spec (prototyped: `make_daily_chart.py`)

3 panels stacked, ~1100×750px, trailing 365 days, shared x-axis:

1. **BTC price (log) + position color band** (50% of height)
   - BTC close as black line
   - Position as colored strip below: green=1.0, red=0.0
   - Title shows date + position + regime + percentile
2. **Ensemble percentile** (25%)
   - Single line with thresholds at 0.5 (long) and 0.9 (defense)
   - Shaded zones above 0.9 (red) and below 0.5 (green)
3. **6 hypothesis composites** (25%)
   - Each hypothesis as a thin colored line, all on 0-1 axis
   - Values labeled at right edge (staggered to prevent overlap)
   - Colors fixed per hypothesis (consistency across runs)

The chart is the same every day. Consistency is what makes it become a perceptual instrument that flags anomalies automatically.

### Weekly chart spec (not prototyped)

4 panels, ~1200×900px, trailing 365 days:
1. Equity curve vs B&H (with drawdown sub-band)
2. BTC price + position band (same as daily)
3. Ensemble percentile (same as daily)
4. **wf365 weight evolution for current regime over last 12 monthly fits** (new — shows how the model is adapting)

### Event alert chart specs

Different per event type. See session 7 strategy memo for details. Common pattern: alert chart focuses on the event, not the standard daily view.

### Items to add to comms if cardinal calibration ships in session 8

- 4th panel on daily chart for drawdown probability time series
- Probability and magnitude rows added to daily message
- New event alert: probability crosses 50% or magnitude q90 worse than −30%

If calibration fails session 8 retry too, comms ships without these additions and uses contextualized-percentile framing instead.

### Items to NOT include in comms

- "The model thinks…" interpretive prose. The model produces numbers; the user produces interpretation.
- Anything that requires the agent to make market-direction judgments.
- Daily summaries longer than 5 lines (defeats the glance-ability).

---

## Part 5 — Operational gotchas surfaced

These are not blocking but worth fixing opportunistically:

1. **ETF premium endpoint stale at 2026-01-06.** Re-pull whenever `pull_all_raw_data.py` next runs.
2. **`fix_parsers.py` is a separate manual step** after `pull_all_raw_data.py`. Easy fold-in, ~30 lines. Currently if forgotten the model silently uses bad data.
3. **No data-freshness monitoring.** If a Coinglass endpoint silently stops returning data, forward-fill keeps the model running on stale values.
4. **`HOLDOUT_START` rolls forward each pull** (`last_btc_date − 365`). Backtest numbers drift slightly between pulls.
5. **The two-tier strong_prior in `test_strong_prior.py` was never wired into production code.** The deployed `auc_excess_weights` in `common.py` uses single-tier `no_flip` only. Either wire it in or update v4 docs.
6. **Velo silently revises historical data.** No archive of what weights were live on day X. No fix proposed.

---

## Conclusion

Three experiments tested in session 7. Two failed productively (sub-signal walk-forward, calibration). One was a clear decision (canonical selection: wf365). One was deliverable design (comms layer).

Net outcome: the model is unchanged but better understood. The architecture wf365 currently has is correct — adaptation at the ensemble layer, not the sub-signal layer; ordinal output (percentile, position) not cardinal (probability, magnitude).

If session 8's calibration retry succeeds, the model gains cardinal risk outputs. If it fails, the model ships with contextualized percentile framing. Either way, session 8 is the operational deployment session: comms layer + agent runtime + paper-trading shadow run.

After session 8, the model is deployment-ready for paper trading. Real-capital deployment should follow at least 4 weeks of paper-trading observation.

## What this session did NOT change

- No model code changes
- No data changes
- No weight changes
- All canonicals preserved as-is

The output of session 7 is this report, the failed test artifacts (`subsig_wf_test/`, `calibration_out/`), and the comms prototype (`make_daily_chart.py`, `today.png`).
