# OOS rule-selection test + shadow-tracking setup

**Date:** 2026-04-18
**Scope:** Honest out-of-sample test of whether acting on `health_check.py` flags improves the model. Extends the 24-month backtest to 40 months (2023-01 to 2026-04) and runs proper rule selection: pick the best rule on the first 20 months, evaluate only the winner on the last 20.

## TL;DR

**No decision rule beats baseline out-of-sample.** On the train half (2023-01 → 2024-08), `naive` is the best remediated rule at Sharpe 1.65, but baseline is 1.84 — no rule wins on the train half. Naively picking the best one anyway, the "train winner" (`naive`) gets Sharpe 0.78 on the test half vs baseline 1.38 — the winning rule loses by 0.60 Sharpe OOS. The apparent persist-2 edge from the earlier 24-month backtest was a 2025-2026 phenomenon, not a persistent pattern.

**Action:** Keep `health_check.py` reporting-only. Do not automate any rule. Shadow persist-2 and naive in parallel going forward, using the state file produced here, and re-evaluate after 2+ quarters of out-of-sample evidence.

## Extended history

Extended `health_check_history.csv` backward to 2023-01-01. First feasible decision date constrained by: (a) all five hypotheses must have enough data for a 365-day trailing AUC (ETH and crypto_deriv come fully online Jan 2022; 2023-01 decision date has 12 months of lookback — marginal but workable); (b) etf_flows sub-signals don't exist pre-2024 and return NaN AUC until Feb 2025 — handled gracefully by compute_auc's 50-row minimum.

Result: 40 monthly decisions, 1,680 rows (1,440 at sub-signal level). Mean 15.9 sub-signals flagged per month, consistent with the earlier 24-month range.

Saved to `health_check_history_extended.csv`.

## OOS rule-selection test

**Procedure:**
1. Split 40 months into train (first 20, 2023-01 → 2024-08) and test (last 20, 2024-09 → 2026-04).
2. On train, rank rules by Sharpe.
3. Declare the best train rule as the "selected" rule.
4. Evaluate ONLY the selected rule on test. Report test-set performance vs baseline.

**Rules tested** (all within 3-parameter budget):
- `naive` — drop if OOS AUC ≤ 0.50 OR IS-OOS delta > 0.15 (MVP current)
- `oos_only` — drop if OOS AUC ≤ 0.50 (no delta rule)
- `oos_persist2` — drop if OOS AUC ≤ 0.50 for 2 consecutive months
- `oos_persist3` — 3 consecutive months
- `oos_persist4` — 4 consecutive months

### Train window (rule selection): 2023-01-01 → 2024-08-31

| rule | Sharpe | total return |
|---|---:|---:|
| **baseline (no action)** | **1.837** | **+215.8%** |
| naive | 1.653 | +202.5% |
| oos_only | 1.492 | +162.8% |
| oos_persist2 | 1.555 | +169.8% |
| oos_persist3 | 1.490 | +152.6% |
| oos_persist4 | 1.421 | +138.3% |

**No remediated rule beats baseline on train.** Baseline wins. But the OOS protocol requires picking the best *remediated* rule anyway — that's `naive` at Sharpe 1.65.

### Test window (honest evaluation): 2024-09-01 → 2026-04-17

Applying ONLY the train-winner (`naive`) to test:

| metric | baseline | naive (train-selected) | Δ |
|---|---:|---:|---:|
| Sharpe | 1.381 | **0.777** | **−0.604** |
| Total return | +92.6% | +39.2% | −53.4pp |
| MaxDD | −25.8% | −29.6% | −3.7pp |

**The train-selected rule loses 0.60 Sharpe and 53pp of total return on the honest out-of-sample window.**

### Reference — all rules on test (not OOS, for context only)

| rule | test Sharpe | test total | test MaxDD |
|---|---:|---:|---:|
| baseline | 1.381 | +92.6% | −25.8% |
| naive | 0.777 | +39.2% | −29.6% |
| oos_only | 1.200 | +78.2% | −32.5% |
| **oos_persist2** | **1.736** | **+119.5%** | **−20.4%** |
| oos_persist3 | 1.656 | +113.1% | −22.3% |
| oos_persist4 | 1.407 | +88.3% | −29.4% |

