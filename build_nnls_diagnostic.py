"""Build ensemble (NNLS per regime) + position + backtest."""
import numpy as np
import pandas as pd
from scipy.optimize import nnls
from common import (
    HYP, FINAL, load_labels, load_regime, load_holdout_start, MODEL_START,
    load_btc_price, compute_auc, composite_score_no_renorm,
)

HYPOTHESES = [
    ("macro_equities",    "macro_equities.parquet"),
    ("cme",               "cme.parquet"),
    ("crypto_derivatives","crypto_derivatives.parquet"),
    ("classic_cycle",     "classic_cycle.parquet"),
    ("etf_flows",         "etf_flows.parquet"),
    ("eth",               "eth.parquet"),
]

# Load
scores = {}
for name, fname in HYPOTHESES:
    df = pd.read_parquet(HYP / fname)
    scores[name] = df["score"]
score_df = pd.DataFrame(scores)

regime_df = load_regime()
labels    = load_labels()
btc       = load_btc_price()
holdout   = load_holdout_start()

# Align indexes
df = score_df.join(regime_df[["regime"]], how="left").join(labels[["y_30","fwd_30d_max_dd"]], how="left")
df = df.join(btc[["close"]], how="left")
df["regime"] = df["regime"].astype(str)

# ── NNLS per regime, on calibration window
ENSEMBLE_WEIGHTS = {}
print("\nNNLS per-regime fit:")
print(f"{'regime':10s}  {'days':>6s}  " + "  ".join(f"{n:>10s}" for n,_ in HYPOTHESES))

for regime in ["bull", "neutral", "bear"]:
    mask = (df.index >= MODEL_START) & (df.index < holdout) & \
           (df["regime"] == regime) & df["y_30"].notna()
    sub = df.loc[mask, [n for n,_ in HYPOTHESES]].fillna(0)
    y = df.loc[mask, "y_30"].astype(float).values
    if len(sub) < 30:
        # Too few days, use uniform
        w = np.ones(len(HYPOTHESES))
    else:
        w, _ = nnls(sub.values, y)
        if w.sum() == 0:
            w = np.ones(len(HYPOTHESES))
    w = w / w.sum()
    ENSEMBLE_WEIGHTS[regime] = dict(zip([n for n,_ in HYPOTHESES], w))
    print(f"{regime:10s}  {len(sub):>6d}  " + "  ".join(f"{w[i]:>10.4f}" for i in range(len(HYPOTHESES))))

# ── Compute ensemble score per row using regime-dependent weights, NaN-skip (no renorm)
def row_ensemble(row):
    weights = ENSEMBLE_WEIGHTS.get(str(row["regime"]), None)
    if weights is None:
        return np.nan
    s = 0.0
    for name, _ in HYPOTHESES:
        v = row[name]
        if not np.isnan(v):
            s += weights[name] * v
    return s

df["ensemble_score"] = df.apply(row_ensemble, axis=1)

# Expanding percentile
df["percentile"] = df["ensemble_score"].expanding(min_periods=180).rank(pct=True)

# Position function (linear hybrid)
def position_fn(p):
    # Thresholds updated 2026-04-15; configurable via env vars (see build_robust.py).
    import os
    lt = float(os.environ.get("POSITION_LONG_THR", 0.55))
    dt_ = float(os.environ.get("POSITION_DEF_THR",  0.70))
    if pd.isna(p):
        return np.nan
    if p <= lt:
        return 1.0
    if p >= dt_:
        return 0.0
    return 1.0 - (p - lt) / (dt_ - lt)

df["position"] = df["percentile"].apply(position_fn)

# Backtest: use yesterday's position, today's BTC return
df["btc_return"] = df["close"].pct_change()
df["strategy_return"] = df["position"].shift(1) * df["btc_return"]

# Equity curves on the post-MODEL_START window
mask_eval = df.index >= MODEL_START
ev = df.loc[mask_eval].copy()
ev["strategy_equity"] = (1 + ev["strategy_return"].fillna(0)).cumprod()
ev["bnh_equity"]      = (1 + ev["btc_return"].fillna(0)).cumprod()

def stats(returns: pd.Series, equity: pd.Series, name: str):
    r = returns.dropna()
    sharpe = (r.mean() / r.std()) * np.sqrt(365) if r.std() > 0 else float("nan")
    total = equity.iloc[-1] - 1
    peak = equity.cummax()
    dd = (equity / peak - 1).min()
    return {
        "name": name,
        "sharpe": sharpe,
        "total_return": total,
        "max_dd": dd,
        "n_days": len(r),
    }

s_strat = stats(ev["strategy_return"], ev["strategy_equity"], "strategy")
s_bnh   = stats(ev["btc_return"],     ev["bnh_equity"],      "buy_and_hold")

print("\n=== BACKTEST (post-MODEL_START 2021-06-30 → today) ===")
for s in [s_strat, s_bnh]:
    print(f"  {s['name']:15s} sharpe={s['sharpe']:.3f}  total={s['total_return']*100:+.1f}%  maxDD={s['max_dd']*100:+.1f}%  days={s['n_days']}")

# In-sample / hold-out ensemble AUC vs y_30
mask_in = (df.index >= MODEL_START) & (df.index < holdout)
mask_h  = df.index >= holdout
auc_in = compute_auc(df.loc[mask_in,"y_30"].astype(float), df.loc[mask_in,"ensemble_score"])
auc_h  = compute_auc(df.loc[mask_h,"y_30"].astype(float),  df.loc[mask_h,"ensemble_score"])
print(f"\nEnsemble AUC vs y_30  in-sample: {auc_in:.4f}")
print(f"Ensemble AUC vs y_30  hold-out:  {auc_h:.4f}")

# Average position by regime
print("\nAvg position by regime (post MODEL_START):")
for r in ["bull","neutral","bear"]:
    m = (df.index >= MODEL_START) & (df["regime"] == r)
    print(f"  {r:10s} avg_pos={df.loc[m,'position'].mean():.3f}  days={m.sum()}")

# Save
out = df[["regime","ensemble_score","percentile","position","btc_return","strategy_return"]].copy()
out.to_parquet(FINAL / "ensemble_position_backtest.parquet")
print(f"\nWrote {FINAL/'ensemble_position_backtest.parquet'}")

# Save ensemble weights as csv
ww = pd.DataFrame(ENSEMBLE_WEIGHTS).T
ww.index.name = "regime"
ww.to_csv(FINAL / "ensemble_weights.csv")
print(f"Wrote {FINAL/'ensemble_weights.csv'}")
