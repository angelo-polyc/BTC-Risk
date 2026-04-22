"""Experiment 1: taper sensitivity sweep.

Rerun the canonical pipeline under several taper widths. Baseline at each.
Report full-window + hold-out metrics. No code changes to production.

This is EXPERIMENT-ONLY — does not modify committed build_robust.py or common.py.
"""
import sys; sys.path.insert(0, '/mnt/project')
from common import compute_auc
import pandas as pd, numpy as np

m = pd.read_csv('/mnt/project/master_daily_view_wf365.csv')
m['date'] = pd.to_datetime(m['date']); m = m.set_index('date').sort_index()
last_date = m.index.max()
hold_start = last_date - pd.Timedelta(days=365)
fit_start = pd.Timestamp('2021-06-30')

# Reuse committed ensemble_score — only taper varies
ens = m['ensemble_score']
def pct_rank(s): return s.rolling(365, min_periods=180).rank(pct=True)
pct = pct_rank(ens)

def pos_fn(p, lt, dt):
    return pd.Series(np.where(p<=lt, 1.0,
                     np.where(p>=dt, 0.0, 1.0-(p-lt)/(dt-lt))),
                     index=p.index)

btc_ret = m['btc_return']

tapers = [
    ('production    ', 0.55, 0.70),
    ('narrow        ', 0.58, 0.68),   # tighter than production
    ('slightly wide ', 0.52, 0.73),
    ('medium wide   ', 0.50, 0.75),
    ('wide          ', 0.45, 0.80),
    ('very wide     ', 0.40, 0.85),
    ('extreme wide  ', 0.35, 0.90),
]

def perf(s, a, b):
    x = s.loc[a:b].dropna()
    if len(x) < 30 or x.std() == 0: return np.nan, np.nan, np.nan, np.nan
    sh_252 = x.mean()/x.std() * np.sqrt(252)
    sh_365 = x.mean()/x.std() * np.sqrt(365)
    cum = (1+x).cumprod()
    return sh_365, cum.iloc[-1]-1, (cum/cum.cummax()-1).min(), sh_252

# Per-year + full + hold-out
print("═══ Taper sweep — baseline under different (long_thr, def_thr) ═══")
print("   Ensemble, percentile, and weights UNCHANGED from v13 canonical.")
print(f"   Using committed ensemble_score. Window: {fit_start.date()} → {last_date.date()}")
print()

results = []
print(f"{'taper':18s} {'(lt,dt)':>14s}  {'full_Sh365':>11s}  {'full_total':>11s}  {'full_MaxDD':>11s}  {'hold_Sh365':>11s}  {'hold_total':>11s}  {'hold_MaxDD':>11s}  {'whipsaw':>8s}")
print('-'*136)
for label, lt, dt in tapers:
    pos = pos_fn(pct, lt, dt)
    strat = pos.shift(1) * btc_ret
    sh_f, tot_f, dd_f, _ = perf(strat, fit_start, last_date)
    sh_h, tot_h, dd_h, _ = perf(strat, hold_start, last_date)
    # Whipsaw: mean |Δpos| over hold-out
    whip = pos.loc[hold_start:].diff().abs().mean()
    mark = ' ←prod' if (lt,dt)==(0.55,0.70) else ''
    print(f"  {label} ({lt:.2f},{dt:.2f})  {sh_f:>11.3f}  {tot_f:>11.1%}  {dd_f:>11.1%}  {sh_h:>11.3f}  {tot_h:>11.1%}  {dd_h:>11.1%}  {whip:>8.4f}{mark}")
    results.append({'taper':label.strip(),'lt':lt,'dt':dt,
                    'full_sharpe_365':sh_f,'full_total':tot_f,'full_maxdd':dd_f,
                    'hold_sharpe_365':sh_h,'hold_total':tot_h,'hold_maxdd':dd_h,
                    'whipsaw':whip})

# Per-year breakdown — where does taper matter most?
print(f"\n═══ Per-year Sharpe by taper ═══")
print(f"{'year':>6s}  " + "  ".join(f"{lbl.strip()[:13]:>13s}" for lbl,_,_ in tapers))
for year in [2021,2022,2023,2024,2025,2026]:
    y_start = max(pd.Timestamp(f'{year}-01-01'), fit_start)
    y_end = min(pd.Timestamp(f'{year}-12-31'), last_date)
    if y_start >= y_end: continue
    row = [f"{year:>6d}"]
    for label, lt, dt in tapers:
        pos = pos_fn(pct, lt, dt)
        strat = pos.shift(1) * btc_ret
        x = strat.loc[y_start:y_end].dropna()
        sh = x.mean()/x.std() * np.sqrt(365) if len(x)>30 and x.std()>0 else float('nan')
        row.append(f"{sh:>13.3f}")
    print("  ".join(row))

# Today's position
print(f"\n═══ Today's position ({last_date.date()}) under each taper ═══")
today_pct = pct.loc[last_date]
print(f"  Ensemble: {ens.loc[last_date]:.4f}  Percentile: {today_pct:.4f}  Regime: {m.loc[last_date,'regime']}")
for label, lt, dt in tapers:
    p = pos_fn(pd.Series([today_pct]), lt, dt).iloc[0]
    print(f"  {label} ({lt:.2f},{dt:.2f})  position: {p:.3f}")

pd.DataFrame(results).to_csv('/home/claude/experiments/taper_sweep_results.csv', index=False)
print("\nSaved results → taper_sweep_results.csv")
