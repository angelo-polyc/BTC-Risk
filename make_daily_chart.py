"""Generate the daily comms chart for wf365.

Usage (from project directory):
    python3 make_daily_chart.py
    python3 make_daily_chart.py --master master_daily_view_wf365.csv \\
                                --raw raw_data_export.csv \\
                                --out today.png

Output: today.png in current directory (or path specified by --out)
Layout: BTC price + position band, ensemble percentile, hypothesis composites
Window: last 365 days
"""
import argparse
import os
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.colors import LinearSegmentedColormap
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--master", default="master_daily_view_wf365.csv",
                help="path to master_daily_view_wf365.csv")
ap.add_argument("--raw", default="raw_data_export.csv",
                help="path to raw_data_export.csv (for BTC close prices)")
ap.add_argument("--out", default="today.png", help="output PNG path")
args = ap.parse_args()

# ─── Load data ───────────────────────────────────────────────────────────────
master = pd.read_csv(args.master, parse_dates=["date"]).set_index("date")
raw = pd.read_csv(args.raw,
                  usecols=["date", "price__btc_ohlc__close"],
                  parse_dates=["date"]).dropna().set_index("date")
raw.columns = ["btc_close"]
master = master.join(raw, how="left")

# Last 365 days
last_date = master.index.max()
window_start = last_date - pd.Timedelta(days=365)
df = master.loc[window_start:].copy()
today = df.iloc[-1]

# ─── Style ────────────────────────────────────────────────────────────────────
plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.edgecolor": "#333",
    "axes.linewidth": 0.8,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linewidth": 0.5,
})

# Hypothesis colors — fixed assignment
HYP_COLORS = {
    "macro_equities":     "#1f77b4",
    "cme":                "#ff7f0e",
    "crypto_derivatives": "#2ca02c",
    "classic_cycle":      "#d62728",
    "etf_flows":          "#9467bd",
    "eth":                "#8c564b",
}
HYP_COLS = [(name, f"{name}_score") for name in HYP_COLORS]

# Position colormap: red (0) → yellow (0.5) → green (1)
pos_cmap = LinearSegmentedColormap.from_list(
    "pos_cmap", [(0.0, "#c62828"), (0.5, "#fbc02d"), (1.0, "#2e7d32")]
)

# ─── Figure ───────────────────────────────────────────────────────────────────
fig = plt.figure(figsize=(11, 7.5), dpi=120)
gs = fig.add_gridspec(
    nrows=4, ncols=1,
    height_ratios=[3.6, 0.45, 1.6, 1.9],
    hspace=0.18,
    left=0.07, right=0.93, top=0.93, bottom=0.07,
)
ax_price = fig.add_subplot(gs[0])
ax_posband = fig.add_subplot(gs[1], sharex=ax_price)
ax_pct = fig.add_subplot(gs[2], sharex=ax_price)
ax_hyp = fig.add_subplot(gs[3], sharex=ax_price)

# ─── Panel 1: BTC price (log) ────────────────────────────────────────────────
ax_price.plot(df.index, df["btc_close"], color="black", linewidth=1.4)
ax_price.set_yscale("log")
ax_price.set_ylabel("BTC (USD, log)", fontsize=10)
# Today vertical line
ax_price.axvline(last_date, color="#444", linestyle="--", linewidth=0.8, alpha=0.6)
# Right-edge label for today's price
ax_price.text(
    1.005, today["btc_close"], f"${today['btc_close']:,.0f}",
    transform=ax_price.get_yaxis_transform(),
    va="center", ha="left", fontsize=9, color="black", fontweight="bold",
)
ax_price.set_title(
    f"wf365 — {last_date.strftime('%Y-%m-%d')}   |   "
    f"position {today['position']:.2f}   |   regime {today['regime']}   |   "
    f"percentile {today['percentile']:.2f}",
    fontsize=11.5, fontweight="bold", loc="left", pad=10,
)
plt.setp(ax_price.get_xticklabels(), visible=False)

# ─── Panel 1b: Position color band ───────────────────────────────────────────
pos_values = df["position"].ffill().values
n = len(pos_values)
# Build pcolormesh strip — use imshow which is simpler for a 1xN strip
import matplotlib.dates as mdates_local
x_nums = mdates_local.date2num(df.index.to_pydatetime())
ax_posband.imshow(
    pos_values.reshape(1, -1),
    aspect="auto",
    cmap=pos_cmap, vmin=0, vmax=1,
    extent=[x_nums[0], x_nums[-1], 0, 1],
    interpolation="nearest",
)
ax_posband.set_yticks([])
ax_posband.set_ylabel("position", fontsize=9, rotation=0, ha="right", va="center", labelpad=18)
ax_posband.grid(False)
ax_posband.axvline(last_date, color="#fff", linestyle="--", linewidth=0.8, alpha=0.7)
plt.setp(ax_posband.get_xticklabels(), visible=False)
# Today's position value at right
ax_posband.text(
    1.005, 0.5, f"{today['position']:.2f}",
    transform=ax_posband.transAxes, va="center", ha="left", fontsize=9, fontweight="bold",
)

