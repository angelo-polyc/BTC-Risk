"""DRAWDOWN PROBABILITY + MAGNITUDE CALIBRATION

Bolts onto wf365 ensemble_score:
  • Drawdown Risk:      P(drawdown ≥20% in next 60d) via isotonic & Platt
  • Drawdown Magnitude: 10/50/90 percentile of fwd_60d_max_dd via GBM quantile regression

Walk-forward calibration: refit monthly to match wf365 cadence.
Validates on hold-out year (2025-04-15 → present).
Reports:
  • Decile diagnostic (does magnitude even sort by ensemble_score?)
  • Reliability curve + Brier score (probability)
  • Spearman + quantile coverage (magnitude)

NOTE FOR SESSION 8: This is the SESSION 7 attempt that FAILED OOS. Use as
a starting point for the rolling-window retry. Specifically:
  1. Change `train_mask` in the walk-forward loop to a rolling-365 window
     instead of expanding from ENSEMBLE_FIT_START
  2. Replace GradientBoostingRegressor with sklearn's HuberRegressor +
     residual-std-based bands
  3. Re-run validation. See refit_report_v7.md §3 for details.

Usage:
    python3 calibration_test.py
    python3 calibration_test.py --master master_daily_view_wf365.csv \\
                                --out calibration_out
"""
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from pathlib import Path
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import GradientBoostingRegressor
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings("ignore")

ap = argparse.ArgumentParser()
ap.add_argument("--master", default="master_daily_view_wf365.csv")
ap.add_argument("--out", default="calibration_out")
args = ap.parse_args()

OUT = Path(args.out)
OUT.mkdir(exist_ok=True)
UTC = "UTC"
ENSEMBLE_FIT_START = pd.Timestamp("2021-06-30", tz=UTC)
HOLDOUT_START = pd.Timestamp("2025-04-15", tz=UTC)

# ─── Load data ───────────────────────────────────────────────────────────────
master = pd.read_csv(args.master, parse_dates=["date"]).set_index("date")
master.index = master.index.tz_localize(UTC) if master.index.tz is None else master.index.tz_convert(UTC)

df = master[["ensemble_score", "y_60", "fwd_60d_max_dd", "regime", "percentile", "position"]].copy()
print(f"Loaded: {len(df)} rows, {df.index.min().date()} → {df.index.max().date()}")
print(f"y_60 non-NaN: {df['y_60'].notna().sum()}, positives: {(df['y_60']==1).sum()}")
print(f"y_60 base rate: {df['y_60'].mean():.4f}")
print(f"fwd_60d_max_dd non-NaN: {df['fwd_60d_max_dd'].notna().sum()}, "
      f"mean: {df['fwd_60d_max_dd'].mean():.4f}, median: {df['fwd_60d_max_dd'].median():.4f}")

# ─── Step 1: Decile diagnostic (is magnitude even predictable?) ─────────────
print("\n" + "="*80)
print("STEP 1 — DECILE DIAGNOSTIC: does magnitude sort by ensemble_score?")
print("="*80)

mask_diag = (df.index >= ENSEMBLE_FIT_START) & df["fwd_60d_max_dd"].notna() & df["ensemble_score"].notna()
diag_df = df.loc[mask_diag, ["ensemble_score", "fwd_60d_max_dd", "y_60"]].copy()
diag_df["decile"] = pd.qcut(diag_df["ensemble_score"], 10, labels=False)

print(f"\n{'decile':>7s} {'es range':>16s} {'n':>5s}  {'P(y60=1)':>9s} {'mean DD':>9s} {'median DD':>10s} {'10pctl':>8s} {'90pctl':>8s}")
for d in range(10):
    sub = diag_df[diag_df["decile"]==d]
    es_lo, es_hi = sub["ensemble_score"].min(), sub["ensemble_score"].max()
    p_y60 = sub["y_60"].mean()
    mean_dd = sub["fwd_60d_max_dd"].mean()
    med_dd = sub["fwd_60d_max_dd"].median()
    p10 = sub["fwd_60d_max_dd"].quantile(0.10)
    p90 = sub["fwd_60d_max_dd"].quantile(0.90)
    print(f"{d:>7d} {es_lo:>6.3f}-{es_hi:>5.3f} {len(sub):>5d}  {p_y60:>9.3f} {mean_dd:>+9.3f} {med_dd:>+10.3f} {p10:>+8.3f} {p90:>+8.3f}")

# Spearman across all training days
sp_full, _ = spearmanr(diag_df["ensemble_score"], diag_df["fwd_60d_max_dd"])
print(f"\nFULL-WINDOW Spearman (es vs realized DD): {sp_full:.4f}  (negative = high es → more negative DD = correct direction)")

