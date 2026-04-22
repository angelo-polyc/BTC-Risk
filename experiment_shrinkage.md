# Experiment: shrinkage of AUC-excess weights

**Date:** 2026-04-18
**Status:** EXPERIMENT ONLY — no changes to committed pipeline. Results are interesting but do NOT support promotion; they surface a deeper question about AUC-based weighting.

## TL;DR

Proposed shrinkage rule: sub-signal weights are a mix of AUC-excess weights and uniform weights: `w = α × (1/N) + (1-α) × (normalized AUC-excess)`. At α=0 this reduces to current behavior (minus the 0.01 floor). At α=1 every sub-signal gets equal weight.

**Empirically, Sharpe *increases* monotonically as α increases**, with uniform weights (α=1) producing hold-out Sharpe 1.73 vs production 1.19. Full-window Sharpe 1.50 vs 1.20.

**But composite AUC *decreases* monotonically**: uniform α=1 gives hold-out ensemble AUC 0.862 vs production 0.886. The model's ranking ability is worse, yet its strategy returns are better.

This decoupling is a red flag, not a discovery. It means the gain is not from better prediction — it is from a different mechanism, most likely related to rolling-percentile amplification (see `followup_taper_amplification_test.md`). **Do not promote**. This experiment actually strengthens the case that the current pipeline's heuristic stack has load-bearing accidental interactions that a naive parameter change can exploit without improving the underlying model.

## What I tested

Two shrinkage formulations, in order:

**v1 (`shrinkage_experiment.py`):** `excess = max(AUC−0.5, 0) × n/(n+k)` — a precision-weighted formulation. **Failed by design**: within a hypothesis, all sub-signals have nearly identical n (~2400 for macro, etc.) so the shrinkage factor cancels in normalization. Weights invariant in k. Abandoned.

**v2 (`shrinkage_v2.py`):** `w = α × (1/N) + (1-α) × (excess / sum excess)` — direct interpolation toward uniform. This is the "right" shrinkage formulation for the problem as stated. Results below are from this.

## Headline results (shrinkage v2, production-style fit)

| α | weights behavior | full Sharpe | hold Sharpe | full AUC | hold AUC |
|---|---|---:|---:|---:|---:|
| 0.00 | AUC-excess only (no floor) | 1.179 | 1.097 | 0.743 | 0.880 |
| 0.10 | mostly excess | 1.218 | 1.138 | 0.737 | 0.881 |
| 0.20 | | 1.246 | 1.232 | 0.730 | 0.882 |
| 0.30 | | 1.306 | 1.454 | 0.722 | 0.882 |
| 0.50 | half-half | 1.421 | 1.722 | 0.706 | 0.881 |
| 0.70 | | 1.425 | 1.662 | 0.687 | 0.877 |
| 1.00 | fully uniform | **1.498** | **1.726** | 0.653 | 0.862 |
| production | 0.01 floor | **1.201** | **1.191** | 0.743 | **0.886** |

Sharpe climbs with α up to ~0.5 then flattens. AUC falls with α throughout. The production line confirms the 0.01 floor is doing specific work vs naive α=0 (no floor): it's worth ~0.09 hold-out Sharpe, corroborating earlier finding that the floor is load-bearing in unintended ways.

## OOS check: fit weights on train half, evaluate on test half

| α | train Sharpe | test Sharpe |
|---|---:|---:|
| 0.00 | 1.074 | 1.306 |
| 0.10 | 1.110 | 1.342 |
| 0.20 | 1.165 | 1.338 |
| 0.30 | 1.223 | 1.398 |
| 0.50 | 1.450 | 1.391 |
| 0.70 | 1.601 | 1.246 |
| 1.00 | **1.714** | 1.280 |
| production | 1.096 | 1.321 |

The train winner (α=1.0) gives test Sharpe 1.28, which is **worse than production's 1.32**. Moderate α values (0.20-0.30) have consistent train-test behavior and beat production on both halves slightly. This is the classic pattern of an over-tuned peak.

## Why does Sharpe rise while AUC falls? (The important question)

Composite volatility collapses as α grows:

| α | composite std (hold-out) |
|---|---:|
| 0.00 | 0.0792 |
| 0.30 | 0.0723 |
| 0.50 | 0.0685 |
| 1.00 | **0.0625** |
| production | 0.0773 |

Uniform-ish weights produce smoother composites. A smoother composite means its rolling-365d rank moves more gradually, which means percentile values sit *further* from the 0.55/0.70 taper boundaries for longer stretches. The interaction with the position function rewards this in ways that have nothing to do with predictive quality.

