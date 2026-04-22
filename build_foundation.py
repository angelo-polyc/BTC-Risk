"""Build regime.parquet and labels.parquet from BTC price.

Regime classifier: D2h — drawdown-from-365d-peak with three-state hysteresis.
Labels: forward 30d and 60d max drawdown, binary threshold -20%.

History / provenance (our project):
  - Through v8 (2026-04-15): canonical was 200d smoothed momentum with
    hysteresis (0.35 / 0.24 / -0.15 / -0.08).
  - v11 (2026-04-16): D2h briefly made canonical, then rolled back the same
    day over worse full-window Sharpe (0.78 vs 1.10).
  - Current session: D2h re-adopted per explicit user directive, following
    d2h_spec.md. The spec acknowledges the full-window cost (Sharpe 0.993 ->
    0.761 on its reference data) and defends the design on three grounds:
    (a) honesty — the label describes today rather than lagging 60-80 days,
        which the v11 post-mortem had also surfaced (Oct 2025 drawdown with
        the classifier still reading bull);
    (b) hold-out AUC advantage (+0.16);
    (c) parameter provenance — 4 parameters from market convention rather
        than grid search.
    Paper-trading validation remains pending before real-capital deployment.
    Revert procedure: comment out the D2h block below and restore the
    previous 200d-smoothed-momentum block (preserved in git history of this
    file in our project, or in /mnt/user-data/outputs/rollback_to_v8/ per
    START_HERE.md).
"""
import numpy as np
import pandas as pd
from common import load_btc_price, DERIVED, load_holdout_start, MODEL_START

btc = load_btc_price()
close = btc["close"]


# ═══════════════════════ D2h drawdown-from-peak regime classifier ════════════
# Three-state hysteresis on drawdown from trailing 365-day peak:
#
#   dd(t) = close(t) / max(close[t-365 : t]) - 1     (always <= 0)
#
#   Enter bull: dd > -0.05    (within 5% of trailing 1y peak)
#   Exit bull:  dd < -0.15    (10pp hysteresis; commentary starts questioning)
#   Enter bear: dd < -0.30    (crypto-specific bear-market convention)
#   Exit bear:  dd > -0.20    (10pp hysteresis; recovery to "only 20% off")
#
# Every regime change passes through neutral (no direct bull<->bear transitions)
# as a consequence of the hysteresis gaps.
#
# Parameter provenance: all four thresholds and the 365d window chosen from
# market convention, NOT from grid search. See d2h_spec.md.

PEAK_WIN = 365
DD_MIN_PERIODS = 60  # rolling max is valid after ~60 days of price history

dd_from_peak = close / close.rolling(PEAK_WIN, min_periods=DD_MIN_PERIODS).max() - 1.0


def classify_d2h(dd: pd.Series) -> pd.Series:
    """Drawdown-from-peak three-state hysteresis classifier.

    Defaults to 'neutral' until enough price history accumulates (dd is NaN
    for the first DD_MIN_PERIODS days).
    """
    states = []
    state = "neutral"
    for v in dd.values:
        if pd.isna(v):
            states.append(state)
            continue
        if state == "neutral":
            if v > -0.05:
                state = "bull"
            elif v < -0.30:
                state = "bear"
        elif state == "bull":
            if v < -0.15:
                state = "neutral"
        elif state == "bear":
            if v > -0.20:
                state = "neutral"
        states.append(state)
    return pd.Series(states, index=dd.index, dtype="category")


regime = classify_d2h(dd_from_peak)
regime_df = pd.DataFrame({
    "dd_from_365d_peak": dd_from_peak,
    "regime": regime,
})
regime_df.to_parquet(DERIVED / "regime.parquet")


# ═══════════════════════ Forward drawdown labels ═════════════════════════════
def fwd_max_dd(c: pd.Series, n: int) -> pd.Series:
    out = pd.Series(np.nan, index=c.index)
    arr = c.values
    for i in range(len(arr) - 1):
        end = min(i + 1 + n, len(arr))
        if end - (i + 1) < n:
            out.iloc[i] = np.nan
        else:
            window = arr[i + 1:end]
            out.iloc[i] = window.min() / arr[i] - 1.0
    return out


fwd_30 = fwd_max_dd(close, 30)
fwd_60 = fwd_max_dd(close, 60)

y_30 = pd.Series(pd.array([pd.NA] * len(close), dtype="Int64"), index=close.index)
y_60 = pd.Series(pd.array([pd.NA] * len(close), dtype="Int64"), index=close.index)
y_30[fwd_30.notna()] = (fwd_30[fwd_30.notna()] <= -0.20).astype("Int64")
y_60[fwd_60.notna()] = (fwd_60[fwd_60.notna()] <= -0.20).astype("Int64")

labels_df = pd.DataFrame({
    "fwd_30d_max_dd": fwd_30,
    "fwd_60d_max_dd": fwd_60,
    "y_30": y_30,
    "y_60": y_60,
})
labels_df.to_parquet(DERIVED / "labels.parquet")


# ═══════════════════════ Summary ═════════════════════════════════════════════
holdout = load_holdout_start()
print(f"BTC price: {close.index.min()} → {close.index.max()} ({len(close)} rows)")
print(f"MODEL_START: {MODEL_START}")
print(f"HOLDOUT_START: {holdout}")
print(f"Regime classifier: D2h drawdown-from-365d-peak, hysteresis (-0.05/-0.15/-0.30/-0.20)")
print(f"Regime counts (full): {regime_df['regime'].value_counts().to_dict()}")

mask_post = regime_df.index >= MODEL_START
print(f"Regime counts (post MODEL_START): {regime_df.loc[mask_post,'regime'].value_counts().to_dict()}")
mask_calib = (regime_df.index >= MODEL_START) & (regime_df.index < holdout)
print(f"Regime counts (calibration window MODEL_START→HOLDOUT_START): {regime_df.loc[mask_calib,'regime'].value_counts().to_dict()}")
print(f"y_30 base rate (full, non-NA): {labels_df['y_30'].dropna().mean():.3f}")
print(f"y_30 base rate (post MODEL_START): {labels_df.loc[mask_post,'y_30'].dropna().mean():.3f}")
