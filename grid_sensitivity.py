"""Grid-scan (long_thr, def_thr) space to characterize the performance/sensitivity tradeoff.

Trick to keep this fast: percentile column doesn't depend on position thresholds, so we
run build_robust.py just 2x (once per input scenario), then apply different position_fns
to the same percentile column. 24 grid points × 2 scenarios → 48 Sharpe computations,
but only 2 pipeline runs.
"""
import subprocess, os, shutil
from pathlib import Path
import pandas as pd
import numpy as np

ROOT = Path("/home/claude/btc_model")
FINAL = ROOT / "data/final"
HYP = ROOT / "data/hypotheses"

STANDALONE = ROOT / "data/hyp_standalone"
MASTER_HYP = ROOT / "data/hyp_from_master"

FIT_START = pd.Timestamp("2021-06-30", tz="UTC")
HOLDOUT   = pd.Timestamp("2025-04-15", tz="UTC")
DATA_END  = pd.Timestamp("2026-04-15", tz="UTC")
# Transaction cost removed 2026-04-17 (see refit_report_v11.md). Strategy
# returns are gross throughout.


def run_once(src: Path, variant="wf365"):
    """Run pipeline once with given input source. Return (df, btc_return_series)."""
    if HYP.exists(): shutil.rmtree(HYP)
    shutil.copytree(src, HYP)
    env = os.environ.copy()
    env.update({"BTC_MODEL_ROOT": str(ROOT), "CALIB_LABEL": "y_60",
                "PYTHONWARNINGS": "ignore", "POSITION_LONG_THR": "0.55"})
    if variant == "wf365":
        env.update({"WALK_FORWARD": "1", "WALK_CADENCE_MONTHS": "1",
                    "WARMUP_MONTHS": "12", "PERCENTILE_WINDOW": "365",
                    "POSITION_DEF_THR": "0.70"})
    else:
        env.update({"WALK_FORWARD": "0", "PERCENTILE_WINDOW": "730",
                    "POSITION_DEF_THR": "0.65"})
        for k in ["WALK_CADENCE_MONTHS","WARMUP_MONTHS"]: env.pop(k, None)
    subprocess.run(["python","build_robust.py"], cwd=ROOT, env=env,
                   capture_output=True, text=True, check=True)
    df = pd.read_parquet(FINAL / "ensemble_robust.parquet")
    return df  # has regime, ensemble_score, percentile, position (ignored), btc_return, strategy_return (ignored)


def position_fn(pct, long_thr, def_thr):
    """Vectorized piecewise-linear position. Same as build_robust.py."""
    out = np.where(pct <= long_thr, 1.0,
          np.where(pct >= def_thr, 0.0,
                   1.0 - (pct - long_thr) / (def_thr - long_thr)))
    return pd.Series(np.where(pd.isna(pct), np.nan, out), index=pct.index)


def compute_metrics(df: pd.DataFrame, long_thr: float, def_thr: float):
    """Apply position function, compute Sharpe/DD/taper-zone%/etc."""
    pos = position_fn(df["percentile"], long_thr, def_thr)
    btc_ret = df["btc_return"].fillna(0)
    # Gross returns only (cost model removed 2026-04-17, see refit_report_v11.md).
    gross = pos.shift(1).fillna(0) * btc_ret
    # Restrict to post-fit window
    mask_full = (df.index >= FIT_START) & (df.index < DATA_END)
    mask_hold = (df.index >= HOLDOUT) & (df.index < DATA_END)
    
    def stats(mask):
        r = gross[mask].dropna()
        if len(r) == 0 or r.std() == 0: return dict(sharpe=np.nan, dd=np.nan, total=np.nan)
        eq = (1 + r).cumprod()
        dd = (eq/eq.cummax() - 1).min()
        return dict(
            sharpe=(r.mean()/r.std()) * np.sqrt(365),
            dd=dd,
            total=eq.iloc[-1] - 1,
        )
    
    full = stats(mask_full)
    hold = stats(mask_hold)
    # Taper-zone % — % of in-window days where percentile is strictly inside (long, def)
    pct = df.loc[mask_full, "percentile"].dropna()
    taper_pct = ((pct > long_thr) & (pct < def_thr)).mean()
    # Average position
    avg_pos = pos[mask_full].mean()
    # Turnover
    tot_turn = turn[mask_full].sum()
    yrs = (df.loc[mask_full].index.max() - df.loc[mask_full].index.min()).days / 365.25
    turnover = tot_turn / yrs if yrs > 0 else np.nan
    return dict(
        sharpe_full=full["sharpe"], dd_full=full["dd"], total_full=full["total"],
        sharpe_hold=hold["sharpe"], dd_hold=hold["dd"], total_hold=hold["total"],
        taper_pct=taper_pct, avg_pos=avg_pos, turnover=turnover,
    )