# ─── Step 2: Walk-forward calibration ─────────────────────────────────────────
print("\n" + "="*80)
print("STEP 2 — WALK-FORWARD CALIBRATION (monthly, mirrors wf365)")
print("="*80)

# Schedule: monthly, first fit at ENSEMBLE_FIT_START + 12 months, expanding window
first_fit = (ENSEMBLE_FIT_START + pd.DateOffset(months=12)).tz_convert(UTC).normalize()
last_date = df.index.max()
fit_dates = pd.date_range(start=first_fit, end=last_date, freq="MS", tz=UTC)
print(f"  {len(fit_dates)} monthly fits from {fit_dates.min().date()} to {fit_dates.max().date()}")

# Output columns
df["dd_prob_iso"] = np.nan      # isotonic probability
df["dd_prob_platt"] = np.nan    # Platt-scaled probability
df["dd_q10"] = np.nan           # 10th percentile of fwd_60d_max_dd
df["dd_q50"] = np.nan           # median
df["dd_q90"] = np.nan           # 90th

for i, fit_date in enumerate(fit_dates):
    train_mask = (df.index >= ENSEMBLE_FIT_START) & (df.index < fit_date) & \
                 df["y_60"].notna() & df["fwd_60d_max_dd"].notna() & df["ensemble_score"].notna()
    if train_mask.sum() < 60:
        continue
    
    es = df.loc[train_mask, "ensemble_score"].values
    y = df.loc[train_mask, "y_60"].astype(int).values
    dd = df.loc[train_mask, "fwd_60d_max_dd"].values
    
    # --- Probability calibrators ---
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(es, y)
    
    platt = LogisticRegression(solver="liblinear")
    platt.fit(es.reshape(-1,1), y)
    
    # --- Magnitude quantile regressors (GBM with quantile loss) ---
    qmodels = {}
    for q in [0.1, 0.5, 0.9]:
        gbm = GradientBoostingRegressor(
            loss="quantile", alpha=q,
            n_estimators=80, max_depth=3, learning_rate=0.05,
            min_samples_leaf=20, random_state=42,
        )
        gbm.fit(es.reshape(-1,1), dd)
        qmodels[q] = gbm
    
    # --- Apply to days [fit_date, next_fit_date) ---
    next_fd = fit_dates[i+1] if i+1 < len(fit_dates) else last_date + pd.Timedelta(days=31)
    apply_mask = (df.index >= fit_date) & (df.index < next_fd) & df["ensemble_score"].notna()
    apply_es = df.loc[apply_mask, "ensemble_score"].values.reshape(-1,1)
    
    df.loc[apply_mask, "dd_prob_iso"] = iso.predict(apply_es.flatten())
    df.loc[apply_mask, "dd_prob_platt"] = platt.predict_proba(apply_es)[:, 1]
    for q, mdl in qmodels.items():
        df.loc[apply_mask, f"dd_q{int(q*100)}"] = mdl.predict(apply_es)

# Backfill the warmup period using the first fit
first_apply_mask = (df.index >= ENSEMBLE_FIT_START) & (df.index < first_fit) & df["ensemble_score"].notna()
if first_apply_mask.any():
    train_mask_first = (df.index >= ENSEMBLE_FIT_START) & (df.index < first_fit) & \
                        df["y_60"].notna() & df["fwd_60d_max_dd"].notna() & df["ensemble_score"].notna()
    es0 = df.loc[train_mask_first, "ensemble_score"].values
    y0 = df.loc[train_mask_first, "y_60"].astype(int).values
    dd0 = df.loc[train_mask_first, "fwd_60d_max_dd"].values
    if len(es0) > 30 and len(np.unique(y0)) > 1:
        iso0 = IsotonicRegression(out_of_bounds="clip"); iso0.fit(es0, y0)
        platt0 = LogisticRegression(solver="liblinear"); platt0.fit(es0.reshape(-1,1), y0)
        warmup_es = df.loc[first_apply_mask, "ensemble_score"].values.reshape(-1,1)
        df.loc[first_apply_mask, "dd_prob_iso"] = iso0.predict(warmup_es.flatten())
        df.loc[first_apply_mask, "dd_prob_platt"] = platt0.predict_proba(warmup_es)[:, 1]
        for q in [0.1, 0.5, 0.9]:
            gbm0 = GradientBoostingRegressor(
                loss="quantile", alpha=q, n_estimators=80, max_depth=3,
                learning_rate=0.05, min_samples_leaf=20, random_state=42,
            )
            gbm0.fit(es0.reshape(-1,1), dd0)
            df.loc[first_apply_mask, f"dd_q{int(q*100)}"] = gbm0.predict(warmup_es)

