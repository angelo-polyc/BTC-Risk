"""Build Macro & Equities (formerly H6).

Sub-signals (8):
  spx_overext_rank        FRED SP500 — rank of price/MA200 - 1
  real_rate_rank          FRED DFII10 — rank of level
  hy_spread_roc_rank      FRED BAMLH0A0HYM2 — rank of .diff(30)
  yield_curve_roc_rank    FRED T10Y2Y — rank of .diff(30)
  rates_abs_stress_rank   FRED DGS2/DGS10 — rank of (|z90(DGS2)| + |z90(DGS10)|)/2
  fed_funds_stress_rank   FRED DFF — rank of z90(DFF)
  vix_z90_rank            FRED VIXCLS — rank of z90(VIXCLS)
  fx_stress_rank          FRED DTWEXBGS, DEXJPUS — rank of z90(DTWEXBGS) - z90(DEXJPUS)

Removed from prior H6: rv21_zscore_rank (moved to Crypto Derivatives),
                      funding_rate_oi_weighted_rank (was placeholder, moved to Crypto Derivatives).
"""
import numpy as np
import pandas as pd
from common import (
    RAW, HYP, expanding_pctile, zscore_rolling, auc_excess_weights,
    apply_flips, composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc as compute_auc_safe, CALIB_LABEL, MIN_CALIB,
)

def load_fred(name: str) -> pd.Series:
    df = pd.read_parquet(RAW / f"fred/{name}.parquet")
    df["date"] = to_utc_midnight(df["date"])
    df = df.set_index("date").sort_index()
    return df["value"].astype(float)

# Build a unified daily index (BTC business days)
btc = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
btc["date"] = to_utc_midnight(btc["date"])
idx = btc.set_index("date").sort_index().index

def to_idx(s: pd.Series) -> pd.Series:
    return s.reindex(idx).ffill()

# Series
spx     = to_idx(load_fred("SP500"))
dfii10  = to_idx(load_fred("DFII10"))
hy_oas  = to_idx(load_fred("BAMLH0A0HYM2"))
curve   = to_idx(load_fred("T10Y2Y"))
dgs2    = to_idx(load_fred("DGS2"))
dgs10   = to_idx(load_fred("DGS10"))
dff     = to_idx(load_fred("DFF"))
vix     = to_idx(load_fred("VIXCLS"))
usdbroad= to_idx(load_fred("DTWEXBGS"))
usdjpy  = to_idx(load_fred("DEXJPUS"))

subs = pd.DataFrame(index=idx)

# spx overextension
spx_ovr = spx / spx.rolling(200, min_periods=100).mean() - 1.0
subs["spx_overext_rank"] = expanding_pctile(spx_ovr, 180)

# real rate level
subs["real_rate_rank"] = expanding_pctile(dfii10, 180)

# HY OAS rate of change
subs["hy_spread_roc_rank"] = expanding_pctile(hy_oas.diff(30), 180)

# yield curve rate of change (note: low/inverted curve = stress, but we let AUC decide via flip)
subs["yield_curve_roc_rank"] = expanding_pctile(curve.diff(30), 180)

# rates absolute stress: symmetric, captures both directions
z2  = zscore_rolling(dgs2, 90)
z10 = zscore_rolling(dgs10, 90)
subs["rates_abs_stress_rank"] = expanding_pctile((z2.abs() + z10.abs()) / 2.0, 180)

# fed funds stress
subs["fed_funds_stress_rank"] = expanding_pctile(zscore_rolling(dff, 90), 180)

# VIX z-score
subs["vix_z90_rank"] = expanding_pctile(zscore_rolling(vix, 90), 180)

# FX stress (USD strength + JPY strength = yen-carry unwind)
zb = zscore_rolling(usdbroad, 90)
zj = zscore_rolling(usdjpy, 90)
subs["fx_stress_rank"] = expanding_pctile(zb - zj, 180)

# Calibrate on pre-HOLDOUT_START data, post MODEL_START
labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["macro_equities"]) & (idx < holdout)
sig_calib = subs.loc[mask]
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

# PINNED PRIORS: these signals have decades of cross-asset evidence for direction.
# Forbid AUC-excess from flipping them — if their AUC < 0.5 they get the floor weight
# (effectively zero) but never contribute in the wrong direction.
#
# v14 note (2026-04-18): fed_funds_stress_rank REMOVED from this set. The a priori
# prior "tightening = risk-asset stress" has been inconsistent on hold-out (AUC 0.072
# on the last 365 days) and the v14 health-check monitor flagged it. Unpinning is
# a no-op in the current state — its 2018-onward calibration AUC is 0.5054 (just
# above 0.5, so the auto-flip branch doesn't trigger), and its weight remains at
# the floor (3.0%) either way. The change is prospective: if a future refit sees
# fed_funds's calibration AUC cross below 0.5, auto-flip will now activate instead
# of being suppressed. See memo_v14_unpin_and_min_calib_test.md.
PINNED_DIRECTION = {
    "spx_overext_rank",        # stretched equities precede risk-asset drawdowns
    "vix_z90_rank",            # high VIX = risk off
    "hy_spread_roc_rank",      # widening credit = stress
}

weights, flips, aucs = auc_excess_weights(sig_calib, y_calib, no_flip=PINNED_DIRECTION)
sub_oriented = apply_flips(subs, flips)
score = composite_score(sub_oriented, weights)

print("MACRO & EQUITIES")
print(f"{'sub-signal':28s} {'AUC':>7s} {'flip':>6s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:28s} {aucs[c]:>7.4f} {str(flips[c]):>6s} {weights[c]:>8.4f}")
print(f"\nComposite in-sample AUC: {compute_auc_safe(y_calib, score.loc[mask]):.4f}  (label={CALIB_LABEL})")

mask_hold = (idx >= holdout)
y_hold = labels.loc[mask_hold, CALIB_LABEL].astype("float")
print(f"Composite hold-out AUC : {compute_auc_safe(y_hold, score.loc[mask_hold]):.4f}")
print(f"Pinned: {sorted(PINNED_DIRECTION)}")

out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "macro_equities.parquet")
print(f"\nWrote {HYP / 'macro_equities.parquet'}")
