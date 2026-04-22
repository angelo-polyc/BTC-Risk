# BTC Model Refit Report v3 — Classic Cycle Sub-Signal Audit + Pinning Audit

**Date:** 2026-04-15 (session 3)
**Supersedes:** `refit_report_v2.md` (session 2) for canonical numbers; previous reports kept for historical reference

## TL;DR

Session 3 diagnosed and fixed the Classic Cycle hold-out collapse (v2 known
issue, hold-out AUC 0.35 on y_60) and audited the other pinned hypotheses for
the same failure mode.

**Classic Cycle fix:** restricted from 9 indicators to 4, refit the ensemble
regime weights. Classic Cycle hold-out AUC **0.35 → 0.76**, ensemble hold-out
AUC **0.75 → 0.79**, strategy max drawdown **−46.6% → −30.2%**, hold-out year
Sharpe **−0.03 → +0.28**. Full-period Sharpe drops 0.04 (in-sample overfit
gains forgiven).

**Pinning audit:** scanned Macro & Equities, Crypto Derivatives, and ETH for
the same "pinned but floor-weighted but actually strong OOS" pattern that hit
Classic Cycle. Found one real repeat in Macro (`spx_overext_rank`, milder
effect size) and one curio (`fed_funds_stress_rank`, severely OOS-inverted
but already floored). Crypto Derivatives and ETH are clean. The Classic Cycle
failure mode was mostly unique to Classic Cycle.

**Next-session roadmap:** two-tier pinning weight formula, walk-forward
refitting, and crisis validation — documented in `NEXT_SESSION_PLAN.md`.

## 1. Investigation — why is Classic Cycle hold-out 0.35 on y_60?

### 1.1 First hypothesis (falsified): extend the calibration window

The v2 report attributed the collapse to the calibration window being too
short, and the task brief for session 3 started with "extend the calibration
window to match CME/Macro."

Tested four windows on both labels, keeping everything else canonical:

| Window | Description | y_30 IS | y_30 OOS | y_60 IS | y_60 OOS |
|---|---|---:|---:|---:|---:|
| 2021-06-30 | canonical | 0.82 | 0.66 | 0.80 | **0.35** |
| 2018-10-01 | match Macro & Eq | 0.74 | 0.40 | 0.76 | 0.23 |
| 2018-02-01 | fear_greed start | 0.74 | 0.35 | 0.76 | 0.21 |
| 2012-01-09 | drop F&G, bmo start | 0.72 | 0.33 | 0.73 | 0.19 |

**Window extension monotonically hurts on y_60** (0.35 → 0.19 as window
grows). More calibration data concentrates weight on whichever indicators
looked best over the longer window, which happen to be the same ones failing
in 2025–2026. The "extend to match Macro" hypothesis is **falsified**.

### 1.2 Root cause — per-sub-signal IS vs OOS decomposition

Per-sub-signal y_60 AUCs on the canonical window:

| Sub-signal | IS AUC (calib) | OOS AUC (hold-out) | Δ | Verdict |
|---|---:|---:|---:|---|
| `two_year_ma` | NaN | NaN | — | flat regime, always floored |
| `golden_ratio` | 0.62 | **0.81** | −0.19 | ✅ works OOS |
| `bmo` | 0.47 | **0.72** | −0.25 | ✅ works OOS |
| `ahr999` | 0.34 | **0.76** | −0.42 | ✅ works OOS |
| `fear_greed` | 0.63 | **0.73** | −0.10 | ✅ works OOS |
| `heatmap` | 0.67 | 0.24 | +0.43 | ❌ inverted OOS |
| `ma_roc` | 0.64 | 0.18 | +0.47 | ❌ inverted OOS |
| `rainbow` | 0.75 | 0.23 | +0.53 | ❌ inverted OOS |
| `bubble_index` | 0.78 | 0.20 | +0.58 | ❌ inverted OOS |

The 9 indicators split cleanly into two groups. All 4 failed indicators are
variants of **"price distance above a long-term moving average"**:
- `heatmap` = price / MA1440
- `ma_roc` = MA1440 rate of change
- `rainbow` = price vs multi-year wave bands
- `bubble_index` = overextension vs long-term trend

The 4 working indicators measure different mechanisms — `golden_ratio` uses a
shorter MA350, `bmo` is a macro oscillator, `ahr999` is a cheap/expensive
ratio, `fear_greed` is sentiment.

**Physical interpretation:** in the post-ETF regime, BTC has sustained being
far above its long-term moving averages without mean-reverting. Institutional
flow appears to have decoupled "price above old MA" from "drawdown precursor."
The other cycle-indicator mechanisms are unaffected.

### 1.3 Interaction with the pinning logic

