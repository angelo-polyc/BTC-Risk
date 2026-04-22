"""Experiment 2: shrinkage of AUC-excess weights.

Replace max(AUC - 0.5, 0.01) with:
   shrunk_excess = max(AUC - 0.5, 0) * n / (n + k)

where n = number of calibration observations and k = shrinkage constant.

At k=0 this is the current formula minus the 0.01 floor.
As k → ∞ weights converge toward uniform (1/N).

Apply ONLY at sub-signal layer inside each hypothesis. Leave ensemble layer
alone. Compute new sub-signal weights → new composites → feed into production
ensemble walk-forward → compare results.

EXPERIMENT-ONLY: does not modify committed common.py or build_*.py.
"""
import sys; sys.path.insert(0, '/mnt/project')
from common import auc_excess_weights, apply_flips, compute_auc
import pandas as pd, numpy as np

m = pd.read_csv('/mnt/project/master_daily_view_wf365.csv')
m['date'] = pd.to_datetime(m['date']); m = m.set_index('date').sort_index()
wh = pd.read_csv('/mnt/project/weight_history_wf365_y_60.csv'); wh['fit_date'] = pd.to_datetime(wh.fit_date)
last_date = m.index.max()
hold_start = last_date - pd.Timedelta(days=365)

HYPS = ['macro_equities','cme','crypto_derivatives','classic_cycle','etf_flows']
PINNED = {'macro_equities':{'spx_overext_rank','vix_z90_rank','fed_funds_stress_rank','hy_spread_roc_rank'},
          'crypto_derivatives':{'funding_zscore_rank','lev_stress_rank','rv21_zscore_rank'},
          'classic_cycle':{'golden_ratio','bmo','ahr999','fear_greed'},'cme':set(),'etf_flows':set()}
MIN_CALIB = {'macro_equities':pd.Timestamp('2018-10-01'),'cme':pd.Timestamp('2021-06-30'),
             'crypto_derivatives':pd.Timestamp('2021-06-30'),'classic_cycle':pd.Timestamp('2021-06-30'),
             'etf_flows':pd.Timestamp('2024-01-11')}

hyp_dfs = {}
for hyp in HYPS:
    h = pd.read_csv(f'/mnt/project/hypothesis_{hyp}.csv'); h['date']=pd.to_datetime(h['date']); h=h.set_index('date')
    hyp_dfs[hyp] = h

def shrunk_weights(signals_df, label, k, no_flip=None, min_excess=0.0):
    """Shrinkage version of auc_excess_weights.
    
    excess[col] = max(AUC - 0.5, min_excess) * n / (n + k)
                  where n = number of valid (signal, label) pairs.
    
    Normalization: weights sum to 1. Flips follow standard no_flip convention.
    """
    no_flip = no_flip or set()
    flips = {}
    aucs = {}
    ns = {}
    for col in signals_df.columns:
        d = pd.DataFrame({'y':label, 's':signals_df[col]}).dropna()
        n = len(d)
        ns[col] = n
        if n < 50 or d['y'].nunique() < 2:
            flips[col] = False
            aucs[col] = float('nan')
            continue
        from sklearn.metrics import roc_auc_score
        raw = float(roc_auc_score(d['y'], d['s']))
        if col in no_flip:
            flips[col] = False; aucs[col] = raw
        elif raw < 0.5:
            flips[col] = True
            # Recompute AUC on flipped
            aucs[col] = float(roc_auc_score(d['y'], 1.0 - d['s']))
        else:
            flips[col] = False; aucs[col] = raw
    excess = {}
    for col, a in aucs.items():
        if np.isnan(a):
            excess[col] = 0.0
        else:
            base = max(a - 0.5, min_excess)  # min_excess=0 by default (no floor)
            shrink_factor = ns[col] / (ns[col] + k)
            excess[col] = base * shrink_factor
    tot = sum(excess.values())
    if tot <= 0:
        weights = {c: 1/len(signals_df.columns) for c in signals_df.columns}  # fallback: uniform
    else:
        weights = {c: excess[c]/tot for c in signals_df.columns}
    return weights, flips, aucs


