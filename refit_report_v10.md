# Refit Report — v10 (D2h + ETF Flows V4 + fresh Coinglass premium)

**Date:** 2026-04-17
**Scope:** Three structural drop-ins against v8 canonical, each isolated at the eventual (fresh-premium) baseline for honest attribution.

**Outcome:**
- **D2h regime classifier** (200d smoothed momentum → drawdown-from-365d peak). Hold-out AUC +0.113, full-window Sharpe −0.19. Expected tradeoff per `d2h_spec.md`.
- **Fresh Coinglass premium** on top of the v8 ETF builder is essentially no-op at the ensemble level (full Sharpe −0.006, hold-out Sharpe −0.07, hold-out AUC +0.006). The v8 builder's premium signal doesn't reach deep enough into the recent hold-out days for this to matter much.
- **ETF Flows V4 hybrid** on top of fresh premium: hypothesis-level hold-out AUC −0.049 (genuine signal-quality loss), ensemble-level impact small and mixed (wf365 full Sharpe +0.026 / hold-out Sharpe +0.011; sf730 full Sharpe −0.035 / hold-out Sharpe **+0.404**). V4 is defensible on structural grounds (real volume share, vendor decoupling, graceful degradation), less so on KPI grounds.
- **New canonical recommended.** Net end-to-end: full Sharpe −0.17, hold-out Sharpe −0.04, **hold-out AUC +0.11**, hold-out MaxDD +1.6 pp.

**Correction to my earlier attribution.** The prior version of this report measured the V4 effect as `s3 − s2` (both with stale premium) and the fresh-premium effect as `s4 − s3` (v4 builder in both arms). That mis-isolated both deltas because V4's graceful-degradation path (NaN-skip premium during the stale window) interacts with the stale→fresh transition. The honest isolation, used here, measures each change against a fixed baseline of the other two: D2h = s2−s1 (premium stale throughout), Fresh premium = s5−s2 (v8 ETF throughout), V4 = s4−s5 (fresh premium throughout). This changes the attribution meaningfully — see §2 for the reconciliation.

---

## 1 — Scenarios

Five scenarios run end-to-end through `regenerate_canonicals.sh`:

| Scenario | Regime classifier | ETF Flows builder | Coinglass premium |
|---|---|---|---|
| **s1 — baseline** | 200d smoothed momentum | v8 (Coinglass flow + proxy volume) | stale (484 rows, ends 2026-01-06) |
| **s2 — +D2h** | **D2h** | v8 | stale |
| **s5 — +D2h +fresh** | D2h | v8 | **fresh** (552 rows, ends 2026-04-15) |
| **s4 — +D2h +fresh +V4** | D2h | **V4 hybrid** | fresh |
| s3 — (not on sequence) | D2h | V4 hybrid | stale |

s1 → s2 → s5 → s4 is the isolated-attribution sequence. s3 is retained as a diagnostic — it's where V4 runs in degraded (V3-equivalent) mode.

Each scenario runs foundation + 6 hypotheses + `build_robust.py` × 4 variants (sf730/wf365 × y_60/y_30). Reported numbers are wf365 × y_60 (deployed canonical) and sf730 × y_60 (reference) unless otherwise noted. All Sharpe / return / drawdown numbers are post-5bps roundtrip cost. Eval window starts 2021-06-30; hold-out starts 2025-04-14.