# ─── Panel 2: Ensemble percentile ────────────────────────────────────────────
# Thresholds recalibrated 2026-04-15 (v8): wf365 uses (0.55, 0.70).
# Read from env so the chart stays in sync with the deployed position function.
LONG_THR = float(os.environ.get("POSITION_LONG_THR", 0.55))
DEF_THR  = float(os.environ.get("POSITION_DEF_THR",  0.70))
ax_pct.plot(df.index, df["percentile"], color="#1a237e", linewidth=1.2)
ax_pct.axhline(LONG_THR, color="#2e7d32", linestyle="--", linewidth=0.7, alpha=0.7)
ax_pct.axhline(DEF_THR,  color="#c62828", linestyle="--", linewidth=0.7, alpha=0.7)
ax_pct.axhspan(DEF_THR, 1.05, color="#c62828", alpha=0.06)
ax_pct.axhspan(-0.05, LONG_THR, color="#2e7d32", alpha=0.06)
ax_pct.set_ylabel("percentile", fontsize=9)
ax_pct.set_ylim(-0.02, 1.02)
ax_pct.set_yticks([0, LONG_THR, DEF_THR, 1.0])
ax_pct.axvline(last_date, color="#444", linestyle="--", linewidth=0.8, alpha=0.6)
plt.setp(ax_pct.get_xticklabels(), visible=False)
# Today's value at right
ax_pct.text(
    1.005, today["percentile"], f"{today['percentile']:.2f}",
    transform=ax_pct.get_yaxis_transform(),
    va="center", ha="left", fontsize=9, fontweight="bold",
)
# Tiny labels for thresholds at right edge
ax_pct.text(1.005, LONG_THR, "long", transform=ax_pct.get_yaxis_transform(),
            va="center", ha="left", fontsize=7.5, color="#2e7d32", alpha=0.8)
ax_pct.text(1.005, DEF_THR, "defense", transform=ax_pct.get_yaxis_transform(),
            va="center", ha="left", fontsize=7.5, color="#c62828", alpha=0.8)

# ─── Panel 3: Hypothesis composites ──────────────────────────────────────────
for name, col in HYP_COLS:
    ax_hyp.plot(df.index, df[col], color=HYP_COLORS[name],
                linewidth=1.0, alpha=0.85, label=name)

# Stagger right-edge labels so they don't overlap
today_vals = [(name, today[col]) for name, col in HYP_COLS if pd.notna(today[col])]
today_vals.sort(key=lambda t: t[1])  # sort by value
min_spacing = 0.06  # in y-axis units
adjusted = []
last_y = -1.0
for name, val in today_vals:
    placed = max(val, last_y + min_spacing)
    adjusted.append((name, val, placed))
    last_y = placed
for name, val, placed in adjusted:
    # Draw a faint connector line if label was nudged
    if abs(placed - val) > 0.001:
        ax_hyp.plot(
            [1.0, 1.005], [val, placed],
            transform=ax_hyp.get_yaxis_transform(),
            color=HYP_COLORS[name], linewidth=0.5, alpha=0.5,
            clip_on=False,
        )
    ax_hyp.text(
        1.008, placed, f"{val:.2f}",
        transform=ax_hyp.get_yaxis_transform(),
        va="center", ha="left", fontsize=7.5,
        color=HYP_COLORS[name], fontweight="bold",
    )
ax_hyp.axhline(0.5, color="#666", linestyle=":", linewidth=0.6, alpha=0.6)
ax_hyp.set_ylabel("hypothesis", fontsize=9)
ax_hyp.set_ylim(-0.02, 1.02)
ax_hyp.set_yticks([0, 0.5, 1.0])
ax_hyp.legend(loc="upper left", fontsize=7.5, ncol=3, framealpha=0.85,
              handlelength=1.5, columnspacing=1.0)
ax_hyp.axvline(last_date, color="#444", linestyle="--", linewidth=0.8, alpha=0.6)

# ─── X-axis formatting ───────────────────────────────────────────────────────
ax_hyp.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
ax_hyp.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
plt.setp(ax_hyp.get_xticklabels(), rotation=0, ha="center", fontsize=8.5)

# ─── Save ────────────────────────────────────────────────────────────────────
out_path = Path(args.out)
plt.savefig(out_path, dpi=120, bbox_inches="tight", facecolor="white")
plt.close()
print(f"Saved {out_path}")
print(f"Date: {last_date.date()}, position: {today['position']:.3f}, "
      f"regime: {today['regime']}, percentile: {today['percentile']:.3f}")