def composite_from_weights(df, weights, flips):
    oriented = apply_flips(df, flips)
    cols = list(weights.keys())
    W = np.array([weights[c] for c in cols])
    X = oriented[cols].values
    mk = ~np.isnan(X)
    den = (mk*W).sum(axis=1); num = np.nansum(np.where(mk, X, 0.0)*W, axis=1)
    return pd.Series(np.where(den>0, num/den, np.nan), index=oriented.index)


# Test range of k values
k_values = [0, 50, 100, 200, 500, 1000, 2000, 5000]

def build_composites(k, return_weights=False):
    """Build all 5 hypothesis composites under shrinkage k."""
    comps = {}
    all_weights = {}
    for hyp in HYPS:
        h = hyp_dfs[hyp]
        sub_cols = [c for c in h.columns if c.startswith('sub_')]
        raw = h[sub_cols].rename(columns={c:c.replace('sub_','') for c in sub_cols})
        calib = MIN_CALIB[hyp]
        y = m['y_60'].reindex(raw.index)
        mask = (raw.index >= calib) & (raw.index < hold_start) & y.notna()
        sig_calib = raw.loc[mask]
        y_calib = y.loc[mask].astype(float)
        w, flips, aucs = shrunk_weights(sig_calib, y_calib, k, no_flip=PINNED[hyp])
        all_weights[hyp] = {'w':w, 'flips':flips, 'aucs':aucs}
        comps[hyp] = composite_from_weights(raw, w, flips)
    if return_weights: return comps, all_weights
    return comps


# Verify k=0 approximately reproduces production (without the 0.01 floor)
comps_k0, weights_k0 = build_composites(0, return_weights=True)

# Compare k=0 composite to committed composite
print("═══ Sanity: shrinkage k=0 (no floor) vs production ═══")
for hyp in HYPS:
    prod = m[f'{hyp}_score']
    shrunk = comps_k0[hyp]
    common = prod.dropna().index.intersection(shrunk.dropna().index)
    diff = (prod.loc[common] - shrunk.loc[common]).abs()
    print(f"  {hyp:22s}  mean|Δ| {diff.mean():.4f}  max|Δ| {diff.max():.4f}  n={len(common)}")

# With floor removed, small differences expected for signals at the floor. Confirm.
print(f"\n  Weights at k=0 for macro (compare to production — production has 0.01 floor):")
for c in sorted(weights_k0['macro_equities']['w'], key=lambda x: -weights_k0['macro_equities']['w'][x]):
    print(f"    {c:30s} AUC {weights_k0['macro_equities']['aucs'][c]:.4f}  weight {weights_k0['macro_equities']['w'][c]:7.2%}")

# Now compute ensemble for each k and evaluate
regimes = m['regime'].astype(str)
fit_dates = sorted(wh.fit_date.unique())
w_lookup = {(fd,rg): dict(zip(g.hypothesis, g.weight)) for (fd,rg),g in wh.groupby(['fit_date','regime'])}
first_fit = fit_dates[0]

def ens_from_comps(comp_dict):
    all_days = m.index
    comp = pd.DataFrame(index=all_days)
    for h in HYPS: comp[h] = comp_dict[h].reindex(all_days)
    e = pd.Series(index=all_days, dtype=float)
    for d in all_days:
        rg = regimes.loc[d] if d in regimes.index else 'neutral'
        if d < first_fit: W = w_lookup[(first_fit, rg)]
        else:
            idx = np.searchsorted(fit_dates, d, side='right')-1
            W = w_lookup[(fit_dates[idx], rg)]
        num, den = 0.0, 0.0
        for h in HYPS:
            v = comp.loc[d, h]
            if not pd.isna(v): num += W[h]*v; den += W[h]
        e.loc[d] = num/den if den > 0 else np.nan
    return e

def pct_rank(s): return s.rolling(365, min_periods=180).rank(pct=True)
def pos_fn(p, lt=0.55, dt=0.70):
    return pd.Series(np.where(p<=lt,1.0,np.where(p>=dt,0.0,1.0-(p-lt)/(dt-lt))), index=p.index)

btc_ret = m['btc_return']
fit_start = pd.Timestamp('2021-06-30')

print(f"\n═══ Shrinkage sweep — full window {fit_start.date()} → {last_date.date()} ═══")
print(f"{'k':>6s}  {'full_Sh365':>11s}  {'full_total':>11s}  {'full_MaxDD':>11s}  {'hold_Sh365':>11s}  {'hold_total':>11s}  {'full_AUC':>9s}  {'hold_AUC':>9s}")
print('-'*110)

