"""Corrected shrinkage: interpolate between AUC-excess-based weights and uniform.

Formula:
    raw_excess[col] = max(AUC_post_flip - 0.5, 0)
    shrunk_weight[col] = (1-α) * raw_excess / sum(raw_excess)  +  α * (1/N)

α ∈ [0, 1]. α=0 reproduces current AUC-excess normalization (minus the 0.01 floor).
α=1 gives uniform weights (1/N each). α=0.5 is 50/50 mix.

This is a proper shrinkage: it pulls weights toward the uniform prior based on one
parameter α, regardless of calibration size.
"""
import sys; sys.path.insert(0, '/mnt/project')
from common import auc_excess_weights, apply_flips, compute_auc
import pandas as pd, numpy as np
from sklearn.metrics import roc_auc_score

m = pd.read_csv('/mnt/project/master_daily_view_wf365.csv')
m['date'] = pd.to_datetime(m['date']); m = m.set_index('date').sort_index()
wh = pd.read_csv('/mnt/project/weight_history_wf365_y_60.csv'); wh['fit_date'] = pd.to_datetime(wh.fit_date)
last_date = m.index.max()
hold_start = last_date - pd.Timedelta(days=365)
fit_start = pd.Timestamp('2021-06-30')

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

def shrunk_weights(signals_df, label, alpha, no_flip=None):
    """Shrunk weights: alpha * uniform + (1-alpha) * auc_excess_normalized."""
    no_flip = no_flip or set()
    flips, aucs = {}, {}
    for col in signals_df.columns:
        d = pd.DataFrame({'y':label, 's':signals_df[col]}).dropna()
        if len(d) < 50 or d['y'].nunique() < 2:
            flips[col] = False; aucs[col] = float('nan'); continue
        raw = float(roc_auc_score(d['y'], d['s']))
        if col in no_flip:
            flips[col] = False; aucs[col] = raw
        elif raw < 0.5:
            flips[col] = True
            aucs[col] = float(roc_auc_score(d['y'], 1.0 - d['s']))
        else:
            flips[col] = False; aucs[col] = raw
    # Raw excesses (no floor; use 0 as lower bound)
    excess = {c: (0.0 if np.isnan(a) else max(a - 0.5, 0.0)) for c,a in aucs.items()}
    tot = sum(excess.values())
    N = len(signals_df.columns)
    if tot <= 0:
        excess_norm = {c: 1/N for c in signals_df.columns}
    else:
        excess_norm = {c: excess[c]/tot for c in signals_df.columns}
    # Shrink toward uniform
    weights = {c: alpha * (1/N) + (1-alpha) * excess_norm[c] for c in signals_df.columns}
    return weights, flips, aucs


def composite_from_weights(df, weights, flips):
    oriented = apply_flips(df, flips)
    cols = list(weights.keys())
    W = np.array([weights[c] for c in cols])
    X = oriented[cols].values
    mk = ~np.isnan(X)
    with np.errstate(invalid='ignore'):
        den = (mk*W).sum(axis=1)
        num = np.nansum(np.where(mk, X, 0.0)*W, axis=1)
        out = np.where(den>0, num/den, np.nan)
    return pd.Series(out, index=oriented.index)


def build_composites(alpha, return_weights=False):
    comps = {}
    all_w = {}
    for hyp in HYPS:
        h = hyp_dfs[hyp]
        sub_cols = [c for c in h.columns if c.startswith('sub_')]
        raw = h[sub_cols].rename(columns={c:c.replace('sub_','') for c in sub_cols})
        calib = MIN_CALIB[hyp]
        y = m['y_60'].reindex(raw.index)
        mask = (raw.index >= calib) & (raw.index < hold_start) & y.notna()
        sig_calib = raw.loc[mask]
        y_calib = y.loc[mask].astype(float)
        w, flips, aucs = shrunk_weights(sig_calib, y_calib, alpha, no_flip=PINNED[hyp])
        all_w[hyp] = {'w':w, 'flips':flips, 'aucs':aucs}
        comps[hyp] = composite_from_weights(raw, w, flips)
    return (comps, all_w) if return_weights else comps


# Ensemble machinery
regimes = m['regime'].astype(str)
fit_dates = sorted(wh.fit_date.unique())
w_lookup = {(fd,rg): dict(zip(g.hypothesis, g.weight)) for (fd,rg),g in wh.groupby(['fit_date','regime'])}
first_fit = fit_dates[0]

def ens_from_comps(cd):
    all_days = m.index
    comp = pd.DataFrame(index=all_days)
    for h in HYPS: comp[h] = cd[h].reindex(all_days)
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
alphas = [0.0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 0.70, 1.00]

print(f"═══ Shrinkage v2: α * uniform + (1-α) * excess ═══")
print(f"Production reference: Sharpe 1.201 full / 1.191 hold-out (committed canonical)\n")
print(f"{'α':>5s}  {'full_Sh':>8s}  {'full_tot':>9s}  {'full_DD':>8s}  {'hold_Sh':>8s}  {'hold_tot':>9s}  {'full_AUC':>9s}  {'hold_AUC':>9s}")
print('-'*95)
results = []
for a in alphas:
    cd = build_composites(a)
    e = ens_from_comps(cd)
    pct = pct_rank(e); pos = pos_fn(pct)
    strat = pos.shift(1) * btc_ret
    x = strat.loc[fit_start:].dropna()
    sh = x.mean()/x.std()*np.sqrt(365); cum=(1+x).cumprod()
    tot = cum.iloc[-1]-1; dd = (cum/cum.cummax()-1).min()
    xh = strat.loc[hold_start:].dropna()
    sh_h = xh.mean()/xh.std()*np.sqrt(365); tot_h=(1+xh).cumprod().iloc[-1]-1
    y_full = m['y_60'].loc[fit_start:last_date]; y_hold = m['y_60'].loc[hold_start:]
    auc_f = compute_auc(y_full, e.loc[fit_start:last_date])
    auc_h = compute_auc(y_hold, e.loc[hold_start:])
    mark = ''
    if a == 0: mark = ' (~=prod minus floor)'
    if a == 1: mark = ' (uniform weights)'
    print(f"  {a:>4.2f}  {sh:>8.3f}  {tot:>9.1%}  {dd:>8.1%}  {sh_h:>8.3f}  {tot_h:>9.1%}  {auc_f:>9.3f}  {auc_h:>9.3f}{mark}")
    results.append({'alpha':a, 'full_sharpe':sh, 'full_total':tot, 'full_maxdd':dd,
                    'hold_sharpe':sh_h, 'hold_total':tot_h, 'full_auc':auc_f, 'hold_auc':auc_h})

pd.DataFrame(results).to_csv('/home/claude/experiments/shrinkage_v2_results.csv', index=False)

# Weight evolution
print(f"\n═══ Weight shrinkage across α (macro_equities) ═══")
print(f"{'signal':30s}  " + "  ".join(f"α={a:<4.2f}" for a in alphas))
_, w0 = build_composites(0.0, return_weights=True)
for sig in sorted(w0['macro_equities']['w']):
    row = [f"{sig:28s}"]
    for a in alphas:
        _, w_a = build_composites(a, return_weights=True)
        row.append(f"{w_a['macro_equities']['w'][sig]:>6.3f}")
    print("  " + "  ".join(row))