# ─── Run pipeline twice — one per scenario ─────────────────────────────────
print("Running pipeline for each input scenario (2 runs; percentile is threshold-independent)…")
master_df = run_once(MASTER_HYP, "wf365")
print("  master-embedded: done")
stale_df  = run_once(STANDALONE, "wf365")
print("  standalone-CSV:  done")


# ─── Grid sweep ────────────────────────────────────────────────────────────
LONG_THRS = [0.40, 0.45, 0.50, 0.55]
DEF_THRS  = [0.65, 0.70, 0.75, 0.80, 0.85, 0.90]

rows = []
for lt in LONG_THRS:
    for dt in DEF_THRS:
        if dt <= lt: continue
        m_canon = compute_metrics(master_df, lt, dt)
        m_stale = compute_metrics(stale_df,  lt, dt)
        rows.append({
            "long_thr": lt, "def_thr": dt, "width": dt - lt,
            "sharpe_full_canon": m_canon["sharpe_full"],
            "sharpe_hold_canon": m_canon["sharpe_hold"],
            "dd_full_canon":     m_canon["dd_full"],
            "dd_hold_canon":     m_canon["dd_hold"],
            "taper_pct_canon":   m_canon["taper_pct"],
            "turnover_canon":    m_canon["turnover"],
            "sharpe_full_stale": m_stale["sharpe_full"],
            "sharpe_hold_stale": m_stale["sharpe_hold"],
            # Sensitivity metrics
            "dsharpe_full_abs":  abs(m_canon["sharpe_full"] - m_stale["sharpe_full"]),
            "dsharpe_hold_abs":  abs(m_canon["sharpe_hold"] - m_stale["sharpe_hold"]),
            "dsharpe_full_rel":  abs(m_canon["sharpe_full"] - m_stale["sharpe_full"]) / abs(m_canon["sharpe_full"]) if m_canon["sharpe_full"] else np.nan,
            "dsharpe_hold_rel":  abs(m_canon["sharpe_hold"] - m_stale["sharpe_hold"]) / abs(m_canon["sharpe_hold"]) if m_canon["sharpe_hold"] else np.nan,
        })

grid = pd.DataFrame(rows)
grid.to_csv(ROOT / "grid_sensitivity.csv", index=False)

# ─── Identify Pareto frontier ──────────────────────────────────────────────
# Performance metric: sharpe_full_canon (higher better)
# Sensitivity metric: dsharpe_hold_abs (lower better) — using hold-out because that's deployment-relevant
def pareto_frontier(df, perf_col, sens_col):
    """Points where no other point dominates on both perf (higher) and sens (lower)."""
    pareto = []
    for i, row in df.iterrows():
        dominated = False
        for j, other in df.iterrows():
            if i == j: continue
            if other[perf_col] >= row[perf_col] and other[sens_col] <= row[sens_col] \
               and (other[perf_col] > row[perf_col] or other[sens_col] < row[sens_col]):
                dominated = True
                break
        if not dominated:
            pareto.append(i)
    return df.loc[pareto].sort_values(perf_col)

pareto_hold = pareto_frontier(grid, "sharpe_full_canon", "dsharpe_hold_abs")
pareto_taper = pareto_frontier(grid, "sharpe_full_canon", "taper_pct_canon")

# ─── Report ────────────────────────────────────────────────────────────────
CURRENT = (0.55, 0.70)
print("\n" + "═"*90)
print("PERFORMANCE vs SENSITIVITY — full grid (gross, wf365, y_60)")
print("═"*90)
print(f"{'long':>5s} {'def':>5s} {'width':>6s}  | {'S_full':>7s} {'S_hold':>7s} {'DD_full':>8s}"
      f"  | {'taper%':>7s} {'turn/y':>7s}  | {'|ΔS_full|':>10s} {'|ΔS_hold|':>10s}  notes")
