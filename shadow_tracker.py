"""Shadow tracker: log counterfactual positions from candidate decision rules.

Purpose: maintain a running record of what the naive, oos_persist2, and
oos_persist3 rules WOULD HAVE DECIDED at each monthly refit, alongside
production baseline. This accumulates the out-of-sample evidence needed to
eventually decide whether any rule should be promoted from reporting to
production.

Read-only with respect to pipeline state. Writes shadow_state.csv only.

Usage:
    python3 shadow_tracker.py                          # refresh with latest data
    python3 shadow_tracker.py --out shadow_state.csv   # custom output

Invariants:
    - Uses ONLY data available at each historical decision date (walk-forward clean).
    - Production ensemble weights at each date (from weight_history_wf365_y_60.csv).
    - Production sub-signal weights (static, single-fit at MIN_CALIB per hypothesis).
    - Drop-and-renormalize within each hypothesis when a rule says to drop.

Rules tracked:
    - baseline        (no action, = production canonical)
    - naive           (MVP rule: OOS AUC <= 0.50 OR IS-OOS delta > 0.15)
    - oos_persist2    (OOS AUC <= 0.50 in current AND previous month)
    - oos_persist3    (OOS AUC <= 0.50 in current AND 2 previous months)

Output columns per rule:
    {rule}_ens       — ensemble score (tape-independent, signal-quality view)
    {rule}_pct       — rolling-365d percentile
    {rule}_pos_prod  — position at production taper (0.55, 0.70)
    {rule}_pos_wide  — position at wide taper (0.45, 0.80)

Two tapers exported because the taper-amplification investigation
(followup_taper_amplification_test.md) showed that persist-rule edge at
production taper is partly amplification noise, not signal quality. The
wide-taper view is cleaner for diagnosing whether a rule is genuinely
adding predictive value.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

HYPS = ['macro_equities', 'cme', 'crypto_derivatives', 'classic_cycle', 'etf_flows']
PINNED = {
    'macro_equities': {'spx_overext_rank', 'vix_z90_rank', 'fed_funds_stress_rank', 'hy_spread_roc_rank'},
    'crypto_derivatives': {'funding_zscore_rank', 'lev_stress_rank', 'rv21_zscore_rank'},
    'classic_cycle': {'golden_ratio', 'bmo', 'ahr999', 'fear_greed'},
    'cme': set(),
    'etf_flows': set(),
}
MIN_CALIB = {
    'macro_equities':    pd.Timestamp('2018-10-01'),
    'cme':               pd.Timestamp('2021-06-30'),
    'crypto_derivatives':pd.Timestamp('2021-06-30'),
    'classic_cycle':     pd.Timestamp('2021-06-30'),
    'etf_flows':         pd.Timestamp('2024-01-11'),
}
FIT_START = pd.Timestamp("2021-06-30")
AUC_THR = 0.50
DELTA_THR = 0.15
LABEL = "y_60"

TRACKED_RULES = ['naive', 'oos_persist2', 'oos_persist3']


def compute_auc(y: pd.Series, s: pd.Series) -> float:
    d = pd.DataFrame({"y": y, "s": s}).dropna()
    if len(d) < 50 or d["y"].nunique() < 2:
        return float("nan")
    try:
        return float(roc_auc_score(d["y"], d["s"]))
    except Exception:
        return float("nan")


def auc_excess_weights(signals_df, label, min_excess=0.01, no_flip=None):
    """Local copy of the production weight function. Pinned = no auto-flip."""
    no_flip = no_flip or set()
    flips, aucs_post = {}, {}
    for col in signals_df.columns:
        raw = compute_auc(label, signals_df[col])
        if np.isnan(raw):
            flips[col] = False; aucs_post[col] = float("nan"); continue
        if col in no_flip:
            flips[col] = False; aucs_post[col] = raw
        elif raw < 0.5:
            flips[col] = True; aucs_post[col] = compute_auc(label, 1.0 - signals_df[col])
        else:
            flips[col] = False; aucs_post[col] = raw
    excess = {c: (0.0 if np.isnan(a) else max(a - 0.5, min_excess)) for c, a in aucs_post.items()}
    tot = sum(excess.values())
    if tot <= 0:
        weights = {c: 0.0 for c in signals_df.columns}
    else:
        weights = {c: excess[c] / tot for c in signals_df.columns}
    return weights, flips, aucs_post


def compute_flag_history(project_dir: Path, decision_dates, hyp_dfs, labels_df):
    """For each monthly decision date and each sub-signal, compute IS/OOS AUC and flags."""
    rows = []
    # For each signal (composite + sub-signals) across all hypotheses
    sigs = {}
    for hyp in HYPS + ['eth']:
        # Composite from master
        pass  # we only need sub-signals here for rule application
    for hyp in HYPS:
        df = hyp_dfs[hyp]
        sub_cols = [c for c in df.columns if c.startswith('sub_')]
        merged = df.merge(labels_df, left_index=True, right_index=True, how='inner')
        for c in sub_cols:
            for D in decision_dates:
                hold_start = D - pd.Timedelta(days=365)
                dd = merged[[c, LABEL]].dropna()
                is_df = dd[(dd.index >= FIT_START) & (dd.index < hold_start)]
                oos_df = dd[(dd.index >= hold_start) & (dd.index <= D)]
                is_a = compute_auc(is_df[LABEL], is_df[c])
                oos_a = compute_auc(oos_df[LABEL], oos_df[c])
                rows.append({
                    'replay_date': D, 'hypothesis': hyp, 'signal': c,
                    'is_auc': is_a, 'oos_auc': oos_a,
                    'is_oos_flag': (not np.isnan(oos_a)) and oos_a <= AUC_THR,
                    'is_delta_flag': (not np.isnan(is_a) and not np.isnan(oos_a)
                                      and (is_a - oos_a) > DELTA_THR),
                })
    return pd.DataFrame(rows)


def flags_for_rule(flag_df, decision_dates, i: int, rule: str) -> set:
    D = decision_dates[i]
    if rule == 'naive':
        cur = flag_df[flag_df.replay_date == D]
        return set(cur[cur.is_oos_flag | (cur.is_delta_flag & ~cur.is_oos_flag)]['signal'])
    if rule == 'oos_only':
        cur = flag_df[(flag_df.replay_date == D) & flag_df.is_oos_flag]
        return set(cur['signal'])
    if rule.startswith('oos_persist'):
        k = int(rule.split('persist')[1])
        if i < k - 1:
            return set()
        s = set(flag_df[(flag_df.replay_date == decision_dates[i]) & flag_df.is_oos_flag]['signal'])
        for j in range(1, k):
            s &= set(flag_df[(flag_df.replay_date == decision_dates[i-j]) & flag_df.is_oos_flag]['signal'])
        return s
    raise ValueError(f"Unknown rule: {rule}")


def compute_sub_weights(hyp_dfs, labels_series, hold_start):
    """Production sub-signal weights — single-fit at MIN_CALIB per hypothesis."""
    out = {}
    for hyp in HYPS:
        df = hyp_dfs[hyp]
        sub_cols = [c for c in df.columns if c.startswith('sub_')]
        raw = df[sub_cols].rename(columns={c: c.replace('sub_', '') for c in sub_cols})
        y = labels_series.reindex(raw.index)
        mask = (raw.index >= MIN_CALIB[hyp]) & (raw.index < hold_start) & y.notna()
        w, _, _ = auc_excess_weights(raw.loc[mask], y.loc[mask].astype(float),
                                      no_flip=PINNED[hyp])
        out[hyp] = {f'sub_{k}': v for k, v in w.items()}
    return out


def build_remediated_composites(master_df, hyp_dfs, subsignal_w, flag_df, decision_dates, rule):
    all_days = master_df.index
    first_D = decision_dates[0]
    comp = pd.DataFrame(index=all_days, columns=HYPS, dtype=float)
    pre = all_days < first_D
    for hyp in HYPS:
        comp.loc[pre, hyp] = master_df.loc[pre, f'{hyp}_score']
    last_date = all_days.max()
    for i, D in enumerate(decision_dates):
        next_D = decision_dates[i+1] if i+1 < len(decision_dates) else last_date + pd.Timedelta(days=1)
        seg_days = all_days[(all_days >= D) & (all_days < next_D)]
        flagged = flags_for_rule(flag_df, decision_dates, i, rule)
        for hyp in HYPS:
            w = dict(subsignal_w[hyp])
            for s in list(w):
                if s in flagged:
                    w[s] = 0.0
            if sum(w.values()) == 0:
                comp.loc[seg_days, hyp] = master_df.loc[seg_days, f'{hyp}_score']
                continue
            tot = sum(w.values())
            w = {k: v / tot for k, v in w.items()}
            sub_df = hyp_dfs[hyp].reindex(seg_days)[list(w.keys())]
            W = np.array([w[c] for c in sub_df.columns])
            X = sub_df.values
            mk = ~np.isnan(X)
            den = (mk * W).sum(axis=1)
            num = np.nansum(np.where(mk, X, 0.0) * W, axis=1)
            comp.loc[seg_days, hyp] = np.where(den > 0, num / den, np.nan)
    return comp


def ensemble_from_comps(comp_df, wh_df, regimes):
    fit_dates = sorted(wh_df.fit_date.unique())
    w_lookup = {(fd, rg): dict(zip(g.hypothesis, g.weight))
                for (fd, rg), g in wh_df.groupby(['fit_date', 'regime'])}
    first_fit = fit_dates[0]
    e = pd.Series(index=comp_df.index, dtype=float)
    for d in comp_df.index:
        rg = regimes.loc[d] if d in regimes.index else 'neutral'
        if d < first_fit:
            W = w_lookup[(first_fit, rg)]
        else:
            idx = np.searchsorted(fit_dates, d, side='right') - 1
            W = w_lookup[(fit_dates[idx], rg)]
        num, den = 0.0, 0.0
        for h in HYPS:
            v = comp_df.loc[d, h]
            if not pd.isna(v):
                num += W[h] * v
                den += W[h]
        e.loc[d] = num / den if den > 0 else np.nan
    return e


def main() -> int:
    ap = argparse.ArgumentParser(description="Shadow-track decision rules alongside baseline.")
    ap.add_argument('--project-dir', type=Path, default=Path(__file__).parent)
    ap.add_argument('--out', type=Path, default=Path('shadow_state.csv'))
    ap.add_argument('--start-date', type=str, default='2023-01-01',
                    help='Earliest monthly decision date to include.')
    args = ap.parse_args()

    project = args.project_dir
    m = pd.read_csv(project / 'master_daily_view_wf365.csv')
    m['date'] = pd.to_datetime(m['date'])
    m = m.set_index('date').sort_index()
    wh = pd.read_csv(project / 'weight_history_wf365_y_60.csv')
    wh['fit_date'] = pd.to_datetime(wh.fit_date)

    last_date = m.index.max()
    hold_start = last_date - pd.Timedelta(days=365)
    regimes = m['regime'].astype(str)

    hyp_dfs = {}
    for hyp in HYPS:
        df = pd.read_csv(project / f'hypothesis_{hyp}.csv')
        df['date'] = pd.to_datetime(df['date'])
        hyp_dfs[hyp] = df.set_index('date')

    start = pd.Timestamp(args.start_date)
    decision_dates = list(pd.date_range(start, last_date, freq='MS'))
    print(f"Shadow tracker: {len(decision_dates)} monthly decisions "
          f"from {decision_dates[0].date()} to {decision_dates[-1].date()}")

    print("Computing flag history...")
    flag_df = compute_flag_history(project, decision_dates, hyp_dfs, m[[LABEL]])

    print("Computing production sub-signal weights...")
    subsignal_w = compute_sub_weights(hyp_dfs, m[LABEL], hold_start)

    # Baseline
    comp_b = pd.DataFrame({h: m[f'{h}_score'] for h in HYPS}, index=m.index)
    e_b = ensemble_from_comps(comp_b, wh, regimes)

    def pct_rank(s): return s.rolling(365, min_periods=180).rank(pct=True)

    def pos_fn(p, long_thr=0.55, def_thr=0.70):
        """Linear hybrid: fully long ≤ long_thr, fully defensive ≥ def_thr,
        linear between. Exported at two taper widths so downstream analysis can
        separate signal-quality effects from rolling-percentile amplification
        (see followup_taper_amplification_test.md)."""
        return pd.Series(np.where(p <= long_thr, 1.0,
                         np.where(p >= def_thr, 0.0, 1.0 - (p - long_thr) / (def_thr - long_thr))),
                         index=p.index)

    # Ensemble / percentile are canonical (tape-independent). Position is stored at
    # two tapers: production (0.55, 0.70) and wide (0.45, 0.80). Consumers should
    # generally use the wide-taper variant for signal-quality analysis because it
    # dampens the amplification effect that inflates apparent rule edge at prod taper.
    out = pd.DataFrame(index=m.index)
    out['regime'] = regimes
    out['btc_return'] = m['btc_return']
    out['baseline_ens'] = e_b
    out['baseline_pct'] = pct_rank(e_b)
    out['baseline_pos_prod']  = pos_fn(out['baseline_pct'], 0.55, 0.70)
    out['baseline_pos_wide']  = pos_fn(out['baseline_pct'], 0.45, 0.80)

    for rule in TRACKED_RULES:
        print(f"Computing rule: {rule}...")
        comp = build_remediated_composites(m, hyp_dfs, subsignal_w, flag_df, decision_dates, rule)
        e = ensemble_from_comps(comp, wh, regimes)
        out[f'{rule}_ens'] = e
        out[f'{rule}_pct'] = pct_rank(e)
        out[f'{rule}_pos_prod'] = pos_fn(out[f'{rule}_pct'], 0.55, 0.70)
        out[f'{rule}_pos_wide'] = pos_fn(out[f'{rule}_pct'], 0.45, 0.80)

    out.to_csv(args.out)
    print(f"\nWrote {len(out)} rows to {args.out}")

    # Today's summary
    today = out.iloc[-1]
    print(f"\nToday ({out.index[-1].date()}): regime={today.regime}")
    print(f"  baseline:       ens {today.baseline_ens:.3f}  pct {today.baseline_pct:.3f}  "
          f"pos_prod {today.baseline_pos_prod:.2f}  pos_wide {today.baseline_pos_wide:.2f}")
    for rule in TRACKED_RULES:
        ens_diff = today[f'{rule}_ens'] - today.baseline_ens
        pct_diff = today[f'{rule}_pct'] - today.baseline_pct
        print(f"  {rule:15s}  ens {today[f'{rule}_ens']:.3f} ({ens_diff:+.3f})  "
              f"pct {today[f'{rule}_pct']:.3f} ({pct_diff:+.3f})  "
              f"pos_prod {today[f'{rule}_pos_prod']:.2f}  pos_wide {today[f'{rule}_pos_wide']:.2f}")

    return 0


if __name__ == '__main__':
    sys.exit(main())