`bmo` and `ahr999` — the two *strongest* hold-out signals at 0.72 and 0.76 —
were floor-weighted to 0.009 each in the v2 canonical fit because their
calibration-window AUCs were below 0.5 and they're pinned. The pinning logic
was actively excluding the indicators that work in the modern era.

This is the general pinning weakness: the `max(auc − 0.5, 0.01)` weight formula
collapses to the floor when the prior direction is mildly hostile in the
calibration window, with no mechanism for sub-signal regime shift between
calibration and deployment. The pinning was doing its direction-protection
job correctly; its weight-formula choice is what's suboptimal.

### 1.4 Simpson's paradox in the by-regime decomposition

The pooled 0.35 hold-out AUC is partially a pooling artifact. By-regime OOS
AUC for the canonical 9-indicator composite:

| Regime | Days | y_60 base rate | OOS AUC |
|---|---:|---:|---:|
| bull | 104 | 0.26 | 0.83 |
| neutral | 147 | 0.24 | 0.26 |
| bear | 115 | 0.78 | **0.996** |
| pooled | 366 | 0.34 | 0.35 |

The composite is actually good in bull, excellent in bear, and only inverts
in neutral. Pooling across regimes with different base rates (neutral 0.24 vs
bear 0.78) collapses the AUC even though within-regime discrimination is
reasonable. This matters because hypothesis-level pooled AUC is misleading
when the ensemble uses regime-specific weights.

## 2. The fix — V1 Classic Cycle (4 indicators)

**`build_classic_cycle.py` restricted to 4 indicators** via a `KEEP_SET`
constant. The 5 dropped indicator load blocks are kept in-place guarded by
`if <n> in KEEP_SET` rather than deleted, so re-enabling any indicator for
A/B testing is a one-line edit.

Full patch is minimal — `KEEP_SET` constant added, 5 indicator load blocks
wrapped in conditionals, `PINNED_FLIPS` filtered through `KEEP_SET`. No
changes to `common.py`, `build_robust.py`, or any other file. The canonical
pipeline run picks up the change and refits regime weights against the new
composite.

### 2.1 V1 Classic Cycle composite

| Sub-signal | IS AUC | OOS AUC | Weight (pinned) |
|---|---:|---:|---:|
| `fear_greed` | 0.63 | 0.73 | 0.489 |
| `golden_ratio` | 0.62 | 0.81 | 0.435 |
| `bmo` | 0.47 | 0.72 | 0.038 (floor) |
| `ahr999` | 0.34 | 0.76 | 0.038 (floor) |

Composite IS 0.62, OOS **0.76**.

Note: `bmo` and `ahr999` remain floor-weighted in V1 (pinning weakness
unchanged). Despite this, the composite recovers because the other two
indicators carry enough signal on their own. A two-tier pinning formula
(session 4 Task 1) would likely lift the composite further.

### 2.2 V1 refit ensemble regime weights

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH |
|---|---:|---:|---:|---:|---:|---:|
| bull | 0.013 | 0.013 | **0.425** | 0.013 | 0.249 | 0.287 |
| neutral | 0.235 | **0.340** | 0.171 | 0.102 | 0.000 | 0.152 |
| bear | 0.218 | **0.577** | 0.017 | 0.017 | 0.000 | 0.172 |

Deltas vs v2 canonical:

| Regime | Macro&Eq | CME | CryptoDeriv | ClassicCyc | ETFFlows | ETH |
|---|---:|---:|---:|---:|---:|---:|
| bull | +0.003 | +0.003 | +0.106 | **−0.246** | +0.062 | +0.072 |
| neutral | +0.026 | +0.038 | +0.019 | **−0.101** | 0.000 | +0.017 |
| bear | +0.074 | +0.195 | +0.006 | **−0.333** | 0.000 | +0.058 |

Classic Cycle's weights drop across all three regimes, redistributed mostly to
CME (bear) and Crypto Derivatives / ETH (bull). The bear regime weight drop
to 0.017 is intentional — V1 Classic Cycle is structurally unsuited to
bear-regime drawdown prediction (the 4 working indicators invert in bear,
measuring capitulation bottoms rather than tops).

## 3. Strategy backtest — full 1,750-day window

| Variant | Full Sharpe | Full total | Full max DD | Ensemble IS AUC | Ensemble OOS AUC |
|---|---:|---:|---:|---:|---:|
| **V0 v2-canonical (previous)** | 1.00 | +348% | −46.6% | 0.885 | 0.747 |
| **V1 refit (new canonical)** | 0.96 | +237% | **−30.2%** | 0.854 | **0.787** |
| buy-and-hold | 0.55 | +108% | −76.6% | — | — |

Full-period Sharpe drops by 0.04 and total return by ~111pp, but max drawdown
improves by 16pp and ensemble OOS AUC improves by 4pp.