results = []
for k in k_values:
    comps_k = build_composites(k)
    e_k = ens_from_comps(comps_k)
    pct_k = pct_rank(e_k)
    pos_k = pos_fn(pct_k)
    strat_k = pos_k.shift(1) * btc_ret
    # Full
    x = strat_k.loc[fit_start:].dropna()
    sh = x.mean()/x.std() * np.sqrt(365) if x.std()>0 else float('nan')
    cum = (1+x).cumprod()
    tot = cum.iloc[-1] - 1
    dd = (cum/cum.cummax()-1).min()
    # Hold
    xh = strat_k.loc[hold_start:].dropna()
    sh_h = xh.mean()/xh.std() * np.sqrt(365) if xh.std()>0 else float('nan')
    cum_h = (1+xh).cumprod()
    tot_h = cum_h.iloc[-1] - 1
    # AUCs
    y_full = m['y_60'].loc[fit_start:last_date]
    y_hold = m['y_60'].loc[hold_start:]
    auc_full = compute_auc(y_full, e_k.loc[fit_start:last_date])
    auc_hold = compute_auc(y_hold, e_k.loc[hold_start:])
    print(f"  {k:>4d}  {sh:>11.3f}  {tot:>11.1%}  {dd:>11.1%}  {sh_h:>11.3f}  {tot_h:>11.1%}  {auc_full:>9.3f}  {auc_hold:>9.3f}")
    results.append({'k':k, 'full_sharpe_365':sh, 'full_total':tot, 'full_maxdd':dd,
                    'hold_sharpe_365':sh_h, 'hold_total':tot_h, 'full_auc':auc_full, 'hold_auc':auc_hold})

pd.DataFrame(results).to_csv('/home/claude/experiments/shrinkage_results.csv', index=False)

# Compare production (k=0 with 0.01 floor) separately using committed ensemble
print(f"\n  For reference — production canonical (k=0 with 0.01 floor): Sharpe 1.201 full / 1.191 hold-out")

# Per-year for best k
best_k = max(results, key=lambda r: r['hold_sharpe_365'])['k']
print(f"\n═══ Per-year comparison: production vs k={best_k} ═══")
prod_strat = pos_fn(pct_rank(m['ensemble_score'])).shift(1) * btc_ret
best_comps = build_composites(best_k)
best_ens = ens_from_comps(best_comps)
best_strat = pos_fn(pct_rank(best_ens)).shift(1) * btc_ret
print(f"{'year':>6s}  {'prod_Sh':>9s}  {'k='+str(best_k)+'_Sh':>9s}  {'Δ':>7s}    {'prod_ret':>9s}  {'k='+str(best_k)+'_ret':>9s}  {'Δret':>6s}")
for year in [2021,2022,2023,2024,2025,2026]:
    ps = prod_strat[prod_strat.index.year==year].dropna()
    bs = best_strat[best_strat.index.year==year].dropna()
    if len(ps) < 30: continue
    psh = ps.mean()/ps.std() * np.sqrt(365) if ps.std()>0 else float('nan')
    bsh = bs.mean()/bs.std() * np.sqrt(365) if bs.std()>0 else float('nan')
    pt = (1+ps).prod() - 1
    bt = (1+bs).prod() - 1
    print(f"  {year:>6d}  {psh:>9.3f}  {bsh:>9.3f}  {bsh-psh:>+7.3f}    {pt:>+8.1%}  {bt:>+8.1%}  {(bt-pt)*100:>+5.1f}pp")

# Weight inspection: how do weights change across k for a single hypothesis?
print(f"\n═══ Weight shrinkage across k values (macro_equities) ═══")
print(f"{'signal':32s}  " + "  ".join(f"k={k:<5d}" for k in k_values))
for sig in sorted(weights_k0['macro_equities']['w']):
    weights_across_k = []
    for k in k_values:
        _, w_k = build_composites(k, return_weights=True)
        weights_across_k.append(w_k['macro_equities']['w'][sig])
    print(f"  {sig:30s}  " + "  ".join(f"{w:>6.3f}" for w in weights_across_k))
