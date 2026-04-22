# Refit Report v6 — Transaction Costs & Stop Recommendation

**Session:** 6
**Date:** 2026-04-13
**Status:** Transaction cost impact measured at 5bps roundtrip — Sharpe drops from 1.00 → 0.96, much smaller than v4 playbook predicted. Three candidate improvements evaluated against a meaningful-improvement bar (>+1pp ensemble OOS AUC OR >+0.10 Sharpe). All three failed. **Recommendation: stop iterating on model design and shift focus to operational deployment.**

---

## Part 1 — Transaction cost analysis

### What was tested

The v4 execution playbook §7.4 estimated Sharpe would drop from 1.00 → ~0.85 at 5bps roundtrip cost (2.5bps per side), based on a back-of-envelope calculation of average daily position turnover × cost. This session ran the actual measurement.

### Method

```python
# For each day:
#   cost_t = |position_t - position_{t-1}| * cost_per_side
#   strategy_return_adj_t = position_{t-1} * btc_return_t - cost_t
COST_PER_SIDE = 0.00025  # 2.5bps per side = 5bps roundtrip
```

Applied to wf365 daily position series across full window (2021-06-30 → 2026-04-14).

### Results

| Variant | Pre-cost Sharpe | Post-cost Sharpe | Pre-cost total | Post-cost total | Δ Sharpe |
|---|---:|---:|---:|---:|---:|
| wf365 | 1.026 | 1.022 | +369% | +364% | −0.004 |
| sf730 | 1.179 | 1.175 | +525% | +519% | −0.004 |
| B&H | 0.550 | 0.550 | +106% | +106% | 0 |

Sharpe drop of 0.004, not 0.15 as the v4 estimate predicted. **The estimate was off by ~37×.**

### Why the v4 estimate was wrong

The v4 calculation assumed average daily position change × number of days × cost per side. Three errors compounded:

1. **Position barely moves day-to-day.** The position function is `1 − f(percentile)` and percentile changes by typically <0.02 per day (rolling-365 has high inertia). Most days have |Δposition| < 0.01.
2. **Most active position changes are small.** When position does move, the typical move is 0.05-0.10, not the 0.5+ implicit in the v4 estimate.
3. **Days with no position change have zero cost.** ~40% of days have position unchanged from the day before (especially during sustained bull/bear regimes when percentile hovers in a narrow band).

Median daily turnover is 0.018. Mean is 0.041. Total turnover over 1750 days ≈ 72.0 (i.e., the equivalent of 36 full round trips in 5 years). At 5bps roundtrip, that's 36 × 5bps = 180bps total cost over 5 years, or ~36bps/year. Annualized return drops by ~36bps. With volatility ~36% annualized, Sharpe drops by 0.01. Matches the measurement.

### Implication

Cost is not a constraint on the strategy. The model can be deployed at this position function without modification. There is no need to add a turnover-suppression tier (the v4 playbook §7.4 considered one), because there's nothing to suppress.

---

## Part 2 — Meaningful improvement bar

### Motivation

After 6 sessions, the model is producing useful results. Continuing to iterate has diminishing returns and risk of over-fitting to the validation set. Need an explicit bar that separates "real improvement" from "noise that happens to look favorable on the current hold-out."

### The bar

A candidate improvement is **meaningful** if it clears EITHER:

- Ensemble hold-out AUC improvement of **>+1.0pp** vs current canonical wf365 (current = 0.7733), OR
- Strategy hold-out Sharpe improvement of **>+0.10** vs current canonical (current = 0.461 post-cost)

OR, regardless of aggregate metrics:

- Materially improves crisis-period behavior on at least one of the 4 historical events (max percentile +5pp toward defensive, or strategy alpha vs B&H +5pp better)

Any candidate that fails all of these is rejected. No "0.3pp AUC improvement" excuses.

### Justification

- Both AUC and Sharpe have measurement noise on a 365-day hold-out. Empirical bootstrap intervals: AUC ±0.02, Sharpe ±0.10. Anything inside that range is noise.
- Crisis-period gains are valued as a tiebreaker because they protect the use case (risk overlay) directly.
- The bar is intentionally aggressive. Marginal improvements aren't worth the integration / deprecation risk of changing a working production model.

## Part 3 — Three candidate improvements evaluated

### Candidate A — ETH calibration window extension

**Hypothesis:** ETH MIN_CALIB is currently 2021-06-30 (matching the rest). Extending back to 2018-04 (when sufficient ETH derivatives data exists) gives ~3 more years of calibration data. Should improve ETH composite OOS AUC.

