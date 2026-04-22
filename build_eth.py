"""Build ETH hypothesis (new). Per H_ETH spec.

Sub-signals (7):
  eth_funding_divergence_rank   ETH price/funding rank divergence
  eth_speculation_ratio_rank    ETH fut/spot vol (invert via flip)
  eth_liq_stress_rank           ETH long-liq cascade (invert via flip)
  eth_funding_zscore_rank       z90 of ETH OI-weighted funding
  eth_cvd_divergence_rank       ETH fut/spot CVD .diff(14)
  eth_btc_dominance_rank        BTC dominance over ETH (invert via flip)
  eth_basis_compression_rank    -ETH basis (positive = compressed)

Target: BTC's y_30 (NOT ETH's) — predicting BTC drawdowns from ETH signals.
"""
import numpy as np
import pandas as pd
from common import (
    RAW, HYP, expanding_pctile, zscore_rolling, auc_excess_weights,
    apply_flips, composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc, CALIB_LABEL, MIN_CALIB, load_btc_price, load_eth_price,
)

VELO_ETH = RAW / "velo_eth"
VELO_BTC = RAW / "velo_btc"

def load_velo(d, metric):
    df = pd.read_parquet(d / f"{metric}.parquet")
    df["date"] = to_utc_midnight(df["date"])
    return df

def agg_sum(d, metric, types):
    df = load_velo(d, metric)
    df = df[df["velo_type"].isin(types)]
    return df.groupby("date")["value"].sum()

def oi_weighted_funding(d):
    f = load_velo(d, "funding_rate")
    o = load_velo(d, "coin_open_interest_close")
    f = f[f["velo_type"] == "futures"]
    o = o[o["velo_type"] == "futures"]
    m = f.merge(o[["date","exchange","value"]], on=["date","exchange"], suffixes=("_f","_o"))
    m["w"] = m["value_f"] * m["value_o"]
    g = m.groupby("date").agg(w=("w","sum"), oi=("value_o","sum"))
    return (g["w"] / g["oi"]).rename("eth_funding_oi_w")

btc = load_btc_price()
eth = load_eth_price()
idx = btc.index
eth_close = eth["close"].reindex(idx)

# ETH aggregates
eth_funding   = oi_weighted_funding(VELO_ETH).reindex(idx)
eth_oi        = agg_sum(VELO_ETH, "coin_open_interest_close", ("futures",)).reindex(idx)
eth_fut_buy   = agg_sum(VELO_ETH, "buy_dollar_volume",  ("futures",)).reindex(idx)
eth_fut_sell  = agg_sum(VELO_ETH, "sell_dollar_volume", ("futures",)).reindex(idx)
eth_spot_buy  = agg_sum(VELO_ETH, "buy_dollar_volume",  ("spot",)).reindex(idx)
eth_spot_sell = agg_sum(VELO_ETH, "sell_dollar_volume", ("spot",)).reindex(idx)
eth_long_liq  = agg_sum(VELO_ETH, "sell_liquidations_dollar_volume", ("futures",)).reindex(idx)  # Velo: sell_liq = longs
eth_short_liq = agg_sum(VELO_ETH, "buy_liquidations_dollar_volume",  ("futures",)).reindex(idx)
eth_total_liq = eth_long_liq + eth_short_liq

# BTC aggregates needed for dominance signal
btc_oi      = agg_sum(VELO_BTC, "coin_open_interest_close", ("futures",)).reindex(idx)
btc_fut_buy = agg_sum(VELO_BTC, "buy_dollar_volume",  ("futures",)).reindex(idx)
btc_fut_sell= agg_sum(VELO_BTC, "sell_dollar_volume", ("futures",)).reindex(idx)
btc_fut_vol = btc_fut_buy + btc_fut_sell
eth_fut_vol = eth_fut_buy + eth_fut_sell

# Coinglass ETH basis
basis_df = pd.read_parquet(RAW / "coinglass_h2/basis_eth.parquet")
basis_df["date"] = to_utc_midnight(basis_df["date"])
basis_df = basis_df.drop_duplicates("date").set_index("date").sort_index()
eth_basis = basis_df["close_basis"].astype(float).reindex(idx)

W = 14
EPCT = lambda s: expanding_pctile(s, 180)
subs = pd.DataFrame(index=idx)

# 1. funding divergence
fr = EPCT(eth_close.pct_change(W))
fd = EPCT(eth_funding.rolling(W).mean())
subs["eth_funding_divergence_rank"] = EPCT(fr - fd)

# 2. speculation ratio (flip)
spot_vol = (eth_spot_buy + eth_spot_sell).replace(0, np.nan)
subs["eth_speculation_ratio_rank"] = EPCT(eth_fut_vol / spot_vol)

# 3. liq stress (flip)
long_share = eth_long_liq / eth_total_liq.replace(0, np.nan)
subs["eth_liq_stress_rank"] = EPCT(EPCT(long_share) * EPCT(eth_total_liq))

# 4. funding z-score
subs["eth_funding_zscore_rank"] = EPCT(zscore_rolling(eth_funding, 30, min_periods=15))

# 5. CVD divergence
fut_cvd  = (eth_fut_buy - eth_fut_sell).fillna(0).cumsum()
spot_cvd = (eth_spot_buy - eth_spot_sell).fillna(0).cumsum()
subs["eth_cvd_divergence_rank"] = EPCT((fut_cvd - spot_cvd).diff(W))

# 6. BTC dominance over ETH (flip via AUC)
oi_dom  = (btc_oi / eth_oi.replace(0, np.nan)).pct_change(W)
vol_dom = (btc_fut_vol / eth_fut_vol.replace(0, np.nan)).pct_change(W)
subs["eth_btc_dominance_rank"] = EPCT(oi_dom + vol_dom)

# 7. basis compression
subs["eth_basis_compression_rank"] = EPCT(-eth_basis)

# Calibrate against BTC's y_30
labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["eth"]) & (idx < holdout)
sig_calib = subs.loc[mask]
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

weights, flips, aucs = auc_excess_weights(sig_calib, y_calib)
sub_oriented = apply_flips(subs, flips)
score = composite_score(sub_oriented, weights)

print("ETH HYPOTHESIS")
print(f"{'sub-signal':32s} {'AUC':>7s} {'flip':>6s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:32s} {aucs[c]:>7.4f} {str(flips[c]):>6s} {weights[c]:>8.4f}")

mask_hold = idx >= holdout
print(f"\nIn-sample AUC: {compute_auc(y_calib, score.loc[mask]):.4f}")
print(f"Hold-out AUC : {compute_auc(labels.loc[mask_hold,CALIB_LABEL].astype('float'), score.loc[mask_hold]):.4f}")

out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "eth.parquet")
print(f"Wrote {HYP/'eth.parquet'}")
