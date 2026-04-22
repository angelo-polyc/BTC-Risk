"""Build Crypto Derivatives = merged H2 (derivatives stress) + H2B (microstructure)
   + rv21_zscore_rank (moved from H6).

Sub-signals (10):
  funding_divergence_rank   H2B — price/funding rank divergence
  liq_stress_rank           H2B — long-liq share × total-liq (flipped)
  speculation_ratio_rank    H2B — fut/spot volume ratio (flipped)
  cvd_divergence_rank       H2B — fut vs spot aggressor CVD .diff(14)
  alt_rotation_rank         H2B — ETH/BTC OI + vol rotation (flipped)
  funding_zscore_rank       H2  — z90 of OI-weighted funding (level form)
  lev_stress_rank           H2  — |funding| × (OI / OI.rolling(90).mean())
  coin_margin_ratio_rank    H2  — coin-margined OI / total OI (Coinglass)
  basis_rank                H2  — futures basis (Coinglass)
  rv21_zscore_rank          moved from H6 — 21d z-score of BTC log returns
"""
import numpy as np
import pandas as pd
from common import (
    RAW, HYP, expanding_pctile, zscore_rolling, auc_excess_weights,
    apply_flips, composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc, CALIB_LABEL, MIN_CALIB,
)

# ── Velo helpers ────────────────────────────────────────────────────────────
VELO_BTC = RAW / "velo_btc"
VELO_ETH = RAW / "velo_eth"

def load_velo_metric(d, metric: str) -> pd.DataFrame:
    df = pd.read_parquet(d / f"{metric}.parquet")
    df["date"] = to_utc_midnight(df["date"])
    return df

def agg_sum(d, metric, types=("futures","spot")) -> pd.Series:
    df = load_velo_metric(d, metric)
    df = df[df["velo_type"].isin(types)]
    return df.groupby("date")["value"].sum()

def agg_futures_only(d, metric) -> pd.Series:
    return agg_sum(d, metric, types=("futures",))

def agg_spot_only(d, metric) -> pd.Series:
    return agg_sum(d, metric, types=("spot",))

# OI-weighted funding across futures exchanges
def oi_weighted_funding(d) -> pd.Series:
    f = load_velo_metric(d, "funding_rate")
    o = load_velo_metric(d, "coin_open_interest_close")
    f = f[f["velo_type"] == "futures"]
    o = o[o["velo_type"] == "futures"]
    m = f.merge(o[["date","exchange","value"]], on=["date","exchange"], suffixes=("_f","_o"))
    m["weighted"] = m["value_f"] * m["value_o"]
    g = m.groupby("date").agg(weighted=("weighted","sum"), oi=("value_o","sum"))
    return (g["weighted"] / g["oi"]).rename("funding_oi_w")

# ── BTC daily index ────────────────────────────────────────────────────────
btc = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
btc["date"] = to_utc_midnight(btc["date"])
btc = btc.set_index("date").sort_index()
idx = btc.index
btc_close = btc["close"]

# ── Velo BTC aggregates ────────────────────────────────────────────────────
btc_funding = oi_weighted_funding(VELO_BTC).reindex(idx)
btc_oi      = agg_futures_only(VELO_BTC, "coin_open_interest_close").reindex(idx)
btc_fut_buy = agg_futures_only(VELO_BTC, "buy_dollar_volume").reindex(idx)
btc_fut_sell= agg_futures_only(VELO_BTC, "sell_dollar_volume").reindex(idx)
btc_spot_buy= agg_spot_only(VELO_BTC, "buy_dollar_volume").reindex(idx)
btc_spot_sell=agg_spot_only(VELO_BTC, "sell_dollar_volume").reindex(idx)
# Velo convention: sell_liq = longs liquidated
btc_long_liq = agg_futures_only(VELO_BTC, "sell_liquidations_dollar_volume").reindex(idx)
btc_short_liq= agg_futures_only(VELO_BTC, "buy_liquidations_dollar_volume").reindex(idx)
btc_total_liq= (btc_long_liq + btc_short_liq)

# ── Velo ETH aggregates (for alt_rotation only) ───────────────────────────
eth_oi      = agg_futures_only(VELO_ETH, "coin_open_interest_close").reindex(idx)
eth_fut_buy = agg_futures_only(VELO_ETH, "buy_dollar_volume").reindex(idx)
eth_fut_sell= agg_futures_only(VELO_ETH, "sell_dollar_volume").reindex(idx)
eth_fut_vol = (eth_fut_buy + eth_fut_sell)
btc_fut_vol = (btc_fut_buy + btc_fut_sell)