for _, r in grid.iterrows():
    mark = " ← current" if (r["long_thr"], r["def_thr"]) == CURRENT else ""
    print(f"{r['long_thr']:>5.2f} {r['def_thr']:>5.2f} {r['width']:>6.2f}  | "
          f"{r['sharpe_full_canon']:>7.3f} {r['sharpe_hold_canon']:>7.3f} {r['dd_full_canon']:>8.1%}"
          f"  | {r['taper_pct_canon']:>7.1%} {r['turnover_canon']:>7.1f}"
          f"  | {r['dsharpe_full_abs']:>10.3f} {r['dsharpe_hold_abs']:>10.3f}{mark}")

print("\n" + "═"*90)
print("PARETO FRONTIER — (Sharpe full-window, canonical) vs (|ΔSharpe hold-out| sensitivity)")
print("═"*90)
print(f"{'long':>5s} {'def':>5s} {'width':>6s}  {'S_full':>7s} {'S_hold':>7s} {'taper%':>7s} {'|ΔS_hold|':>10s}")
for _, r in pareto_hold.iterrows():
    mark = " ← current" if (r["long_thr"], r["def_thr"]) == CURRENT else ""
    print(f"{r['long_thr']:>5.2f} {r['def_thr']:>5.2f} {r['width']:>6.2f}  "
          f"{r['sharpe_full_canon']:>7.3f} {r['sharpe_hold_canon']:>7.3f} {r['taper_pct_canon']:>7.1%} {r['dsharpe_hold_abs']:>10.3f}{mark}")

print("\n" + "═"*90)
print("PARETO FRONTIER — (Sharpe full, canonical) vs (% days in taper zone)")
print("═"*90)
print(f"{'long':>5s} {'def':>5s} {'width':>6s}  {'S_full':>7s} {'S_hold':>7s} {'taper%':>7s} {'|ΔS_hold|':>10s}")
for _, r in pareto_taper.iterrows():
    mark = " ← current" if (r["long_thr"], r["def_thr"]) == CURRENT else ""
    print(f"{r['long_thr']:>5.2f} {r['def_thr']:>5.2f} {r['width']:>6.2f}  "
          f"{r['sharpe_full_canon']:>7.3f} {r['sharpe_hold_canon']:>7.3f} {r['taper_pct_canon']:>7.1%} {r['dsharpe_hold_abs']:>10.3f}{mark}")

# ─── Simple balance scores ──────────────────────────────────────────────────
# Normalize & rank
grid["rank_perf"]  = grid["sharpe_full_canon"].rank(ascending=False)
grid["rank_sens"]  = grid["dsharpe_hold_abs"].rank(ascending=True)
grid["rank_taper"] = grid["taper_pct_canon"].rank(ascending=True)
grid["rank_avg_perf_sens"]  = (grid["rank_perf"] + grid["rank_sens"]) / 2
grid["rank_avg_perf_taper"] = (grid["rank_perf"] + grid["rank_taper"]) / 2

print("\n" + "═"*90)
print("TOP-5 BALANCE CANDIDATES — equal-weight rank(Sharpe) + rank(|ΔS_hold|)")
print("═"*90)
print(f"{'long':>5s} {'def':>5s} {'width':>6s}  {'S_full':>7s} {'S_hold':>7s} {'taper%':>7s} {'|ΔS_hold|':>10s} {'rank_score':>11s}")
for _, r in grid.nsmallest(5, "rank_avg_perf_sens").iterrows():
    mark = " ← current" if (r["long_thr"], r["def_thr"]) == CURRENT else ""
    print(f"{r['long_thr']:>5.2f} {r['def_thr']:>5.2f} {r['width']:>6.2f}  "
          f"{r['sharpe_full_canon']:>7.3f} {r['sharpe_hold_canon']:>7.3f} {r['taper_pct_canon']:>7.1%} "
          f"{r['dsharpe_hold_abs']:>10.3f} {r['rank_avg_perf_sens']:>11.1f}{mark}")

# ─── Current vs candidates comparison ──────────────────────────────────────
cur = grid[(grid.long_thr == CURRENT[0]) & (grid.def_thr == CURRENT[1])].iloc[0]
print(f"\nCurrent (0.55, 0.70): S_full={cur.sharpe_full_canon:.3f}, S_hold={cur.sharpe_hold_canon:.3f}, "
      f"taper%={cur.taper_pct_canon:.1%}, |ΔS_hold|={cur.dsharpe_hold_abs:.3f}")
print(f"\nGrid saved to: {ROOT/'grid_sensitivity.csv'}")