**Test:** rebuilt ETH hypothesis with MIN_CALIB = 2018-04-01. Re-ran wf365 ensemble.

**Result:**
- ETH OOS AUC: 0.6256 → 0.6201 (−0.6pp, slightly worse)
- Ensemble OOS AUC: 0.7733 → 0.7741 (+0.08pp, noise)
- Strategy hold-out Sharpe: 0.461 → 0.448 (−0.013, slightly worse)
- Crisis behavior: no change

**Verdict: REJECT.** Pre-2021 ETH derivatives data is structurally different (lower OI, different exchange mix) and dilutes signal more than it adds. Extension didn't clear the bar in any direction.

### Candidate B — HY spread additional sub-signal in Macro

**Hypothesis:** Macro has 8 sub-signals. Adding `BAMLH0A0HYM2` 90-day rate-of-change as a 9th could capture credit market stress more granularly.

**Test:** built `hy_spread_z90_rank` as a 9th sub-signal, refit Macro with 9 signals.

**Result:**
- Macro OOS AUC: 0.6202 → 0.6244 (+0.4pp, noise)
- New signal weight: 0.067 (modest; doesn't dominate)
- Ensemble OOS AUC: 0.7733 → 0.7758 (+0.25pp, noise)
- Strategy hold-out Sharpe: 0.461 → 0.471 (+0.010, noise)
- Crisis behavior: no change on COVID/LUNA/FTX. Slightly less defensive on 2018 (max percentile 0.92 → 0.89).

**Verdict: REJECT.** Improvement is within noise on both metrics. The 2018 worsening, while small, is a strict negative.

### Candidate C — DXY momentum sub-signal in Macro

**Hypothesis:** DXY (USD index) is in Macro via `fx_stress_rank` already, but only as cross-sectional FX stress. A momentum component (DXY 90d Z-score) could capture USD strength as a separate dynamic.

**Test:** built `dxy_z90_rank` as a 9th sub-signal in Macro. Refit.

**Result:**
- Macro OOS AUC: 0.6202 → 0.6189 (−0.13pp, noise)
- New signal weight: 0.041 (low; doesn't dominate)
- Ensemble OOS AUC: 0.7733 → 0.7720 (−0.13pp, noise)
- Strategy hold-out Sharpe: 0.461 → 0.452 (−0.009, noise)
- Crisis behavior: no change

**Verdict: REJECT.** Negligible across the board.

## Summary table

| Candidate | OOS AUC Δ | Sharpe Δ | Crisis Δ | Verdict |
|---|---:|---:|---|---|
| ETH MIN_CALIB extension | +0.08pp | −0.01 | none | REJECT |
| Macro HY spread Z90 | +0.25pp | +0.01 | slight regression on 2018 | REJECT |
| Macro DXY Z90 | −0.13pp | −0.01 | none | REJECT |
| Bar | >+1.0pp | >+0.10 | meaningful improvement | — |

## Conclusion: stop iterating, shift focus to operational deployment

The model has hit diminishing returns on the candidates accessible from this session's horizon. Three reasonable candidates evaluated, none cleared the bar. Continuing to test more variants invites overfitting on the hold-out year (effectively a validation set after 6 sessions of testing).

**The remaining work that would move the model forward is at the architectural level, not the marginal sub-signal level:**

- Adding entirely new hypothesis classes (on-chain data, options-implied probabilities, stablecoin flows) — these are 1-2 weeks each and require new data sources
- Cardinal calibration of probability + magnitude — see session 7 work
- Operational reliability (paper trading verification, monitoring infrastructure) — see session 7+ comms layer

None of these are session-of-fitting work. They are integration, ops, or new-data work.

**Recommendation for session 7+:** stop running model variants. Treat wf365 as the canonical and focus on (a) cardinal risk outputs to support the discretionary + risk-overlay use case, (b) operational deployment (comms layer + monitoring), (c) paper-trading shadow run before any real capital.

## What this session did NOT change

- No model code changes
- No data changes
- All canonicals preserved as-is
- The 5bps cost adjustment is applied **only at reporting time**; the model itself produces uncosted positions, downstream callers apply costs

## Tasks carried into session 7

Listed in former `NEXT_SESSION_PLAN.md` (since superseded by the v7 version):
1. Canonical selection: pick one of sf730 / wf365 as the deployed model, document why
2. Cardinal risk outputs (probability + magnitude) — explore as bolt-ons to ensemble_score
3. Sub-signal walk-forward test — the architectural gap from session 4's "Phase 2"
4. Comms layer design for operational deployment
