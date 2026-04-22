# Session synthesis — findings, failed directions, and a recommendation

**Date:** 2026-04-18
**Scope:** Summary of the v14 session and all follow-up threads, with a recommendation for what to do next.

## What's been tried this session

Chronologically:

1. **Monitor MVP (`health_check.py`) shipped.** Flags sub-signals with hold-out AUC ≤ 0.50 or IS-OOS delta > 0.15. Read-only. Validated on v13 canonical: correctly flags `etf_flows` (0.479), `sub_fed_funds_stress_rank` (0.072), does not flag known-working cases.

2. **`test_strong_prior.py` deleted.** Tested a never-adopted feature (`strong_prior` kwarg). Crashed against production. Dead scaffolding.

3. **`fed_funds_stress_rank` unpinned.** Confirmed dead-prior cleanup — no-op in current state (calib AUC 0.5054 is just above 0.5, auto-flip branch doesn't trigger). Shipped as prospective safety.

4. **MIN_CALIB tightening (2018→2021) rejected.** Experiment showed hold-out Sharpe 1.19 → 0.95, today's position 1.00 → 0.43. Tightening moves fed_funds from 3% to 17.3% weight; that signal's hold-out AUC is 0.07. The 2018-onward calibration window was providing accidental insurance.

5. **Monitor backtested as a decision system (24-month window).** Naive rule (MVP default) confirmed harmful. Persist-2 rule appeared to win (Sharpe 1.05 → 1.36). Persist-3 close behind. Flagged methodology concerns.

6. **Extended to 40 months + proper OOS rule selection.** Train on 2023-01→2024-08, test on 2024-09→2026-04. Result: no rule beats baseline on train. Forced to pick best-remediated train winner (naive) — it loses test Sharpe by −0.60. The 24-month persist-2 apparent edge was a 2025-2026 phenomenon not visible on the full window.

7. **Taper-amplification investigation.** Predicted persist-2's 2025-2026 edge would collapse at wider taper because amplification was doing the work. **Prediction wrong.** At wider taper, both wins and losses shrink, but losses shrink more; persist-2 edge is partly amplification but not entirely.

## The taper-amplification result in detail

Per-year Sharpe Δ (persist-2 minus baseline) at different taper widths:

| taper | 2023 | 2024 | 2025 | 2026 | cumulative |
|---|---:|---:|---:|---:|---:|
| production (0.55, 0.70) | −0.05 | −0.81 | +0.55 | +1.63 | −16pp |
| wide (0.45, 0.80) | −0.01 | −0.61 | +0.38 | +1.20 | +8pp |
| very wide (0.40, 0.85) | −0.02 | −0.55 | +0.40 | +0.87 | +15pp |
| full linear (0, 1) | −0.20 | −0.55 | +0.22 | +0.65 | −37pp |

Whipsaw intensity (daily `|Δposition|`, 2025):

| taper | baseline | persist-2 | ratio |
|---|---:|---:|---:|
| production | 0.088 | 0.263 | 3.0× |
| wide | 0.098 | 0.243 | 2.5× |
| full linear | 0.126 | 0.139 | 1.1× |

Persist-2 whipsaws 3× more than baseline at production taper. The signal-quality component of the edge (visible at wide taper where amplification is dampened) is real but modest. The 2023-2024 underperformance pattern survives every taper, so the rule isn't purely lucky — but much of its *apparent magnitude* at production taper is amplification.

## What this means structurally

The current pipeline has two things worth flagging as findings independent of the monitor question:

**(a) Rolling-percentile amplification (open item #9) is more consequential than previously carried.** The tight 0.55/0.70 taper interacts with small composite-score shifts to produce whipsaw. This is actively amplifying baseline's 2024 outperformance *and* persist-2's 2025-2026 apparent outperformance. A wider taper (0.45, 0.80) preserves baseline's 40-month Sharpe at 1.62 vs 1.63 — essentially no cost — while reducing whipsaw-driven variance. Worth investigating as its own change.

**(b) Sub-signal weights are fixed at calibration, and the v7 walk-forward attempt failed.** Confirmed today by a quick sketch: walk-forward refitting for `macro_equities` gives meaningfully different composites (mean |Δ| 0.16) and actually slightly *worse* hold-out AUC (0.622 vs 0.631). Per the HANDOVER memory, the v7 attempt at walk-forward sub-signals caused cascading instability. So this isn't free money.

## Where we ended up

Today (2026-04-17): regime=bear, baseline ensemble 0.461, percentile 0.529, position 1.00. All three tracked rules (naive, persist-2, persist-3) converge with baseline on position today; divergence is purely historical. Note a minor finding from the dual-taper shadow output: under wide taper (0.45, 0.80), baseline's position is 0.77, not 1.00 — the production taper's fully-long plateau is masking a percentile that sits moderately above the "definitively bullish" zone.

Shadow tracker `shadow_tracker.py` updated to include persist-3 and to output ensemble + percentile + position-at-both-tapers for all four rules. Rerun monthly to accumulate forward evidence.

---

# Recommendation

## Near-term (this session / next session)

1. **Ship the monitor as reporting-only. Don't automate any rule.** The OOS rule-selection test was decisive: no rule beats baseline on train. The shadow tracker accumulates forward evidence; revisit in 2+ quarters.

2. **Investigate a wider production taper.** Specifically (0.45, 0.80). Not the monitor question — a standalone structural test. Evidence on hand:
   - 40-month Sharpe: 1.63 (prod) vs 1.62 (wide) — basically unchanged
   - Whipsaw: drops by ~10% for baseline, ~7% for persist-2
   - Behavior on known bad days (Nov 2024): loss cushioned
   - Doesn't introduce new parameters — just moves two that already exist

This is inside the project's "3+ new parameters, reject it" discipline (zero new parameters), testable in one session, and addresses a long-standing concern.

## Medium-term (the real question)

The user flagged "static sub-signal weights" as a known issue. I agree it's the highest-impact structural gap. Three approaches, in order of increasing ambition:

### Option A: Annual sub-signal refit (low-risk, incremental)

Refit sub-signal weights inside each hypothesis once per year (or once per 2 years), on an expanding window. Not walk-forward at monthly cadence — that was the v7 failure mode. Just a scheduled refresh that's less stale than "frozen at MIN_CALIB."

Rationale: fixes the worst case (fed_funds_stress_rank with calib AUC 0.505 / hold-out 0.07 and therefore non-adapting weight 3%) without introducing the monthly-refit instability that killed v7. Matches the discipline already applied at the ensemble layer (monthly walk-forward) but at a slower cadence that reflects sub-signal data being less responsive.

Cost: one new parameter (refit cadence) per hypothesis. Risk: annual refits right after major regime shifts could be unlucky; mitigate by making the cadence date-deterministic rather than data-driven. Implementable in 1 session, including a comparison backtest.

### Option B: Shrinkage / partial pooling of sub-signal weights (principled)

Replace `max(AUC − 0.5, 0.01)` normalization with a Bayesian-style shrinkage: each sub-signal's weight is shrunk toward the uniform prior `1/N` based on a confidence parameter tied to the number of calibration observations. Equivalently, excess becomes `max(AUC − 0.5, 0) × (n_obs / (n_obs + k))` for some shrinkage constant k. This would reduce the "unknown-signal-with-tiny-calibration-AUC-gets-floor-weight" artifacts and would make the weights converge smoothly as more data arrives.

Rationale: the current formula is ad hoc. The 0.01 floor and the `max(· − 0.5)` are load-bearing in ways no one intended (the fed_funds insurance story). Shrinkage replaces these with a single principled knob that has a well-understood interpretation.

Cost: one parameter (shrinkage constant). Risk: modest — this is a conservative generalization of the current formula; at high n_obs it reduces to existing behavior. Implementable in 1-2 sessions.

### Option C: Replace AUC-excess with a calibrated probabilistic model (ambitious)

Instead of AUC-excess weights → composite → percentile → position, fit a logistic regression (or gradient boosted trees with strong regularization) directly on the sub-signal ranks to predict `y_60`. Output is a calibrated probability of drawdown-in-60-days. Position becomes `1 − P(drawdown)` or some monotone mapping thereof. Walk-forward refit at monthly cadence as the ensemble already does.

Rationale: the current pipeline is a stack of heuristics (AUC-excess → NaN-renorm → rolling percentile → linear hybrid taper). Each step was principled at the time but the stack has load-bearing accidental interactions (fed_funds insurance, taper whipsaw). A calibrated model is one learnable object with interpretable output and a clear principle: minimize drawdown-prediction error subject to regularization that controls overfitting.

Cost: this is a real project. 3-5 sessions including careful walk-forward validation. Risk: I expect it to be **competitive but not dramatically better** than the current pipeline on this data (~1,400 calibration days, handful of hypotheses) — the current heuristic stack is reasonable and a model has to beat it meaningfully to justify the added complexity. But it would eliminate the accidental-insurance / amplification-noise class of problems structurally.

Worth noting: this would replace both `build_*_equities.py`'s per-hypothesis composite step and `build_robust.py`'s ensemble step. Big surface area of change.

## What I'd actually do if this were my call

Do the wide-taper test this week (low-risk, zero-parameter test of a structural concern). Shadow-track the three rules for 2+ quarters in parallel. In a future session, pursue **Option B (shrinkage)** as the highest-ratio improvement — it addresses the static-weight concern, is a single new knob, and is a conservative generalization that reduces to the current behavior at high data.

**Option A is fine but doesn't fix anything structural** — it just refreshes stale weights more often. The AUC-excess formula's ad-hoc character survives.

**Option C is the right ambition eventually**, but the right precondition is first cleaning up the amplification / floor-convention / calibration-window issues that are doing load-bearing work in the current pipeline. Replacing the whole stack while those are unresolved risks discovering the new stack doesn't reproduce the accidental-insurance behavior we didn't know we were depending on.

The order I'd commit to: wide taper (now) → shrinkage (medium-term) → revisit calibrated model (long-term if shrinkage doesn't close the gap).

## Files produced this session

**Shipped / promoted:**
- `build_macro_equities.py` (modified: unpin fed_funds_stress_rank)
- `health_check.py` (MVP monitor, reporting-only)
- `shadow_tracker.py` (updated: 3 rules, dual tapers, exports ensemble/percentile)
- `shadow_state.csv` (baseline + 3 rules, regenerated on each run)

**Investigation artifacts (not pipeline):**
- `memo_v14_unpin_and_min_calib_test.md` — Part A shipped, Part B rejected
- `backtest_monitor_report.md` — initial 24-month backtest, motivated extended test
- `oos_rule_selection_memo.md` — 40-month OOS test killed the 24-month finding
- `followup_taper_amplification_test.md` — this memo
- `health_check_history.csv`, `health_check_history_extended.csv`, `monitor_backtest.csv`, `min_calib_experiment.csv` — supporting data

## Open items carried forward

Unchanged:
- Promote v13+v14 bundle to `/mnt/project/` (modified `build_macro_equities.py`, all memos, `shadow_tracker.py`, `shadow_state.csv`)
- Cardinal calibration retry (open item #2)
- Operational comms runtime (#3)
- `OPERATIONS.md` (#5)
- Return/DD-side of annual health-check (#6)
- Watch `sub_fed_funds_stress_rank` across 2+ more refits (#8)
- Rolling-percentile amplification fix (#9) — **elevated in importance** by taper investigation

New:
- Wide-taper structural test (proposed above)
- Shrinkage of AUC-excess weights (proposed above)
- Shadow-track 3 decision rules monthly for 2+ quarters; re-run OOS eval after 6+ new decisions
- Walk-forward sub-signal refit is **not** free money — attempted as a sketch today, hold-out AUC slightly worse than static. Consistent with v7 failure. Don't retry without a different architecture.