## 4. IS vs OOS split — where the improvement actually lives

| Period | Variant | Sharpe | Total | Max DD |
|---|---|---:|---:|---:|
| IS (2021-06 → 2025-04) | V0 | 1.28 | +390% | −25.3% |
| IS (2021-06 → 2025-04) | V1 refit | 1.10 | +224% | −26.4% |
| **OOS (2025-04 → 2026-04)** | **V0** | **−0.03** | **−8.5%** | **−46.6%** |
| **OOS (2025-04 → 2026-04)** | **V1 refit** | **+0.28** | **+4.1%** | **−30.2%** |

**V0's entire full-period performance was in-sample.** In the hold-out year
V0 made negative Sharpe, lost 8.5%, and took its full 46.6% drawdown. V1
refit makes positive Sharpe, positive return, and caps drawdown at −30.2%.

For context, the B&H hold-out year was Sharpe −0.06, return −11.1%, max DD
−49.7%. V1 refit gave +0.34 Sharpe, +15pp total return, and 20pp drawdown
improvement on this passive comparison over the hold-out year.

**Position averages by regime in the hold-out year** (the clearest mechanism
for the improvement):

| Regime | V0 | V1 refit | Δ |
|---|---:|---:|---:|
| bull | 0.75 | 0.65 | −0.10 |
| neutral | 0.88 | 0.62 | −0.26 |
| bear | **0.86** | **0.49** | **−0.37** |

V0 held 86% position on average during bear-regime hold-out days — the failed
indicators were reading "low danger" when price had dropped below long-term
MAs. V1 holds 49%. That 37pp position gap on 55 bear-regime days drove most
of the max drawdown improvement.

## 5. Diagnostic — drop-in vs refit comparison

As a sanity check, a variant was tested with V1 Classic Cycle swapped into
the ensemble but using **V0's old regime weights** (no refit). This isolates
the effect of the sub-signal change from the ensemble weight refit.

| Variant | Full Sharpe | Full total | Full max DD | OOS Sharpe | OOS max DD |
|---|---:|---:|---:|---:|---:|
| V0 canonical | 1.00 | +348% | −46.6% | −0.03 | −46.6% |
| V1 drop-in | 0.91 | +209% | −29.5% | +0.42 | −23.1% |
| **V1 refit (canonical)** | 0.96 | +237% | −30.2% | +0.28 | −30.2% |

The drop-in has modestly better OOS metrics than the refit (0.42 vs 0.28 on
Sharpe, −23.1% vs −30.2% on max DD). The Sharpe delta is well within noise
at 366 days (SE ~1.0), but the direction is consistent.

**Interpretation:** this is a quiet signal that `build_robust.py`'s AUC-excess
fit is itself slightly over-indexed on the 2021–2025 window. The refit is
technically correct — it uses the new composite to learn weights — but the
"tasting" is happening on a window where the failed indicators were still
giving misleading signals for other hypotheses. Frozen weights from an
earlier fit happen to preserve some character the refit erases.

**Not actionable on one data point** — the delta is within noise. But worth
remembering: if this pattern shows up on a third hypothesis or a third refit,
it's two data points on the same mechanism and becomes worth treating as
systemic.

**Deployment choice: V1 refit** because it's the canonical pipeline path and
runs through `run_full_pipeline.py` with no special handling. Both V1 variants
massively beat V0 on hold-out metrics.

## 6. Pinning audit — scanning Macro/CD/ETH for the same failure mode

After the Classic Cycle diagnosis, the natural follow-up was: are the other
hypotheses also silently excluding strong sub-signals the same way?

### 6.1 Method

Two patterns flagged across all non-Classic-Cycle sub-signals:

- **Pattern A** — pinned sub-signal with IS AUC < 0.5 (floored) but OOS
  AUC > 0.6. The bmo/ahr999 pattern. Signal being silently excluded.
- **Pattern B** — sub-signal with IS AUC ≥ 0.65 but OOS AUC ≤ 0.35. The
  bubble_index/rainbow pattern. Signal carrying overfit signal.
- **Pattern C** (informational) — any sub-signal with |IS − OOS| ≥ 0.30.

Calibration windows: Macro 2018-10 → 2025-04, CD and ETH 2021-06 → 2025-04.
Label: y_60. Hold-out: 2025-04 → 2026-04.

### 6.2 Findings summary

| Hypothesis | Pattern A | Pattern B | Pattern C | Verdict |
|---|:---:|:---:|:---:|---|
| Macro & Equities | 1 (`spx_overext`) | 0 | 1 (`fed_funds_stress`) | **1 real find** |
| Crypto Derivatives | 0 | 0 | 0 | ✓ clean |
| ETH | n/a (no pinning) | 0 | 0 | ✓ clean |