Concrete example — October 2025 (bear regime, big drawdown):

| | production | α=0.5 |
|---|---|---|
| BTC return | −3.95% | −3.95% |
| mean position | 0.10 | 0.06 |
| strategy return | −6.29% | +0.11% |
| edge | — | +6.40pp |

Same sub-signals, same ensemble weights, different sub-signal-weighting scheme → 6.4pp edge in one month. The α=0.5 composite happened to be slightly lower that month, which pushed percentile further into the defensive zone earlier in the crash. This is not a prediction improvement — it's a noise-smoothing benefit combined with rolling-percentile amplification.

## Connection to the taper-amplification story

In the taper investigation (`followup_taper_amplification_test.md`) we showed that persist-2's apparent edge was partly due to whipsaw-amplification interaction with the position function's binary thresholds. Shrinkage is exploiting the *same* class of interaction, in a different direction: smoother composites → fewer near-threshold oscillations → more consistent positioning through drawdowns.

Both experiments point at the same conclusion: **the 0.55/0.70 taper is doing non-trivial work and the pipeline's performance is sensitive to anything that interacts with it**. The proper response is not to rewrite the sub-signal weighting; it is to fix the amplification issue first (i.e., implement the wider taper from `experiment_taper_sweep.md`).

## Why I don't trust these results enough to promote

1. **AUC falls as α rises.** A Bayesian statistician would say this is a failure of shrinkage: the prior is overriding the data rather than regularizing it. The model is making worse predictions and getting rewarded for it.

2. **The regime where it matters most is bear years.** 2022 under α=0.5 shows most of the Sharpe gain. That's the year where the 2018-onward macro calibration (fed_funds insurance) was already providing accidental protection. Stacking more accidental-protection mechanisms doesn't make the model genuinely better.

3. **The train-test split shows the optimum moves.** Train favors α=1.0; test favors α=0.2-0.5. A robust signal should have a stable optimum.

4. **Uniform weights aren't principled for this task.** Some sub-signals in the current model are genuinely informative (real_rate_rank at AUC 0.75) and others aren't (hy_spread_roc_rank at 0.40). Uniformly averaging them isn't more principled than AUC-weighting; it's just a different set of assumptions. The Sharpe gain from averaging is the noise-smoothing effect described above, not a signal-quality discovery.

5. **The exact same gain would appear from any composite-smoothing operation.** I haven't tested this, but I'd expect that literally averaging the *committed* ensemble_score with its 7-day moving average would produce a qualitatively similar Sharpe bump via the same mechanism. If that's true, shrinkage isn't really the story — composite smoothing is.

## Recommendation

**Do not promote.** Investigate what's actually happening.

Two follow-ups if this topic is pursued further:

1. **Confirm the smoothing hypothesis.** Replace the committed `ensemble_score` with an EWMA-smoothed version (span = 5, 10, 20 days). If that produces Sharpe gains comparable to α=0.5, then shrinkage isn't doing anything structural; composite smoothing is, and the right intervention is much simpler.

2. **If smoothing confirms, the right structural fix is still the wider taper.** A wider taper reduces threshold sensitivity directly, at the position-function layer where the interaction actually happens, without distorting sub-signal weights or reducing predictive AUC.

If the shrinkage story were "I reduced the weight variance and got better weights," we'd expect AUC to rise or stay flat. AUC falls monotonically. That's a tell that we're not improving the model, we're bypassing its prediction quality with a different error-cancelling mechanism. The MaxDD / 2022-protection gains are real but they're coming for the wrong reason, and relying on that is how models silently fail when the environment shifts.

## What this experiment is actually useful for

Despite not supporting a weight-rule change, this experiment is valuable:

- Confirms the 0.01 AUC-excess floor is load-bearing (~0.09 Sharpe).
- Provides further evidence (complementary to the taper experiment) that rolling-percentile amplification is the dominant structural issue in the current pipeline.
- Rules out the most natural "shrinkage toward uniform" fix as a free improvement.
- Sharpens the recommendation from the earlier synthesis: fix the amplification (wider taper) first. Don't change sub-signal weighting until that's done — any change will be contaminated by threshold-interaction effects.

## Files

- `shrinkage_v2_results.csv` — alpha sweep, 9 values, full-window + hold-out metrics.
- `shrinkage_results.csv` — v1 (n/(n+k)) formulation, archived for reference.
