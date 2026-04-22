"""Robust ensemble: AUC-excess weights across hypotheses per regime.

PROVENANCE (v9 session, 2026-04-16): This file is the recovered pre-v11 build_robust.py,
replacing the 122-line single-fit version that was shipped in the v8 handover. It honors
all env vars exported by regenerate_canonicals.sh, implements walk-forward with monthly
refits, and reconstructs master_daily_view_wf365.csv bit-exact (ensemble_score max
diff 9.2e-7, percentile 5.0e-7, position 3.6e-6) when fed master's embedded hypothesis
columns. See refit_report_v9.md §3 for the full recovery context.

Two modes controlled by env vars (honors the variables regenerate_canonicals.sh exports):

  Single-fit mode (WALK_FORWARD=0, default):
    - Fit weights once on [ENSEMBLE_FIT_START, holdout) per regime
    - Apply those fixed weights to all days
    - Used for sf730 canonical with PERCENTILE_WINDOW=730

  Walk-forward mode (WALK_FORWARD=1):
    - Refit weights every WALK_CADENCE_MONTHS (default 1) starting at the
      first month-start after ENSEMBLE_FIT_START + WARMUP_MONTHS (default 12)
    - Expanding training window: every fit uses [ENSEMBLE_FIT_START, fit_date)
    - For each day, use the weights from the most-recent-fit-date ≤ that day
    - Pre-first-fit days use the first fit's weights (backward-extended)
    - Writes weight_history_robust.csv with one row per (fit_date, regime, hypothesis)
    - Used for wf365 canonical with PERCENTILE_WINDOW=365

  Percentile basis (both modes):
    - If PERCENTILE_WINDOW is an integer string, use rolling(N, min_periods=180)
    - Anything else (e.g. "expanding"), use expanding(min_periods=180)

  Position mapping:
    - Linear-hybrid between POSITION_LONG_THR (default 0.45) and POSITION_DEF_THR (default 0.80)
    - Full long below LONG_THR, full defense above DEF_THR, linear between
"""
import os
import numpy as np
import pandas as pd
from common import (
    HYP, FINAL, load_labels, load_regime, load_holdout_start, MODEL_START,
    load_btc_price, compute_auc, CALIB_LABEL, ENSEMBLE_FIT_START,
)

# ─── Env var configuration ───────────────────────────────────────────────────
WALK_FORWARD        = int(os.environ.get("WALK_FORWARD", "0"))
WALK_CADENCE_MONTHS = int(os.environ.get("WALK_CADENCE_MONTHS", "1"))
WARMUP_MONTHS       = int(os.environ.get("WARMUP_MONTHS", "12"))
_PW                 = os.environ.get("PERCENTILE_WINDOW", "expanding")
try:
    PERCENTILE_WINDOW: int | str = int(_PW)
except ValueError:
    PERCENTILE_WINDOW = _PW  # "expanding"
POSITION_LONG_THR   = float(os.environ.get("POSITION_LONG_THR", 0.45))
POSITION_DEF_THR    = float(os.environ.get("POSITION_DEF_THR",  0.80))

print(f"Config: WALK_FORWARD={WALK_FORWARD}  CADENCE={WALK_CADENCE_MONTHS}mo  "
      f"WARMUP={WARMUP_MONTHS}mo  PCT_WIN={PERCENTILE_WINDOW}  "
      f"POS_THRS=({POSITION_LONG_THR}, {POSITION_DEF_THR})")

HYPOTHESES = [
    ("macro_equities",    "macro_equities.parquet"),
    ("cme",               "cme.parquet"),
    ("crypto_derivatives","crypto_derivatives.parquet"),
    ("classic_cycle",     "classic_cycle.parquet"),
    ("etf_flows",         "etf_flows.parquet"),
    # ETH hypothesis removed from ensemble as of v12 (2026-04-17). Reason:
    # ETH-rotation-driven drawdown signals in 2024-2025 were period-specific
    # (ETH euphoria pulling capital from BTC), not a stable crypto-structure
    # feature. `build_eth.py` still runs and produces hypothesis_eth outputs
    # for per-hypothesis health-check monitoring (open item #6). See
    # refit_report_v12.md for full reasoning and the h2025 Sharpe cost (-0.36).
]
HYP_NAMES = [n for n, _ in HYPOTHESES]

# ─── Load data ───────────────────────────────────────────────────────────────
scores = {}
for name, fname in HYPOTHESES:
    df = pd.read_parquet(HYP / fname)
    scores[name] = df["score"]
score_df = pd.DataFrame(scores)

regime_df = load_regime()
labels    = load_labels()
btc       = load_btc_price()
holdout   = load_holdout_start()

df = score_df.join(regime_df[["regime"]]).join(labels[[CALIB_LABEL]]).join(btc[["close"]])
df["regime"] = df["regime"].astype(str)