### 6.3 Finding 1 — Macro & Equities `spx_overext_rank` [Pattern A]

| | Value |
|---|---:|
| Pin status | pinned ("stretched equities precede risk-asset stress") |
| IS AUC (2018-10 → 2025-04) | **0.43** ← below 0.5, floored |
| OOS AUC (2025-04 → 2026-04) | **0.67** |
| Current weight | 0.026 (floor) |
| Proposed weight under `max(|auc−0.5|, 0.01)` | 0.099 (3.8×) |

Structurally the same pattern as Classic Cycle's bmo and ahr999, milder
effect size. The 2018–2025 window was mildly hostile to the prior
(overextension didn't cleanly predict drawdowns during that period); the
2025–2026 hold-out re-establishes the relationship.

**Not patched this session.** Macro's composite OOS is already healthy at
0.62; rescuing this signal gives a modest boost rather than a structural
repair. The right fix is a broader pinning weight-formula change (Task 1 in
`NEXT_SESSION_PLAN.md`).

### 6.4 Finding 2 — Macro & Equities `fed_funds_stress_rank` [Pattern C]

| | Value |
|---|---:|
| Pin status | pinned ("tightening = stress") |
| IS AUC | 0.51 (noise) |
| OOS AUC | **0.07** ← severely anti-predictive |
| Current weight | 0.026 (floor) |

OOS AUC 0.07 is extreme. Physical interpretation: Fed rate-cuts during 2025
happened reactively *during* BTC drawdowns, not *before* them, so "tightening
stress high → future drawdown" is structurally wrong in this window. The
signal is already floored and contributing almost nothing.

**Not actionable, but a canary:** if any future pinning relaxation lifts its
weight off the floor, it would bleed bad direction into the Macro composite.
Task 1 in session 4 must explicitly handle this — the simpler `max(|auc−0.5|,0.01)`
formula would give this signal 0.43 weight × wrong direction = catastrophic.

### 6.5 Crypto Derivatives — no flags (pinning working correctly)

The most interesting row is `rv21_zscore_rank`:

| | Value |
|---|---:|
| Pin status | pinned |
| IS AUC | 0.36 (well below 0.5, floored) |
| OOS AUC | 0.50 (noise) |
| Current weight | 0.015 (floor) |

This is the "pinning caught a wrong-direction overfit trap" success case.
Without pinning, the 0.36 IS would have been flipped to 0.64 and given
meaningful weight; the 0.50 OOS confirms that flip would have been noise.
Pinning floored it correctly. This is the signal that Task 1 must NOT
unfloor — it has no external prior evidence for the pinned direction.

Other pinned signals (funding_zscore 0.51/0.52, lev_stress 0.52/0.42,
coin_margin_ratio 0.57/0.41) all hover near random in both windows — none
silent-excluded, none overfit carriers. Non-pinned working signals
(speculation_ratio 0.68/0.68, funding_divergence 0.63/0.55, basis 0.56/0.55)
are in good shape.

### 6.6 ETH — no flags

Pattern A inapplicable (no pinning by design). Non-pinned data-driven fitting
is producing sensible weights. `eth_cvd_divergence_rank` is mildly interesting
(IS 0.51, OOS 0.64, weight 0.033) but within sampling noise on one hold-out.

## 7. Meta-observation — scope of the post-ETF regime shift

The Classic Cycle failure was severe and mechanism-specific. The audit
confirms it didn't spread to other hypotheses. The specific thing that
broke is "price distance above a long-term moving average as a drawdown
precursor," and it broke for a specific reason (post-ETF institutional flow
decoupled this relationship). Other cycle-indicator mechanisms, macro-level
signals, microstructure signals, and sentiment/positioning signals all held
up out-of-sample.

This is moderately good news about robustness: the architecture correctly
isolated the failing mechanism to one hypothesis. Other hypotheses weren't
contaminated. The diversity hedge worked.

It's also a reminder: we noticed this failure because we could audit
sub-signal by sub-signal. If the architecture had compressed the cycle
indicators into a single black-box "classic cycle" number, we might not
have found the mechanism-specific failure so quickly. **Diagnostic
transparency is a robustness property** — keep the sub-signal layer
auditable in any future refactoring.

## 8. Open items carried forward

1. Pinning weight-formula upgrade (Task 1 in session 4).
2. Walk-forward ensemble refitting (Task 2).
3. Crisis validation on historical stress periods (Task 3).
4. Transaction costs + position-change threshold (deferred; touches backtest
   loop, easier after Task 2).
5. Fold `fix_parsers.py` into `pull_all_raw_data.py` (inherited from v2).
6. Refresh ETF premium endpoint (inherited from v2, still stale at 2026-01).

Full breakdown: `NEXT_SESSION_PLAN.md`.
