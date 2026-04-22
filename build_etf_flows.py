"""Build ETF Flows hypothesis (V4 hybrid) — per etf_flows_v4_hybrid_spec.md.

V4 hybrid: Artemis for flow/volume series, Coinglass for premium/NAV series.

Sub-signals (all unpinned; direction learned by AUC-excess flip at calibration):
  1. etf_net_flow_rank         — Artemis ETF_FLOWS, 7d rolling sum
  2. etf_premium_rank          — Coinglass avg_premium_pct (NaN on stale days;
                                 composite_score NaN-skip renormalizes the
                                 remaining three — V4 degrades to V3 exactly)
  3. etf_flow_divergence_rank  — sign(price.diff(7)) × flow.rolling(7).sum()
  4. etf_volume_share_rank     — ETF_SPOT_VOLUME / btc_volume (real volume
                                 share; replaces the prior |flow|/btc_close
                                 proxy, lifting sub-signal OOS AUC 0.54 -> 0.59)

Active from 2024-01-11 (US spot BTC ETF launch); all sub-signals NaN before.

Upstream provenance note: Artemis ETF_FLOWS wraps Coinglass internally (Pearson
1.000 over 581-day overlap per the spec). The swap is a vendor-path change for
flows, not a data-lineage change. The operational benefits are (a) fewer parser
bugs and (b) access to ETF_SPOT_VOLUME, which Coinglass does not expose as a
clean aggregate series.

Requires pull_artemis_etf.py to have produced data/raw/artemis_etf/btc.parquet.
If that file is missing, this builder fails loud with a pointer to the pull
script rather than silently falling back to Coinglass flow data (which would
hide an upstream failure).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from common import (
    RAW, HYP, expanding_pctile, auc_excess_weights, apply_flips,
    composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc, CALIB_LABEL, MIN_CALIB, load_btc_price,
)

ETF_LAUNCH = pd.Timestamp("2024-01-11", tz="UTC")


# ══════════════════════ Load inputs ══════════════════════════════════════════
btc = load_btc_price()
idx = btc.index
btc_close = btc["close"]
btc_volume = btc["volume"]

# --- Artemis (flows + spot volume) ------------------------------------------
artemis_path = RAW / "artemis_etf" / "btc.parquet"
if not artemis_path.exists():
    sys.stderr.write(
        f"\nERROR: Artemis parquet not found at {artemis_path}.\n\n"
        "The V4 hybrid ETF Flows builder requires flows and spot volume from\n"
        "Artemis (sub-signals 1, 3, 4). Run:\n\n"
        "    ARTEMIS_API_KEY=<key> python3 pull_artemis_etf.py\n\n"
        "before rebuilding this hypothesis. See etf_flows_v4_hybrid_spec.md.\n"
    )
    sys.exit(2)

art = pd.read_parquet(artemis_path)
art["date"] = to_utc_midnight(art["date"])
art = art.drop_duplicates("date").set_index("date").sort_index()

missing_cols = [c for c in ("etf_flow_usd", "etf_spot_volume_usd") if c not in art.columns]
if missing_cols:
    raise RuntimeError(
        f"Artemis parquet at {artemis_path} is missing expected columns: "
        f"{missing_cols}. Columns present: {list(art.columns)}. Re-run "
        f"pull_artemis_etf.py — the extractor may need updating for a new "
        f"Artemis response shape."
    )

net_flow = art["etf_flow_usd"].astype(float).reindex(idx)
etf_spot_volume = art["etf_spot_volume_usd"].astype(float).reindex(idx)

# --- Coinglass (premium to NAV; stale as of 2026-01-06 per credentials.md) ---
prem = pd.read_parquet(RAW / "coinglass_h3/etf_premium_discount.parquet")
prem["date"] = to_utc_midnight(prem["date"])
prem = prem.drop_duplicates("date").set_index("date").sort_index()
premium = prem["avg_premium_pct"].astype(float).reindex(idx)


# ══════════════════════ Sub-signals ══════════════════════════════════════════
subs = pd.DataFrame(index=idx)

# 1. etf_net_flow_rank — 7d rolling sum of daily net flow (Artemis)
subs["etf_net_flow_rank"] = expanding_pctile(
    net_flow.rolling(7, min_periods=3).sum(),
    60,
)

# 2. etf_premium_rank — avg premium to NAV across tracked spot BTC ETFs (Coinglass).
# Stays NaN on days where Coinglass is stale; composite_score handles the gap.
subs["etf_premium_rank"] = expanding_pctile(premium, 60)

# 3. etf_flow_divergence_rank — sign of 7d price change × 7d rolling flow (Artemis).
flow_7d = net_flow.rolling(7, min_periods=3).sum()
price_dir = np.sign(btc_close.diff(7))
subs["etf_flow_divergence_rank"] = expanding_pctile(price_dir * flow_7d, 60)

# 4. etf_volume_share_rank — ETF spot volume / total BTC spot volume (real share).
# Replaces the prior |flow|/btc_close proxy. Guard against zero btc_volume just
# in case of a pathological day (shouldn't happen for BTC, but cheap insurance).
safe_btc_vol = btc_volume.where(btc_volume > 0, np.nan)
subs["etf_volume_share_rank"] = expanding_pctile(etf_spot_volume / safe_btc_vol, 60)

# All sub-signals NaN before ETF launch.
subs.loc[idx < ETF_LAUNCH] = np.nan


# ══════════════════════ Calibration ═══════════════════════════════════════════
labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["etf_flows"]) & (idx < holdout) & (idx >= ETF_LAUNCH)
sig_calib = subs.loc[mask]
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

print(f"ETF FLOWS (V4 hybrid) — calibration sample: {mask.sum()} days")

weights, flips, aucs = auc_excess_weights(sig_calib, y_calib)
sub_oriented = apply_flips(subs, flips)
score = composite_score(sub_oriented, weights)

print(f"{'sub-signal':32s} {'AUC':>7s} {'flip':>6s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:32s} {aucs[c]:>7.4f} {str(flips[c]):>6s} {weights[c]:>8.4f}")

mask_hold = idx >= holdout
print(f"\nIn-sample AUC: {compute_auc(y_calib, score.loc[mask]):.4f}")
print(f"Hold-out AUC : {compute_auc(labels.loc[mask_hold,CALIB_LABEL].astype('float'), score.loc[mask_hold]):.4f}")


# ══════════════════════ Write output ═════════════════════════════════════════
out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "etf_flows.parquet")
print(f"Wrote {HYP/'etf_flows.parquet'}")