# ── Coinglass H2 series ────────────────────────────────────────────────────
def load_cgh2(name) -> pd.DataFrame:
    df = pd.read_parquet(RAW / f"coinglass_h2/{name}.parquet")
    df["date"] = to_utc_midnight(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()
    return df

cm_oi_btc = load_cgh2("coin_margin_oi_btc")["close"].astype(float).reindex(idx)
oi_agg_btc= load_cgh2("oi_aggregated_btc")["close"].astype(float).reindex(idx)
basis_btc = load_cgh2("basis_btc")["close_basis"].astype(float).reindex(idx)

# ── Sub-signals ────────────────────────────────────────────────────────────
W = 14
EPCT = lambda s: expanding_pctile(s, 180)

subs = pd.DataFrame(index=idx)

# A. funding_divergence_rank (H2B)
fr = EPCT(btc_close.pct_change(W))
fd = EPCT(btc_funding.rolling(W).mean())
subs["funding_divergence_rank"] = EPCT(fr - fd)

# B. liq_stress_rank (H2B — long share × total liq, will be flipped by AUC)
long_share = (btc_long_liq / btc_total_liq.replace(0, np.nan))
subs["liq_stress_rank"] = EPCT(EPCT(long_share) * EPCT(btc_total_liq))

# C. speculation_ratio_rank (H2B)
fut_vol = btc_fut_buy + btc_fut_sell
spot_vol = (btc_spot_buy + btc_spot_sell).replace(0, np.nan)
subs["speculation_ratio_rank"] = EPCT(fut_vol / spot_vol)

# D. cvd_divergence_rank (H2B)
fut_cvd  = (btc_fut_buy - btc_fut_sell).fillna(0).cumsum()
spot_cvd = (btc_spot_buy - btc_spot_sell).fillna(0).cumsum()
subs["cvd_divergence_rank"] = EPCT((fut_cvd - spot_cvd).diff(W))

# E. alt_rotation_rank (H2B)
eth_btc_oi  = (eth_oi / btc_oi.replace(0, np.nan)).pct_change(W)
eth_btc_vol = (eth_fut_vol / btc_fut_vol.replace(0, np.nan)).pct_change(W)
subs["alt_rotation_rank"] = EPCT((EPCT(eth_btc_oi) + EPCT(eth_btc_vol)) / 2.0)

# F. funding_zscore_rank (H2)
subs["funding_zscore_rank"] = EPCT(zscore_rolling(btc_funding, 30, min_periods=15))

# G. lev_stress_rank (H2): |funding| * (OI / OI.MA90)
oi_ratio = btc_oi / btc_oi.rolling(90, min_periods=30).mean()
subs["lev_stress_rank"] = EPCT(btc_funding.abs() * oi_ratio)

# H. coin_margin_ratio_rank (H2 — Coinglass)
subs["coin_margin_ratio_rank"] = EPCT(cm_oi_btc / oi_agg_btc.replace(0, np.nan))

# I. basis_rank (H2 — Coinglass)
subs["basis_rank"] = EPCT(basis_btc)

# J. rv21_zscore_rank (moved from H6) — 21d z-score of BTC log returns
log_ret = np.log(btc_close / btc_close.shift(1))
rv21 = log_ret.rolling(21, min_periods=10).std() * np.sqrt(252)
subs["rv21_zscore_rank"] = EPCT(zscore_rolling(rv21, 90, min_periods=30))

# ── Calibrate ───────────────────────────────────────────────────────────────
labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["crypto_derivatives"]) & (idx < holdout)
sig_calib = subs.loc[mask]
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

weights, flips, aucs = auc_excess_weights(sig_calib, y_calib, no_flip={
    # Pinned by structural priors:
    "funding_zscore_rank",     # high funding = overleveraged longs = danger
    "lev_stress_rank",         # high leverage stress = danger by construction
    "coin_margin_ratio_rank",  # high coin-margin share = reflexive fragility
    "rv21_zscore_rank",        # high realized vol = stress
})
sub_oriented = apply_flips(subs, flips)
score = composite_score(sub_oriented, weights)

print("CRYPTO DERIVATIVES")
print(f"{'sub-signal':28s} {'AUC':>7s} {'flip':>6s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:28s} {aucs[c]:>7.4f} {str(flips[c]):>6s} {weights[c]:>8.4f}")

mask_hold = idx >= holdout
print(f"\nIn-sample AUC: {compute_auc(y_calib, score.loc[mask]):.4f}")
print(f"Hold-out AUC : {compute_auc(labels.loc[mask_hold,CALIB_LABEL].astype('float'), score.loc[mask_hold]):.4f}")

out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "crypto_derivatives.parquet")
print(f"Wrote {HYP/'crypto_derivatives.parquet'}")