print(f"  dd_prob_iso non-NaN: {df['dd_prob_iso'].notna().sum()}")
print(f"  dd_prob_platt non-NaN: {df['dd_prob_platt'].notna().sum()}")
print(f"  dd_q50 non-NaN: {df['dd_q50'].notna().sum()}")

# ─── Step 3: Validation on hold-out year ─────────────────────────────────────
print("\n" + "="*80)
print("STEP 3 — VALIDATION ON HOLD-OUT YEAR (2025-04-15 → present)")
print("="*80)

ho_mask = (df.index >= HOLDOUT_START) & df["y_60"].notna() & df["dd_prob_iso"].notna()
ho_dd_mask = (df.index >= HOLDOUT_START) & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna()
in_mask = (df.index >= ENSEMBLE_FIT_START) & (df.index < HOLDOUT_START) & df["y_60"].notna() & df["dd_prob_iso"].notna()

print(f"\nHold-out: {ho_mask.sum()} days with prob, {ho_dd_mask.sum()} days with magnitude")
print(f"In-sample: {in_mask.sum()} days")

# Probability validation
print("\n--- DRAWDOWN PROBABILITY ---")
def brier(p, y):
    return float(np.mean((p - y)**2))

def calib_error(p, y, n_bins=10):
    """Expected Calibration Error: weighted abs diff between mean predicted prob and mean observed."""
    bins = np.quantile(p, np.linspace(0, 1, n_bins+1))
    bins[0] = -np.inf; bins[-1] = np.inf
    ece = 0.0
    for i in range(n_bins):
        m = (p >= bins[i]) & (p < bins[i+1])
        if m.sum() < 5: continue
        ece += (m.sum()/len(p)) * abs(p[m].mean() - y[m].mean())
    return float(ece)

for name, col in [("isotonic", "dd_prob_iso"), ("Platt", "dd_prob_platt")]:
    p_in = df.loc[in_mask, col].values
    y_in = df.loc[in_mask, "y_60"].astype(int).values
    p_ho = df.loc[ho_mask, col].values
    y_ho = df.loc[ho_mask, "y_60"].astype(int).values
    print(f"\n  {name}:")
    print(f"    IS  Brier = {brier(p_in, y_in):.4f},  ECE = {calib_error(p_in, y_in):.4f},  mean p = {p_in.mean():.3f},  realized rate = {y_in.mean():.3f}")
    print(f"    OOS Brier = {brier(p_ho, y_ho):.4f},  ECE = {calib_error(p_ho, y_ho):.4f},  mean p = {p_ho.mean():.3f},  realized rate = {y_ho.mean():.3f}")

# Reference: Brier of the constant-base-rate prediction
base_rate_in = df.loc[in_mask, "y_60"].mean()
base_rate_ho = df.loc[ho_mask, "y_60"].mean()
print(f"\n  REFERENCE (constant base-rate prediction):")
print(f"    IS  Brier = {brier(np.full(in_mask.sum(), base_rate_in), df.loc[in_mask,'y_60'].astype(int).values):.4f}")
print(f"    OOS Brier = {brier(np.full(ho_mask.sum(), base_rate_ho), df.loc[ho_mask,'y_60'].astype(int).values):.4f}")

# Magnitude validation
print("\n--- DRAWDOWN MAGNITUDE ---")
realized_in = df.loc[in_mask & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna(), "fwd_60d_max_dd"].values
pred_q50_in = df.loc[in_mask & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna(), "dd_q50"].values
pred_q10_in = df.loc[in_mask & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna(), "dd_q10"].values
pred_q90_in = df.loc[in_mask & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna(), "dd_q90"].values

realized_ho = df.loc[ho_dd_mask, "fwd_60d_max_dd"].values
pred_q50_ho = df.loc[ho_dd_mask, "dd_q50"].values
pred_q10_ho = df.loc[ho_dd_mask, "dd_q10"].values
pred_q90_ho = df.loc[ho_dd_mask, "dd_q90"].values

# Spearman: predicted-magnitude rank vs realized-magnitude rank
sp_in_q50, _ = spearmanr(pred_q50_in, realized_in)
sp_ho_q50, _ = spearmanr(pred_q50_ho, realized_ho)
sp_es_in, _ = spearmanr(df.loc[in_mask & df["fwd_60d_max_dd"].notna(), "ensemble_score"], 
                        df.loc[in_mask & df["fwd_60d_max_dd"].notna(), "fwd_60d_max_dd"])