The persist-2 rule still looks best on the test window. But notice: on the train window persist-2 had Sharpe 1.56 (below baseline's 1.84). **You could not have picked persist-2 a priori** — it looked mediocre when you had the evidence to choose. Its post-hoc test performance is irrelevant to the question "can we act on this rule going forward?"

## Year-by-year decomposition

| year | baseline | naive | oos_only | persist2 | persist3 | persist4 |
|---|---:|---:|---:|---:|---:|---:|
| 2023 Sharpe | 2.409 | 2.295 | 2.270 | 2.356 | 2.356 | 2.404 |
| 2024 Sharpe | 1.807 | 1.064 | 1.070 | 0.996 | 0.896 | 0.770 |
| 2025 Sharpe | 0.602 | 0.129 | 0.475 | 1.148 | 1.044 | 0.633 |
| 2026 Sharpe | 1.059 | 1.658 | 1.503 | 2.686 | 2.482 | 2.342 |

**Rules beating baseline by year:** 2023 none; 2024 none; 2025 persist2/3/4; 2026 all of them. Two years of "no rule helps" followed by two years of "rules help" is not a pattern you can trust — it's exactly what you'd see if the signals the monitor is dropping happened to be the ones whose failure was obvious ex post.

Compounded consequence: the 2024 underperformance (baseline +95.5%, persist2 +40.1%) is so large that even with persist2's strong 2025 and 2026, it needs several more years of clear wins to overtake baseline on a true long-run Sharpe comparison.

## Interpretation

The extended backtest refutes the earlier 24-month finding. What the 24-month window caught:
- A regime in which baseline was unusually weak (2025) and the monitor-acting rules recovered what baseline lost.
- A subsequent recovery window (2026) in which everything beat baseline, including naive.

What it missed:
- The full 2023-2024 period, in which baseline dominates every rule.

The claim "persist-2 improves the model" is not supported by honest OOS testing on the data we have.

That doesn't mean the monitor is useless. What it means:
- As a **diagnostic** tool (flag sub-signals for human attention), the monitor's current behavior is justified.
- As a **decision system** (auto-drop flagged sub-signals), no rule in the set {naive, oos_only, persist-2, persist-3, persist-4} is justified on existing data.

## Recommendations

1. **Keep `health_check.py` reporting-only.** Do not ship any auto-mutation logic. Reaffirmed.

2. **Shadow-track two rules going forward.** Logging the counterfactual positions from `naive` and `oos_persist2` alongside production. After 2+ quarters of forward data (6+ more monthly decisions), re-run this same OOS protocol: train on 2023-01 → 2024-12, test on 2025-01 → 2026-10 (or whenever). Check if persist-2's edge survives. Shadow file saved: `shadow_state.csv`.

3. **Do not read per-year tables as a reason to "wait for persist-2 to vindicate itself."** The correct way to evaluate the rule is in a held-out window where you committed to it before seeing the outcome. Cherry-picking a rule based on recent years' tables is exactly the overfitting failure mode we're trying to avoid.

4. **Revisit the threshold itself (0.15 delta, 0.50 AUC floor).** These were chosen a priori but never validated. Some of the monitor's false-alarming could come from the thresholds being too aggressive for this noise regime, not from the fundamental design. A separate study, not this memo's scope.

## What's happening today (2026-04-17)

Position calls from each rule on today's data:

| rule | position | notes |
|---|---:|---|
| baseline (shipped) | 1.00 | regime bear, percentile 0.529 |
| naive | 1.00 | identical |
| oos_persist2 | 1.00 | identical |

All five tested rules give position 1.00 today. The decisions diverge historically; today they converge.

## Files produced

- `health_check_history_extended.csv` — 1,680 rows over 40 months.
- `shadow_state.csv` — day-by-day (ensemble, percentile, position) for baseline and naive rule. Extend this going forward for shadow tracking of naive and persist-2.
- `oos_rule_selection_memo.md` — this document.

## Standing principle reaffirmed

Session's working norm was "if a fix requires 3+ new parameters, reject it" and "results that look good but have methodological caveats are flagged and not promoted." The earlier 24-month window looked good. This extended test shows exactly why methodological rigor matters: the OOS protocol turns a Sharpe +0.31 claim into a Sharpe −0.60 failure. Ship the MVP as reporting; don't promote.
