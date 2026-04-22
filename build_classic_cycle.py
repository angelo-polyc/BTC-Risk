"""Build Classic Cycle Indicators (formerly H4v2).

As of the 2026-04 refit this hypothesis is restricted to 4 of the original 9
indicators. The other 5 (two_year_ma, heatmap, ma_roc, rainbow, bubble_index)
are "distance above long-term moving average" variants whose out-of-sample
AUC collapsed to 0.18-0.24 in 2025-2026 as BTC sustained being far above its
long-term MAs without mean-reverting — the post-ETF regime appears to have
decoupled "price above old MA" from "drawdown precursor". See refit_report.md
"Classic Cycle sub-signal audit (2026-04)" for the full diagnosis.

The 4 retained indicators (golden_ratio, bmo, ahr999, fear_greed) all held up
out-of-sample (AUC 0.72-0.81). Two of them — bmo and ahr999 — had been
floor-weighted to 0.009 in the 9-indicator fit because their in-sample AUC was
below 0.5 under pinning; removing the failed indicators lets them contribute
appropriately to the composite.

Loads of the 5 dropped indicators are kept in-place (commented out below)
rather than deleted so that re-enabling one for testing is a single-line edit.
"""
import numpy as np
import pandas as pd
from common import (
    RAW, HYP, expanding_pctile, auc_excess_weights, apply_flips,
    composite_score, load_labels, load_holdout_start, MODEL_START,
    to_utc_midnight, compute_auc, CALIB_LABEL, MIN_CALIB,
)

CG = RAW / "coinglass_cycle"

def load_cg(name) -> pd.DataFrame:
    df = pd.read_parquet(CG / f"{name}.parquet")
    df["date"] = to_utc_midnight(df["date"])
    df = df.drop_duplicates("date").set_index("date").sort_index()
    return df

btc = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
btc["date"] = to_utc_midnight(btc["date"])
idx = btc.set_index("date").sort_index().index

# Restriction set — the 4 indicators that survived the 2026-04 OOS audit.
# To add one back for testing, extend this set and the PINNED_FLIPS dict below.
KEEP_SET = {"golden_ratio", "bmo", "ahr999", "fear_greed"}

raw = {}

# 1. 2yr MA Multiplier — price / (mA730 * 5) — FLIP
# DROPPED 2026-04: NaN AUC (flat regime in modern era).
if "two_year_ma" in KEEP_SET:
    df = load_cg("2yr_ma_multiplier")
    raw["two_year_ma"] = (df["price"].astype(float) / df["ma2y_x5"].astype(float)).reindex(idx)

# 2. Golden Ratio Multiplier — price / ma350 — FLIP
if "golden_ratio" in KEEP_SET:
    df = load_cg("golden_ratio")
    raw["golden_ratio"] = (df["price"].astype(float) / df["ma350"].astype(float)).reindex(idx)

# 3. 200W MA Heatmap — price / mA1440 — KEEP
# DROPPED 2026-04: hold-out AUC 0.24 (IS was 0.67). "Distance above long-term MA"
# mechanism decoupled from drawdown risk in post-ETF regime.
if "heatmap" in KEEP_SET or "ma_roc" in KEEP_SET:
    df = load_cg("200w_heatmap")
    ma1440 = df["mA1440"].astype(float)
    if "heatmap" in KEEP_SET:
        raw["heatmap"] = (df["price"].astype(float) / ma1440).reindex(idx)

# 4. 200W MA ROC — mA1440.pct_change(30) — KEEP
# DROPPED 2026-04: hold-out AUC 0.18 (IS was 0.64). Same mechanism as heatmap.
if "ma_roc" in KEEP_SET:
    raw["ma_roc"] = ma1440.pct_change(30).reindex(idx)

# 5. BMO — value directly — FLIP
if "bmo" in KEEP_SET:
    df = load_cg("bmo")
    raw["bmo"] = df["bmo_value"].astype(float).reindex(idx)

# 6. AHR999 — 1/ahr999 (pre-inverted at raw level) — KEEP after inversion
if "ahr999" in KEEP_SET:
    df = load_cg("ahr999")
    raw["ahr999"] = df["ahr999_inv"].astype(float).reindex(idx)

