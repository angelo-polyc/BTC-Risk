"""Build CME (institutional positioning) — unchanged from playbook spec."""
import numpy as np
import pandas as pd
from common import (
    RAW, HYP, expanding_pctile, auc_excess_weights, apply_flips,
    composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc, CALIB_LABEL, MIN_CALIB,
)

df = pd.read_parquet(RAW / "cftc/cftc_133741_futopt.parquet")
df["date"] = to_utc_midnight(df["date"])
df = df.set_index("date").sort_index()

dealer_net    = df["Dealer_Positions_Long_All"]    - df["Dealer_Positions_Short_All"]
asset_mgr_net = df["Asset_Mgr_Positions_Long_All"] - df["Asset_Mgr_Positions_Short_All"]
lev_funds_net = df["Lev_Money_Positions_Long_All"] - df["Lev_Money_Positions_Short_All"]
oi            = df["Open_Interest_All"]

cme_w = pd.DataFrame({
    "dealer_net":         dealer_net,
    "asset_mgr_net_pct":  asset_mgr_net / oi,
    "lev_funds_net_pct":  lev_funds_net / oi,
})

# Reindex to BTC daily and forward fill
btc = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
btc["date"] = to_utc_midnight(btc["date"])
idx = btc.set_index("date").sort_index().index
cme_d = cme_w.reindex(idx).ffill()

subs = pd.DataFrame(index=idx)
subs["dealer_net_rank"]            = expanding_pctile(cme_d["dealer_net"], min_periods=30)
subs["asset_mgr_net_pct_rank"]     = expanding_pctile(cme_d["asset_mgr_net_pct"], min_periods=30)
# CRITICAL: lev funds inverted at rank step
subs["lev_funds_net_pct_rank"]     = 1.0 - expanding_pctile(cme_d["lev_funds_net_pct"], min_periods=30)

labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["cme"]) & (idx < holdout) & subs.notna().all(axis=1)
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

weights, flips, aucs = auc_excess_weights(subs.loc[mask], y_calib)
sub_oriented = apply_flips(subs, flips)
score = composite_score(sub_oriented, weights)

print("CME")
print(f"{'sub-signal':30s} {'AUC':>7s} {'flip':>6s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:30s} {aucs[c]:>7.4f} {str(flips[c]):>6s} {weights[c]:>8.4f}")

mask_hold = idx >= holdout
print(f"\nIn-sample AUC: {compute_auc(y_calib, score.loc[mask]):.4f}")
print(f"Hold-out AUC : {compute_auc(labels.loc[mask_hold,CALIB_LABEL].astype('float'), score.loc[mask_hold]):.4f}")

out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "cme.parquet")
print(f"Wrote {HYP/'cme.parquet'}")