sp_es_ho, _ = spearmanr(df.loc[ho_dd_mask, "ensemble_score"], realized_ho)

print(f"\n  Spearman correlation (predicted vs realized magnitude):")
print(f"    raw ensemble_score, IS:  {sp_es_in:+.4f}")
print(f"    raw ensemble_score, OOS: {sp_es_ho:+.4f}")
print(f"    GBM q50 prediction, IS:  {sp_in_q50:+.4f}")
print(f"    GBM q50 prediction, OOS: {sp_ho_q50:+.4f}")
print(f"    (negative = correct direction; high es / high q50 magnitude → more negative realized DD)")

# Coverage at the (10, 90) interval — should be ~80%
def coverage(low, high, realized):
    return float(((realized >= low) & (realized <= high)).mean())

cov_in = coverage(pred_q10_in, pred_q90_in, realized_in)
cov_ho = coverage(pred_q10_ho, pred_q90_ho, realized_ho)
print(f"\n  Coverage of (q10, q90) interval (target = 80%):")
print(f"    IS:  {cov_in:.3f}")
print(f"    OOS: {cov_ho:.3f}")

# MAE of median prediction
mae_in = float(np.mean(np.abs(pred_q50_in - realized_in)))
mae_ho = float(np.mean(np.abs(pred_q50_ho - realized_ho)))
print(f"\n  MAE of q50 prediction:")
print(f"    IS:  {mae_in:.4f}")
print(f"    OOS: {mae_ho:.4f}")

# Pinball loss at each quantile (proper scoring rule)
def pinball(pred, realized, q):
    diff = realized - pred
    return float(np.mean(np.maximum(q*diff, (q-1)*diff)))

print(f"\n  Pinball loss at q10/q50/q90 (lower better):")
print(f"    IS:  q10={pinball(pred_q10_in, realized_in, 0.1):.4f}  q50={pinball(pred_q50_in, realized_in, 0.5):.4f}  q90={pinball(pred_q90_in, realized_in, 0.9):.4f}")
print(f"    OOS: q10={pinball(pred_q10_ho, realized_ho, 0.1):.4f}  q50={pinball(pred_q50_ho, realized_ho, 0.5):.4f}  q90={pinball(pred_q90_ho, realized_ho, 0.9):.4f}")

# Save all output
df[["ensemble_score","y_60","fwd_60d_max_dd","dd_prob_iso","dd_prob_platt","dd_q10","dd_q50","dd_q90","percentile","position"]].to_csv(OUT/"calibration_output.csv")
print(f"\nSaved {OUT/'calibration_output.csv'}")

# ─── Step 4: Diagnostic plots ─────────────────────────────────────────────────
print("\nGenerating diagnostic plots...")

# 4a: Reliability curve (calibration plot)
fig, axes = plt.subplots(1, 2, figsize=(13, 5))

for ax, name, col in [(axes[0], "Isotonic", "dd_prob_iso"), (axes[1], "Platt", "dd_prob_platt")]:
    for label, mask, color in [("In-sample", in_mask, "steelblue"), ("Hold-out", ho_mask, "indianred")]:
        p = df.loc[mask, col].values
        y = df.loc[mask, "y_60"].astype(int).values
        if len(p) < 10: continue
        bins = np.quantile(p, np.linspace(0, 1, 11))
        bins[0] = -np.inf; bins[-1] = np.inf
        bin_mids, bin_obs, bin_n = [], [], []
        for i in range(10):
            m = (p >= bins[i]) & (p < bins[i+1])
            if m.sum() < 5: continue
            bin_mids.append(p[m].mean())
            bin_obs.append(y[m].mean())
            bin_n.append(m.sum())
        ax.scatter(bin_mids, bin_obs, s=[max(20,n/5) for n in bin_n], color=color, alpha=0.7, label=f"{label} (n={len(p)})")
        ax.plot(bin_mids, bin_obs, color=color, alpha=0.4, linewidth=0.8)
    ax.plot([0,1],[0,1], "k--", linewidth=0.7, alpha=0.5, label="perfect calibration")
    ax.set_xlabel("Predicted P(drawdown ≥20% in 60d)")
    ax.set_ylabel("Realized fraction with drawdown")
    ax.set_title(f"Reliability — {name}")
    ax.legend(fontsize=9, loc="upper left")
    ax.grid(True, alpha=0.3)
    ax.set_xlim(-0.02, 1.02); ax.set_ylim(-0.02, 1.02)
plt.tight_layout()
plt.savefig(OUT/"reliability_curves.png", dpi=120, bbox_inches="tight", facecolor="white")
plt.close()
print(f"  Saved {OUT/'reliability_curves.png'}")