# ─── Ensemble-level AUC-excess weights (same formula in single-fit & walk-forward) ───
def fit_weights(train_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Fit per-regime weights over train_df. train_df must have regime, CALIB_LABEL,
    and all hypothesis columns. Returns {regime: {hyp: weight}}."""
    W = {}
    for regime in ["bull", "neutral", "bear"]:
        mask = (train_df["regime"] == regime) & train_df[CALIB_LABEL].notna()
        y = train_df.loc[mask, CALIB_LABEL].astype(float)
        aucs = {n: compute_auc(y, train_df.loc[mask, n]) for n in HYP_NAMES}
        excess = {n: max((a or 0.5) - 0.5, 0.01) if not np.isnan(a) else 0.0
                  for n, a in aucs.items()}
        total = sum(excess.values())
        W[regime] = {n: (excess[n]/total if total > 0 else 1/len(excess))
                     for n in HYP_NAMES}
    return W


def print_weights(W: dict, label: str = ""):
    hdr = f"{'regime':10s}  " + "  ".join(f"{n[:8]:>10s}" for n in HYP_NAMES)
    print(hdr)
    for regime in ["bull", "neutral", "bear"]:
        print(f"{regime:10s}  " + "  ".join(f"{W[regime][n]:>10.4f}" for n in HYP_NAMES))


# ─── Mode-specific weight history ────────────────────────────────────────────
weight_history: list[dict] = []

if WALK_FORWARD:
    # Walk-forward: monthly refits starting at month-start after ENSEMBLE_FIT_START + WARMUP_MONTHS
    warmup_end = ENSEMBLE_FIT_START + pd.DateOffset(months=WARMUP_MONTHS)
    # First fit at the month-start ON OR AFTER warmup_end
    first_fit = (warmup_end + pd.offsets.MonthBegin(0)) if warmup_end.day == 1 else \
                (warmup_end + pd.offsets.MonthBegin(1))
    data_end = df.index.max()
    # Generate fit dates at MonthBegin cadence
    fit_dates = pd.date_range(first_fit, data_end, freq=f"{WALK_CADENCE_MONTHS}MS", tz=first_fit.tz)
    print(f"\nWalk-forward: {len(fit_dates)} fits from {fit_dates[0].date()} to {fit_dates[-1].date()}")

    # Fit at each date using expanding window [ENSEMBLE_FIT_START, fit_date)
    # Note: no holdout clamp — walk-forward uses all available data up to fit_date.
    # This is intentional: each fit represents "what we would have known then."
    # The hold-out evaluation is separate (it's how we measure performance of the
    # resulting ensemble score), but weights themselves are fit on everything available.
    per_date_weights: dict[pd.Timestamp, dict] = {}
    for fd in fit_dates:
        mask = (df.index >= ENSEMBLE_FIT_START) & (df.index < fd)
        train = df.loc[mask]
        W = fit_weights(train)
        per_date_weights[fd] = W
        for regime in ["bull", "neutral", "bear"]:
            for hyp in HYP_NAMES:
                weight_history.append({
                    "fit_date": fd.strftime("%Y-%m-%d"),
                    "regime": regime,
                    "hypothesis": hyp,
                    "weight": W[regime][hyp],
                })

    # Print weights at latest fit
    print(f"\nLatest fit weights ({fit_dates[-1].date()}):")
    print_weights(per_date_weights[fit_dates[-1]])

    # Build per-row weights by looking up the most recent fit_date ≤ row date.
    # Pre-first-fit rows backward-extend to the first fit's weights.
    fit_dates_list = list(fit_dates)
    first_weights = per_date_weights[fit_dates[0]]

    def weights_for_date(d: pd.Timestamp) -> dict[str, dict]:
        # Binary search: find largest fit_date ≤ d
        if d < fit_dates_list[0]:
            return first_weights
        # Iterate backward (fast enough at 46 fits)
        for fd in reversed(fit_dates_list):
            if fd <= d:
                return per_date_weights[fd]
        return first_weights

else:
    # Single-fit mode: one fit over [ENSEMBLE_FIT_START, holdout)
    mask = (df.index >= ENSEMBLE_FIT_START) & (df.index < holdout)
    train = df.loc[mask]
    W_single = fit_weights(train)
    print(f"\nSingle-fit weights (train window {ENSEMBLE_FIT_START.date()} → {holdout.date()}):")
    print_weights(W_single)

    def weights_for_date(d: pd.Timestamp) -> dict[str, dict]:
        return W_single


# ─── Compute ensemble score per row (NaN-skip, RENORMALIZE on NaN) ───────────
# v13 change (2026-04-18): renormalize active weights when a hypothesis is NaN on a given
# day, matching the convention already used at the hypothesis layer by `composite_score` in
# common.py. The pre-v13 (v12 and earlier) inlined logic did NOT renormalize, compressing
# ensemble_score pre-2024 (when etf_flows is NaN) by ~(1 − w_etf_flows) — up to 33% in bear
# regime. See refit_report_v13.md.
def row_ensemble(row):
    regime = str(row["regime"])
    Wd = weights_for_date(row.name)
    regime_w = Wd.get(regime)
    if regime_w is None:
        return np.nan
    num = 0.0
    denom = 0.0
    for n in HYP_NAMES:
        v = row[n]
        if not np.isnan(v):
            num += regime_w[n] * v
            denom += regime_w[n]
    return num / denom if denom > 0 else np.nan

df["ensemble_score"] = df.apply(row_ensemble, axis=1)

# ─── Percentile ──────────────────────────────────────────────────────────────
if isinstance(PERCENTILE_WINDOW, int):
    df["percentile"] = df["ensemble_score"].rolling(PERCENTILE_WINDOW, min_periods=180).rank(pct=True)
else:
    df["percentile"] = df["ensemble_score"].expanding(min_periods=180).rank(pct=True)


# ─── Position function ───────────────────────────────────────────────────────
def position_fn(p):
    """Linear-hybrid position mapping.
      wf365 canonical: (0.45, 0.80)  — widened in v14 (2026-04-18) per experiment_taper_sweep.md
      sf730 reference: (0.55, 0.65)  — unchanged; recalibrated 2026-04-15 per refit_report_v8
    Overridable via env vars POSITION_LONG_THR / POSITION_DEF_THR.
    """
    if pd.isna(p): return np.nan
    if p <= POSITION_LONG_THR: return 1.0
    if p >= POSITION_DEF_THR: return 0.0
    return 1.0 - (p - POSITION_LONG_THR) / (POSITION_DEF_THR - POSITION_LONG_THR)

df["position"] = df["percentile"].apply(position_fn)
df["btc_return"] = df["close"].pct_change()
df["strategy_return"] = df["position"].shift(1) * df["btc_return"]


# ─── Backtest summary ────────────────────────────────────────────────────────
mask_eval = df.index >= ENSEMBLE_FIT_START
ev = df.loc[mask_eval].copy()
ev["strategy_equity"] = (1 + ev["strategy_return"].fillna(0)).cumprod()
ev["bnh_equity"]      = (1 + ev["btc_return"].fillna(0)).cumprod()

def stats(returns, equity, name):
    r = returns.dropna()
    sharpe = (r.mean()/r.std()) * np.sqrt(365) if r.std() > 0 else float("nan")
    total = equity.iloc[-1] - 1
    dd = (equity/equity.cummax() - 1).min()
    return name, sharpe, total, dd, len(r)

print("\n=== BACKTEST (robust ensemble) ===")
for s in [stats(ev["strategy_return"], ev["strategy_equity"], "strategy"),
          stats(ev["btc_return"],     ev["bnh_equity"],      "buy_and_hold")]:
    print(f"  {s[0]:15s} sharpe={s[1]:.3f}  total={s[2]*100:+.1f}%  maxDD={s[3]*100:+.1f}%  days={s[4]}")

mask_in = (df.index >= ENSEMBLE_FIT_START) & (df.index < holdout)
mask_h  = df.index >= holdout
print(f"\nEnsemble AUC in-sample: {compute_auc(df.loc[mask_in,CALIB_LABEL].astype(float), df.loc[mask_in,'ensemble_score']):.4f}")
print(f"Ensemble AUC hold-out:  {compute_auc(df.loc[mask_h,CALIB_LABEL].astype(float),  df.loc[mask_h,'ensemble_score']):.4f}")

print("\nAvg position by regime:")
for r in ["bull","neutral","bear"]:
    m = (df.index >= ENSEMBLE_FIT_START) & (df["regime"] == r)
    print(f"  {r:10s} avg_pos={df.loc[m,'position'].mean():.3f}  days={m.sum()}")

# ─── Outputs ─────────────────────────────────────────────────────────────────
out = df[["regime","ensemble_score","percentile","position","btc_return","strategy_return"]].copy()
out.to_parquet(FINAL / "ensemble_robust.parquet")

# Save "latest" weights to ensemble_weights_robust.csv (matches pre-walk-forward schema)
if WALK_FORWARD:
    latest_W = per_date_weights[fit_dates[-1]]
else:
    latest_W = W_single
ww = pd.DataFrame(latest_W).T
ww.index.name = "regime"
ww.to_csv(FINAL / "ensemble_weights_robust.csv")
print(f"\nWrote {FINAL/'ensemble_robust.parquet'}")
print(f"Wrote {FINAL/'ensemble_weights_robust.csv'}")

if weight_history:
    wh = pd.DataFrame(weight_history)
    wh.to_csv(FINAL / "weight_history_robust.csv", index=False)
    print(f"Wrote {FINAL/'weight_history_robust.csv'}  ({len(wh)} rows, {len(wh['fit_date'].unique())} fits)")
