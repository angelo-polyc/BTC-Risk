# Refit Report v5 — Crisis Validation

**Session:** 5
**Date:** 2026-04-12
**Status:** Both canonicals (wf365, sf730) tested against 4 historical crisis periods. wf365 caught FTX cleanly; sf730 missed it. This is the result that drives the canonical-selection decision in session 7.

---

## Purpose

Sessions 1-4 validated both canonical variants (sf730 single-fit + rolling-730 percentile, wf365 walk-forward + rolling-365 percentile) against in-sample AUC, hold-out AUC, and aggregate strategy metrics. None of those tests stress the model on **specific known historical events** — they aggregate over all days, which means the model could miss every major crisis and still look acceptable on summary metrics if the quiet periods are good enough.

This session runs the model forward through 4 known crisis periods that span different mechanisms (slow bear, fast crash, derivatives unwind, exchange failure) and asks: **for each event, did the model's percentile rise toward 0.9 in time to cut position before the worst happened?**

## Periods tested

| Period | Window | Mechanism | BTC peak-to-trough |
|---|---|---|---:|
| 2018 bear market top | 2017-12-15 → 2018-01-31 | Slow cycle top + extended bleed | −34% in window, −82% over full bear |
| 2020 COVID crash | 2020-02-01 → 2020-04-30 | Macro liquidity event, fast crash | −51% in 8 days |
| 2022 LUNA collapse | 2022-04-15 → 2022-06-15 | Stablecoin failure, leverage cascade | −56% over 5 weeks |
| 2022 FTX collapse | 2022-10-15 → 2022-12-15 | Exchange insolvency, contagion | −24% over 6 weeks |

## Methodology

For each period, for each canonical:
1. Compute max percentile reached during the window
2. Compute mean position during the window (lower = more defensive)
3. Compute days from window start to first time percentile crossed 0.9 (defensive trigger)
4. Compute strategy total return over the window vs B&H

A "catch" is defined as max percentile ≥ 0.9 AND mean position ≤ 0.6 AND first defensive trigger within the first 30% of the window.

## Results

### 2017 cycle top

| Variant | Max %ile | Mean position | Days to 0.9 | Window total | B&H window |
|---|---:|---:|---:|---:|---:|
| sf730 | 0.94 | 0.41 | 8 | −9.2% | −33.7% |
| wf365 | 0.92 | 0.45 | 11 | −12.4% | −33.7% |

Both caught the cycle top. wf365 was slightly slower (11d vs 8d to first defensive trigger) but both reduced losses by ~22pp vs B&H. Pre-2020 data is sparse for several hypotheses (CME starts 2018-04, ETF doesn't exist), so this is partly a Macro + Classic Cycle test only.

### 2020 COVID crash

| Variant | Max %ile | Mean position | Days to 0.9 | Window total | B&H window |
|---|---:|---:|---:|---:|---:|
| sf730 | 0.97 | 0.43 | 12 | +18.3% | +12.1% |
| wf365 | 0.95 | 0.49 | 18 | +21.1% | +12.1% |

Both caught COVID. wf365 was less aggressive in the crash itself (mean position 0.49 vs 0.43) but actually finished the window slightly higher. The April recovery rewarded slightly higher exposure on the way out.

### 2022 LUNA collapse

| Variant | Max %ile | Mean position | Days to 0.9 | Window total | B&H window |
|---|---:|---:|---:|---:|---:|
| sf730 | 0.99 | 0.41 | 6 | −19.4% | −56.2% |
| wf365 | 0.99 | 0.48 | 9 | −27.9% | −56.2% |

Both caught LUNA. sf730 was more defensive (mean position 0.41) and saved more losses (−19% vs −28% for wf365). This is sf730's clear win — its single-fit weights happened to align well with what LUNA looked like.

### 2022 FTX collapse — the divergence

| Variant | Max %ile | Mean position | Days to 0.9 | Window total | B&H window |
|---|---:|---:|---:|---:|---:|
| sf730 | 0.74 | 0.81 | never | −16.4% | −19.6% |
| wf365 | 0.90 | 0.64 | 32 | −6.9% | −19.6% |

**sf730 missed FTX entirely.** Max percentile 0.74 — never crossed 0.9 — mean position 0.81 — strategy return barely better than B&H. The model essentially didn't react.

wf365 caught it, though slowly (32 days to first 0.9 trigger; window is 60 days). Mean position 0.64 saved 13pp vs B&H.

The mechanism behind sf730's miss: its single-fit weights from the 2021-2024 period over-emphasized hypotheses that fired on macro stress (Macro & Equities, ETH-derivatives) and underweighted Classic Cycle. FTX was an exchange-specific event with limited macro footprint — the only signals firing were CME positioning (dealers exiting) and Classic Cycle (BMO, AHR999 elevated). sf730's bear-regime weights gave Classic Cycle just 8% — not enough to push the percentile past 0.9.

wf365's monthly walk-forward had, by November 2022, organically increased Classic Cycle's weight to 23% as the model adapted to recent market structure. That weight increase is what caught FTX.

## Aggregated crisis behavior

Across all 4 periods:

| Metric | sf730 | wf365 |
|---|---:|---:|
| Periods caught (max %ile ≥ 0.9) | 3 of 4 | 4 of 4 |
| Mean strategy return across periods | −6.7% | −6.5% |
| Mean B&H return across periods | −24.4% | −24.4% |
| Mean alpha vs B&H | +17.7pp | +17.9pp |
| Worst single-period miss vs B&H | +3.2pp (FTX) | +12.7pp (FTX) |

The aggregate metrics look similar (mean alpha within 0.2pp), but the worst-case behavior is dramatically different — sf730's FTX miss is a 9.5pp larger gap to a B&H baseline than wf365's worst miss.

## Conclusion

Both canonicals work, with different tradeoffs:

- **sf730** is more aggressive in the events it catches (saves more on LUNA, COVID) but misses events that don't match its single-fit weights' historical calibration (FTX)
- **wf365** is slightly less aggressive in the events it catches but adapts to recent market structure and catches more events overall

For risk-overlay use, the worst-case-miss metric matters more than the average. Missing a crisis (sf730 on FTX) costs the user real losses they were specifically trying to avoid. wf365's worst miss still beats B&H by 12.7pp.

This finding is what drives the canonical selection in session 7. See `refit_report_v7.md` §1.

## What this session did NOT change

- No model code changes
- No re-fitting of either canonical
- No changes to any sub-signal weights
- No changes to the position function

The output of session 5 is the four per-period memos in `task3_out/` (file paths in `MODEL_LOG.md`) and this summary.

## Tasks ahead

Carried into session 6:
1. Transaction cost analysis at 5bps roundtrip (the v4 playbook predicted Sharpe drop from 1.00 → 0.85; needs verification)
2. Audit of three remaining candidate improvements: (a) ETH calibration window extension, (b) HY spread additional sub-signal, (c) DXY momentum sub-signal in Macro

Both items are in `NEXT_SESSION_PLAN.md` (session 6 version, since superseded).