# 7. Rainbow Chart Position — count(price > band) / 10 — KEEP
# DROPPED 2026-04: hold-out AUC 0.23 (IS was 0.75). Price-vs-wave-band
# variant of the long-term MA mechanism.
if "rainbow" in KEEP_SET:
    df = load_cg("rainbow_chart")
    band_cols = [f"band_{i}" for i in range(1, 11)]
    price = df["price"].astype(float)
    position = sum((price > df[c].astype(float)).astype(int) for c in band_cols) / 10.0
    raw["rainbow"] = position.reindex(idx)

# 8. Fear & Greed — raw value — FLIP
if "fear_greed" in KEEP_SET:
    df = load_cg("fear_greed")
    raw["fear_greed"] = df["fear_greed"].astype(float).reindex(idx)

# 9. Bubble Index — raw value — KEEP
# DROPPED 2026-04: hold-out AUC 0.20 (IS was 0.78). The highest-weighted
# indicator in the canonical 9-indicator fit (w=0.25) and the worst
# hold-out performer. Pure overfit.
if "bubble_index" in KEEP_SET:
    df = load_cg("bubble_index")
    raw["bubble_index"] = df["bubble_index"].astype(float).reindex(idx)

# Forward-fill cycle indicators (slow daily series, weekend gaps OK)
subs = pd.DataFrame(index=idx)
for name, s in raw.items():
    subs[name] = expanding_pctile(s.ffill(), 180)

# Calibrate
labels = load_labels()
holdout = load_holdout_start()
mask = (idx >= MIN_CALIB["classic_cycle"]) & (idx < holdout)
sig_calib = subs.loc[mask]
y_calib = labels.loc[mask, CALIB_LABEL].astype("float")

# HARD-CODED ORIENTATIONS per playbook §6 H4v2 spec — these are pinned priors
# from BTC analysts' published conventions, not data-driven flips.
# Indicators marked "flip" are inverted; indicators marked "keep" are not.
# NOTE: full 9-indicator dict kept here for documentation; the set is filtered
# by KEEP_SET below before use so dropping/re-enabling indicators is a one-edit.
PINNED_FLIPS_ALL = {
    "two_year_ma":  True,   # high price/(MA730*5) = top
    "golden_ratio": True,   # high price/MA350 = top
    "heatmap":      False,  # high price/MA1440 = top, but kept per playbook
    "ma_roc":       False,  # MA1440 ROC, kept
    "bmo":          True,   # BMO oscillator, flipped per convention
    "ahr999":       False,  # ahr999_inv pre-inverted at raw level, then kept
    "rainbow":      False,  # rainbow position, kept
    "fear_greed":   True,   # fear=danger=high reading, flipped per convention
    "bubble_index": False,  # bubble index, kept (high = danger directly)
}
PINNED_FLIPS = {k: v for k, v in PINNED_FLIPS_ALL.items() if k in KEEP_SET}
# We still let auc_excess compute weights, but apply the pinned flips and tell
# auc_excess not to re-flip (no_flip=all).
sub_pinned = subs.copy()
for col, do_flip in PINNED_FLIPS.items():
    if do_flip:
        sub_pinned[col] = 1.0 - sub_pinned[col]

weights, flips, aucs = auc_excess_weights(
    sub_pinned.loc[mask], y_calib, no_flip=set(subs.columns)
)
sub_oriented = sub_pinned  # already oriented by hard-coded flips
score = composite_score(sub_oriented, weights)  # composite_score renormalizes on NaN

print("CLASSIC CYCLE INDICATORS  (orientations PINNED per playbook spec)")
print(f"{'sub-signal':28s} {'AUC':>7s} {'pin_flip':>9s} {'weight':>8s}")
for c in subs.columns:
    print(f"{c:28s} {aucs[c]:>7.4f} {str(PINNED_FLIPS[c]):>9s} {weights[c]:>8.4f}")

mask_hold = idx >= holdout
print(f"\nIn-sample AUC: {compute_auc(y_calib, score.loc[mask]):.4f}")
print(f"Hold-out AUC : {compute_auc(labels.loc[mask_hold,CALIB_LABEL].astype('float'), score.loc[mask_hold]):.4f}")

out = pd.DataFrame({"score": score})
for c in sub_oriented.columns:
    out[f"sub_{c}"] = sub_oriented[c]
out.to_parquet(HYP / "classic_cycle.parquet")
print(f"Wrote {HYP/'classic_cycle.parquet'}")