**Absolute-level caveat.** Baseline is reconstructed from `raw_data_export.csv` (same recipe as v9's bit-exact reproduction at the hypothesis layer). Full-window Sharpe is 1.07 vs HANDOVER's cited 1.16 — consistent with v9's documented FRED-revision drift in three macro sub-signals (T10Y2Y, DFF, BAMLH0A0HYM2). Attribution deltas are robust (all scenarios share the same lineage); only absolute comparisons to the shipped HANDOVER numbers are affected.

## 2 — Isolated attribution (wf365 × y_60, post-5bps)

| KPI | s1 baseline | s2 +D2h | s5 +fresh | s4 +V4 |
|---|---:|---:|---:|---:|
| Full Sharpe | 1.071 | 0.880 | 0.874 | 0.900 |
| Hold-out Sharpe | 0.779 | 0.793 | 0.726 | 0.737 |
| In-sample Sharpe | 1.136 | 0.911 | 0.911 | 0.942 |
| Full total return | +378% | +240% | +240% | +251% |
| Hold-out total return | +18.0% | +17.0% | +17.1% | +15.7% |
| Full MaxDD | −38.8% | −39.4% | −39.4% | −39.4% |
| Hold-out MaxDD | −23.0% | −21.3% | −21.2% | −21.5% |
| Full AUC | 0.712 | 0.724 | 0.724 | 0.727 |
| In-sample AUC | 0.820 | 0.744 | 0.744 | 0.746 |
| **Hold-out AUC** | **0.788** | **0.901** | **0.907** | **0.902** |
| Annual turnover | 43.9 | 42.0 | 44.1 | 43.1 |

### Delta attribution (isolated)

| KPI | **D2h** (s2−s1) | **Fresh** (s5−s2) | **V4** (s4−s5) | **TOTAL** (s4−s1) |
|---|---:|---:|---:|---:|
| Full Sharpe | −0.191 | −0.006 | +0.026 | −0.172 |
| Hold-out Sharpe | +0.014 | −0.066 | +0.011 | −0.042 |
| In-sample Sharpe | −0.226 | 0.000 | +0.031 | −0.194 |
| Full AUC | +0.012 | +0.001 | +0.002 | +0.015 |
| In-sample AUC | −0.077 | 0.000 | +0.003 | −0.074 |
| **Hold-out AUC** | **+0.113** | +0.006 | −0.005 | **+0.113** |
| Hold-out MaxDD | +1.7 pp | +0.1 pp | −0.3 pp | +1.6 pp |
| Annual turnover | −1.9 | +2.1 | −1.0 | −0.8 |

### Reconciliation to prior (incorrectly isolated) attribution

The prior version of this report reported ETFv4 = +0.16 hold-out Sharpe and fresh premium = −0.22 hold-out Sharpe. Those numbers were `s3−s2` and `s4−s3`, both crossing a stale→fresh premium boundary on the v4 builder. Under correct isolation:

- The "V4 +0.16" was actually "V4 running on stale premium, relative to v8 on stale premium." When V4's premium sub-signal goes NaN-skip on the post-stale window, the composite renormalizes across three sub-signals, and that 3-sub-signal composite happens to produce different taper-zone percentiles than v8's carrying-NaN behavior. It's a premium-stale-handling difference, not a V4-vs-v8 effect.
- The "Fresh premium −0.22" was actually "swap V4-on-stale for V4-on-fresh", which activates the fourth sub-signal on 67 new days during a walk-forward refit window where the bear-regime weight for ETF Flows moves from 0.10 to 0.21 — the v9-documented rolling-percentile amplification.
- In the correct isolation, **fresh premium alone under v8 ETF moves hold-out Sharpe by −0.07 (small)**, and **V4 alone under fresh premium moves hold-out Sharpe by +0.01 (tiny)**. Total effect of V4 + fresh premium on hold-out Sharpe is −0.056, not the −0.06 my earlier decomposition showed — close in total, but very different in attribution.

Both attributions get the s1→s4 total right (−0.04 hold-out Sharpe); only the internal allocation changes. The old attribution is wrong by ±0.15 on hold-out Sharpe across the Fresh / V4 split.

## 3 — D2h: biggest effect, tradeoff matches the spec

D2h shifts the eval-window regime distribution substantially:

| Regime | 200d momentum | D2h | Δ |
|---|---:|---:|---:|
| bull | 698 (39.9%) | 582 (33.3%) | −6.6 pp |
| neutral | 669 (38.2%) | 429 (24.5%) | −13.7 pp |
| bear | 383 (21.9%) | 739 (42.2%) | **+20.3 pp** |

Nearly 2× the bear days. The reclassification pulls forward the bear call — days that 200d-momentum labeled neutral (still-positive-smoothed-return in a drawdown) become bear under D2h (>30% below trailing peak).

D2h wins exactly where it was supposed to:
- **Hold-out AUC +0.113** (0.788 → 0.901). Very large single-component gain.
- Hold-out Sharpe +0.014 (near-flat post-cost).
- Hold-out MaxDD −23.0% → −21.3% (+1.7 pp improvement).

D2h pays where the spec conceded:
- Full-window Sharpe **−0.191** (1.071 → 0.880), mostly from in-sample (−0.226).
- Full total return +378% → +240% (−138 pp equity over ~5 years).
- In-sample AUC **−0.077** (0.820 → 0.744) — consequence of shifting ~350 days out of bull/neutral into bear; per-regime calibration sees different slices of history.

This is the tradeoff `d2h_spec.md` advertised. The Oct-2025 labeling problem that motivated D2h is real in this session's data: the 200d classifier called October 2025 "bull" or "neutral" while BTC dropped ~30% from peak; D2h called "bear" correctly. The justification for adopting D2h hasn't weakened.

## 4 — Fresh Coinglass premium: small effect in isolation

Coinglass fixed their `/etf/bitcoin/premium-discount/history` endpoint this session. Previously stalled at 2026-01-06 (484 rows); now through 2026-04-15 (552 rows). 68 new days, concentrated in the hold-out window.

**Parser required a small patch.** The fresh endpoint no longer populates `premium_discount_percent` on per-ETF entries; each entry now carries `{nav_usd, market_price_usd, premium_discount_details, ticker}`. `fix_parsers.py` now computes premium directly from `(market_price_usd - nav_usd) / nav_usd` (the recipe `credentials.md` already documented as authoritative). On the 484-day overlap, the computed series correlates 0.9989 with the shipped pre-fix series — same signal, just current.

**Isolated effect** (s5 − s2, v8 ETF builder in both arms):
- Hypothesis-level hold-out AUC: 0.392 → 0.382 (−0.010, within variance for this noisy hypothesis).
- Ensemble-level hold-out AUC: +0.006 (tiny improvement).
- Full-window Sharpe: −0.006 (effectively zero).
- Hold-out Sharpe: **−0.066** (mild, not the −0.22 the prior report showed).
- Hold-out MaxDD: essentially unchanged.

The small hold-out Sharpe drop comes from a handful of bear-regime days where fresh premium data slightly changed the ETF Flows composite, which under walk-forward refit slightly changed the bear-regime ETF Flows weight (0.19 → 0.19, small), which slightly changed a few percentile rankings, which touched a few taper-zone boundaries on negative BTC days. Not knife-edge like the misattributed s4−s3 case — it really is a small effect here.

**On sf730** (single-fit, rolling-730 percentile), fresh premium delta is **exactly zero** across all KPIs to four decimals. That's because sf730 fits weights once on the calibration window ending 2025-04-14, before any fresh-premium days exist. Post-2026-01-06 premium data has no effect on weights, and the rolling-730 percentile smooths out any score changes in that recent window. Useful confirmation that the small wf365 effect is walk-forward-specific.

**Assessment:** fresh premium is a data-freshness win with essentially no model impact. The signal was rarely the decisive sub-signal in ETF Flows anyway.

## 5 — ETF Flows V4 hybrid: hypothesis-level loss, ensemble-level small

V4 sources flows from Artemis (Pearson 1.000 with Coinglass per the spec), keeps Coinglass premium, and swaps the `|flow|/btc_close` proxy for real `ETF_SPOT_VOLUME / btc_volume`. Initial validation reproduced the spec's expected calibration output bit-for-bit.

**Hypothesis-level** (s4 − s5, fresh premium in both arms):
- Hold-out AUC: 0.382 → 0.333 (**−0.049**). Genuine signal-quality loss.
- In-sample AUC: 0.519 → 0.519 (unchanged).

The real-volume-share swap was supposed to lift OOS AUC at the sub-signal level per spec (0.54 → 0.59). At the composite level with reweighting across four sub-signals, the net is a loss. ETF Flows remains the weakest of the six hypotheses regardless of variant (hold-out AUC ~0.33–0.39).

**Ensemble-level** (s4 − s5):
- wf365 Full Sharpe: +0.026, wf365 Hold-out Sharpe: +0.011 (small gains).
- sf730 Full Sharpe: −0.035, sf730 Hold-out Sharpe: **+0.404** (0.926 → 1.330).

The large sf730 hold-out gain is real but doesn't come from prediction quality (ensemble hold-out AUC moves +0.006 on sf730). It comes from V4's four-sub-signal composite hitting the rolling-730 percentile differently on a handful of days that happened to align well with BTC direction in the hold-out window.

**Assessment:** V4 is defensible on structural grounds (real volume share replacing a weak proxy, vendor decoupling from Coinglass for flow data, graceful V3 degradation when premium goes stale, cleaner parser path). It is NOT defensible as an AUC win at the hypothesis layer. The ensemble-level effect is small and variant-dependent. Safe to adopt; adopt because the structure is right, not because the KPIs are better.

## 6 — sf730 cross-check

| KPI | s1 | s2 +D2h | s5 +fresh | s4 +V4 |
|---|---:|---:|---:|---:|
| Full Sharpe | 1.405 | 1.298 | 1.298 | 1.264 |
| Hold-out Sharpe | 1.117 | 0.926 | 0.926 | **1.330** |
| Hold-out AUC | 0.787 | 0.897 | 0.897 | 0.903 |

| Delta | D2h | Fresh | V4 |
|---|---:|---:|---:|
| Full Sharpe | −0.107 | 0.000 | −0.035 |
| Hold-out Sharpe | −0.191 | 0.000 | **+0.404** |
| Hold-out AUC | +0.111 | 0.000 | +0.006 |

sf730 confirms D2h's +0.11 hold-out AUC exactly. It also isolates V4 as the driver of the earlier "+0.37 hold-out Sharpe improvement" I had informally attributed to fresh premium — fresh premium does exactly nothing on sf730, and V4 does +0.40. On this variant, V4 hold-out Sharpe gain is real but AUC-neutral — still position-amplification, not prediction quality.

## 7 — Position-level mechanics: today's call unchanged

BTC is 40% below its trailing 365d peak. All four scenarios call bear + zero position at the edge:

| Scenario | regime | ensemble_score | percentile | position |
|---|---|---:|---:|---:|
| s1 baseline | bear | 0.481 | 0.718 | 0.000 |
| s2 +D2h | bear | 0.497 | 0.759 | 0.000 |
| s5 +D2h +fresh | bear | 0.511 | 0.833 | 0.000 |
| s4 +D2h +fresh +V4 | bear | 0.510 | 0.811 | 0.000 |

The disagreements were in the middle of the hold-out window, not at the edge where the live call is made.

## 8 — Recommendation

**Adopt s4 as the new canonical.** Three reasons:

1. **D2h's +0.113 hold-out AUC** is the single largest prediction-layer improvement I've seen in this model's refit history. It comes from a structural change (4 convention-based parameters replacing 6 partially-tuned ones) and carries no overfitting risk. The full-window Sharpe cost (−0.19) is expected and documented.
2. **Fresh Coinglass premium** is a data-freshness win. The ensemble effect is small; the value is not leaving a known stale endpoint in production.
3. **V4 hybrid** is defensible on structure — real volume share, vendor decoupling, cleaner degradation. It does not improve hypothesis AUC, but the ensemble-level effect is bounded and small.

Do not read the s4 wf365 hold-out Sharpe of 0.74 as the deployment-relevant number in isolation. It reflects that s5 → s4 and prior steps moved percentile rankings across taper-zone boundaries on a handful of days. On sf730 (same signals, different percentile window) the same changes produce hold-out Sharpe 1.33. Paper trading will measure which window the live model actually sits on.

**Structural fix for rolling-percentile amplification remains deferred.** This session confirms what v9 showed: small prediction-layer changes can produce large action-layer changes via the rolling-percentile→taper-zone pathway. Candidates per v9 (sigmoid on absolute ensemble_score, multi-window percentile average, widened taper zone, probability calibration) unchanged. First clean refit opportunity after paper trading completes.

## 9 — What did NOT change this session

- `common.py`, `build_robust.py`, the five non-ETF hypothesis builders.
- Position thresholds, `MIN_CALIB`, pinning sets, calibration labels.
- Shipped canonical CSVs in `/mnt/project/` — new outputs are in `/mnt/user-data/outputs/`.
- Paper-trading protocol. HANDOVER's "4+ weeks of clean paper trading before real capital" stays in force and presumably resets on this structural change.

## 10 — Session artifacts

### Code / data deliverables (all in `/mnt/user-data/outputs/`)
- `build_foundation.py` — D2h classifier
- `build_etf_flows.py` — V4 hybrid builder
- `pull_artemis_etf.py` — Artemis SDK-based pull
- `fix_parsers.py` — patched ETF premium parser
- `artemis_etf_btc.parquet` — Artemis pull, 837 rows, drop at `data/raw/artemis_etf/btc.parquet`
- `coinglass_etf_premium_discount.parquet` — fresh Coinglass pull, 552 rows, drop at `data/raw/coinglass_h3/etf_premium_discount.parquet`
- `master_daily_view_wf365_v10.csv` — new canonical, 4,228 rows

### Analysis CSVs
- `kpi_wf365_y60.csv` — five-scenario KPI table, wf365 × y_60
- `kpi_wf365_y60_deltas.csv` — **corrected** isolated attribution
- `kpi_sf730_y60.csv` — sf730 four-scenario cross-check
- `per_hypothesis_auc.csv` — per-hypothesis hold-out AUC across all four scenarios

### Patches
- `HANDOVER_patch.md` — diffs to apply to `/mnt/project/HANDOVER.md`
- `credentials_patch.md` — diffs to apply to `/mnt/project/credentials.md`

## 11 — Open items (HANDOVER.md carryover, updated)

1. **Paper trading shadow run (4+ weeks).** Blocking prerequisite for real capital. Presumably resets on this structural change.
2. **Cardinal calibration retry.** Session-8 plan in `refit_report_v7.md` §3.
3. **Operational comms runtime.** Scheduler, trigger evaluator, event-alert charts, dedup, agent wiring.
4. **Operational hygiene.** Add Artemis to `credentials.md`; install `artemis` SDK; wire `pull_artemis_etf.py` into `pull_all_raw_data.py`; remove stale-endpoint note from credentials. `fix_parsers.py` patched this session.
5. `OPERATIONS.md`.
6. **Annual health-check script.** Add ETF Flows hold-out AUC stability monitoring (oscillates 0.33–0.39 across variants; current v10 value 0.33 is within band).
7. **Deferred: structural fix for rolling-percentile amplification.** This session is another reinforcing example. Candidates unchanged.