# 4b: Predicted vs realized magnitude scatter, with quantile bands
fig, axes = plt.subplots(1, 2, figsize=(14, 5.5))
for ax, label, mask in [(axes[0], "In-sample", in_mask & df["fwd_60d_max_dd"].notna() & df["dd_q50"].notna()),
                         (axes[1], "Hold-out", ho_dd_mask)]:
    if mask.sum() < 10: continue
    es = df.loc[mask, "ensemble_score"].values
    realized = df.loc[mask, "fwd_60d_max_dd"].values
    q10 = df.loc[mask, "dd_q10"].values
    q50 = df.loc[mask, "dd_q50"].values
    q90 = df.loc[mask, "dd_q90"].values
    
    # Sort by ensemble_score for cleaner plotting
    order = np.argsort(es)
    es_s, realized_s = es[order], realized[order]
    q10_s, q50_s, q90_s = q10[order], q50[order], q90[order]
    
    ax.scatter(es_s, realized_s, s=10, alpha=0.4, color="black", label="realized")
    ax.plot(es_s, q50_s, color="blue", linewidth=1.8, label="predicted q50")
    ax.fill_between(es_s, q10_s, q90_s, color="blue", alpha=0.2, label="(q10, q90) band")
    ax.axhline(0, color="gray", linewidth=0.5, alpha=0.5)
    ax.axhline(-0.20, color="red", linewidth=0.6, alpha=0.6, linestyle="--", label="−20% threshold")
    ax.set_xlabel("ensemble_score")
    ax.set_ylabel("fwd_60d_max_dd")
    sp, _ = spearmanr(q50_s, realized_s)
    ax.set_title(f"Magnitude — {label}  (Spearman={sp:+.3f}, n={mask.sum()})")
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(OUT/"magnitude_scatter.png", dpi=120, bbox_inches="tight", facecolor="white")
plt.close()
print(f"  Saved {OUT/'magnitude_scatter.png'}")

# 4c: Time series of all 3 outputs over the hold-out year
fig, axes = plt.subplots(3, 1, figsize=(13, 9), sharex=True,
                          gridspec_kw={"height_ratios":[1.5,1.5,1.5]})
ho_window = df.loc[HOLDOUT_START:].copy()

# Position (existing)
ax = axes[0]
ax.fill_between(ho_window.index, 0, ho_window["position"], color="steelblue", alpha=0.6)
ax.plot(ho_window.index, ho_window["position"], color="darkblue", linewidth=1)
ax.set_ylabel("position", fontsize=10); ax.set_ylim(-0.05, 1.05)
ax.grid(True, alpha=0.3)
ax.set_title("Hold-out year — position, drawdown probability, drawdown magnitude band", fontsize=12, fontweight="bold")

# Drawdown probability (new)
ax = axes[1]
ax.plot(ho_window.index, ho_window["dd_prob_iso"], color="darkred", linewidth=1.4, label="isotonic")
ax.plot(ho_window.index, ho_window["dd_prob_platt"], color="purple", linewidth=1.0, alpha=0.7, label="Platt")
ax.axhline(base_rate_in, color="gray", linestyle="--", linewidth=0.7, alpha=0.7, label=f"in-sample base rate ({base_rate_in:.2f})")
ax.set_ylabel("P(drawdown ≥20%)", fontsize=10); ax.set_ylim(-0.02, 1.02)
ax.legend(loc="upper left", fontsize=8)
ax.grid(True, alpha=0.3)

# Drawdown magnitude band (new)
ax = axes[2]
ax.fill_between(ho_window.index, ho_window["dd_q10"], ho_window["dd_q90"],
                color="orange", alpha=0.25, label="(q10, q90) interval")
ax.plot(ho_window.index, ho_window["dd_q50"], color="darkorange", linewidth=1.4, label="q50 (median)")
ax.plot(ho_window.index, ho_window["fwd_60d_max_dd"], color="black", linewidth=1.0, alpha=0.6, label="realized")
ax.axhline(0, color="gray", linewidth=0.5)
ax.axhline(-0.20, color="red", linestyle="--", linewidth=0.7, alpha=0.6, label="−20% threshold")
ax.set_ylabel("fwd_60d_max_dd", fontsize=10)
ax.legend(loc="lower left", fontsize=8)
ax.grid(True, alpha=0.3)
axes[-1].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.tight_layout()
plt.savefig(OUT/"holdout_timeseries.png", dpi=120, bbox_inches="tight", facecolor="white")
plt.close()
print(f"  Saved {OUT/'holdout_timeseries.png'}")

print("\nDONE.")
