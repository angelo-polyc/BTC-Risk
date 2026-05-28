"""Composite scoring — production implementation of the v2 momentum composite.

score = (1/4) * [z(residual_14d) + z(raw_14d) + z(raw_7d) + pct_rank(cvd_14d_sum)]

All z-scores are cross-sectional (per date, across tokens).
BTC 200d MA regime gate determines gate_on status.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Cross-sectional helpers                                                      #
# --------------------------------------------------------------------------- #

def _xs_zscore(panel: pd.DataFrame) -> pd.DataFrame:
    mu  = panel.mean(axis=1)
    sig = panel.std(axis=1).replace(0, np.nan)
    return panel.sub(mu, axis=0).div(sig, axis=0)


def _xs_pct_rank(panel: pd.DataFrame) -> pd.DataFrame:
    return panel.rank(axis=1, pct=True, na_option="keep")


# --------------------------------------------------------------------------- #
# Composite                                                                    #
# --------------------------------------------------------------------------- #

def compute_scores(
    prices:    pd.DataFrame,
    cvd_buy:   pd.DataFrame,
    cvd_sell:  pd.DataFrame,
    ls_global: pd.DataFrame | None = None,
    funding:   pd.DataFrame | None = None,
    oi:        pd.DataFrame | None = None,
) -> dict:
    """
    Returns a dict ready to be serialised as scores.json:
    {
      "as_of":     "2026-05-25T13:35:00Z",
      "regime":    "bear" | "bull",
      "gate_on":   false,
      "btc_price": 77443.0,
      "btc_ma200": 80826.0,
      "scores": [{"symbol": ..., "score": ..., "rank_pct": ...}, ...]   # sorted best first
    }
    """
    if prices.empty:
        raise ValueError("prices panel is empty")

    # Price panel is the universe. CVD is reindexed to it — NaN where unavailable.
    # Tokens without CVD still score on 3 momentum components (v1 behaviour).
    prices_c = prices
    buy_c    = cvd_buy.reindex(index=prices.index, columns=prices.columns)
    sell_c   = cvd_sell.reindex(index=prices.index, columns=prices.columns)

    # ---- momentum components ----
    skip = 2  # avoid microstructure noise

    # residual_14d: beta-adjusted 14d return
    btc     = prices_c["BTC"] if "BTC" in prices_c else prices_c.iloc[:, 0]
    log_btc = np.log(btc).diff()
    btc_var = log_btc.rolling(60).var()

    tok_14d = np.log(prices_c.shift(skip)) - np.log(prices_c.shift(skip + 14))
    btc_14d = (np.log(btc.shift(skip)) - np.log(btc.shift(skip + 14)))

    residual_parts = {}
    for sym in prices_c.columns:
        log_tok = np.log(prices_c[sym]).diff()
        cov     = log_tok.rolling(60).cov(log_btc)
        beta    = (cov / btc_var).shift(skip)
        residual_parts[sym] = tok_14d[sym] - beta * btc_14d
    residual_14d = pd.DataFrame(residual_parts)

    raw_14d = prices_c.shift(skip) / prices_c.shift(skip + 14) - 1
    raw_7d  = prices_c.shift(skip) / prices_c.shift(skip + 7)  - 1

    # cvd_14d_sum: net taker flow, 14d rolling, skip 2
    cvd_14d_sum = (buy_c - sell_c).shift(skip).rolling(14).sum()

    # ---- cross-sectional transforms ----
    z_res  = _xs_zscore(residual_14d)
    z_r14  = _xs_zscore(raw_14d)
    z_r7   = _xs_zscore(raw_7d)
    p_cvd  = _xs_pct_rank(cvd_14d_sum)

    # ---- composite (NaN-tolerant mean, require at least 3 of 4 components) ----
    # Tokens without CVD score on 3 price components; tokens with CVD score on 4.
    n_valid = (z_res.notna().astype(int) + z_r14.notna().astype(int) +
               z_r7.notna().astype(int)  + p_cvd.notna().astype(int))
    total   = z_res.fillna(0) + z_r14.fillna(0) + z_r7.fillna(0) + p_cvd.fillna(0)
    composite = (total / n_valid).where(n_valid >= 3)

    # ---- latest scores ----
    latest_date  = composite.dropna(how="all").index[-1]
    latest_scores = composite.loc[latest_date].dropna().sort_values(ascending=False)
    latest_ranks  = latest_scores.rank(pct=True, ascending=True)

    # ---- regime gate ----
    btc_series = prices["BTC"] if "BTC" in prices.columns else prices.iloc[:, 0]
    btc_price  = float(btc_series.iloc[-1])
    btc_ma200  = float(btc_series.rolling(200).mean().iloc[-1])
    gate_on    = bool(btc_price > btc_ma200)

    # Component values at latest date (for heatmap / breakdown)
    z_res_latest  = z_res.loc[latest_date]
    z_r14_latest  = z_r14.loc[latest_date]
    z_r7_latest   = z_r7.loc[latest_date]
    p_cvd_latest  = p_cvd.loc[latest_date]

    # L/S extreme short flag: ts-z of ls_global ratio vs 60d history < -1.0
    # Crowded shorts within a high-momentum name = potential squeeze amplifier.
    # Low-confidence screener annotation only — do not use as a composite input.
    if ls_global is not None and not ls_global.empty:
        ls_g       = ls_global.reindex(index=prices.index, columns=prices.columns)
        ls_mean60  = ls_g.rolling(60, min_periods=30).mean()
        ls_std60   = ls_g.rolling(60, min_periods=30).std().replace(0, np.nan)
        ls_tsz     = (ls_g - ls_mean60) / ls_std60
        ls_ext_short_latest = ls_tsz.loc[latest_date] if latest_date in ls_tsz.index else pd.Series(dtype=float)
    else:
        ls_ext_short_latest = pd.Series(dtype=float)

    # ---- pre-momentum composite (forward-looking screener signal) ----
    # Five signals: rel_7d_xs_z, cvd_7d_pct, comp_accel_xs_z, ext_short_fund, oi_growth_xs_z
    # Identifies non-Q5 tokens likely to enter Q5 within 14 days (+16pp lift vs base rate).

    def _xs_z(s: pd.Series) -> pd.Series:
        mu, sig = s.mean(), s.std()
        return (s - mu) / sig if sig > 0 else s * 0

    # Signal 1: BTC-relative 7d return, cross-sectional z-score
    btc_7d_ret = (btc.shift(skip) / btc.shift(skip + 7) - 1)
    rel_7d     = (prices_c.shift(skip) / prices_c.shift(skip + 7) - 1).sub(btc_7d_ret, axis=0)
    rel_7d_xs  = _xs_z(rel_7d.loc[latest_date])

    # Signal 2: CVD 7d pct rank (buy pressure)
    cvd_7d_sum = (buy_c - sell_c).shift(skip).rolling(7, min_periods=4).sum()
    cvd_7d_pct_s = cvd_7d_sum.rank(axis=1, pct=True, na_option='keep').loc[latest_date]
    cvd_7d_xs  = _xs_z(cvd_7d_pct_s.dropna())

    # Signal 3: Composite score acceleration (today vs 7 dates ago)
    valid_idx = composite.dropna(how='all').index
    if len(valid_idx) >= 8:
        comp_7d_ago   = composite.loc[valid_idx[-8]]
        comp_accel    = composite.loc[latest_date] - comp_7d_ago
        comp_accel_xs = _xs_z(comp_accel.dropna())
    else:
        comp_accel_xs = pd.Series(dtype=float)

    # Signal 4: Extreme short funding flag (ts-z < -1.5)
    if funding is not None and not funding.empty:
        fund_r     = funding.reindex(index=prices_c.index, columns=prices_c.columns)
        fund_mean  = fund_r.rolling(60, min_periods=30).mean()
        fund_std   = fund_r.rolling(60, min_periods=30).std().replace(0, np.nan)
        fund_tsz_l = ((fund_r - fund_mean) / fund_std).loc[latest_date] if latest_date in fund_r.index else pd.Series(dtype=float)
        ext_short  = (fund_tsz_l < -1.5).astype(float).where(fund_tsz_l.notna()).fillna(0)
        ext_short_xs = _xs_z(ext_short)
    else:
        ext_short_xs = pd.Series(dtype=float)

    # Signal 5: OI growth 14d, cross-sectional z-score
    if oi is not None and not oi.empty:
        oi_r       = oi.reindex(index=prices_c.index, columns=prices_c.columns)
        oi_growth  = oi_r.shift(skip) / oi_r.shift(skip + 14) - 1
        oi_growth_l = oi_growth.loc[latest_date] if latest_date in oi_growth.index else pd.Series(dtype=float)
        oi_xs      = _xs_z(oi_growth_l.dropna())
    else:
        oi_xs = pd.Series(dtype=float)

    # Combine — equal-weight, NaN-tolerant (require ≥ 2 of 5)
    pre_components = [rel_7d_xs, cvd_7d_xs, comp_accel_xs, ext_short_xs, oi_xs]
    all_syms = prices_c.columns
    pre_total  = pd.Series(0.0, index=all_syms)
    pre_nvalid = pd.Series(0,   index=all_syms)
    for comp in pre_components:
        comp_r = comp.reindex(all_syms)
        valid  = comp_r.notna()
        pre_total  += comp_r.fillna(0)
        pre_nvalid += valid.astype(int)
    pre_score = (pre_total / pre_nvalid).where(pre_nvalid >= 2)
    pre_rank  = pre_score.rank(pct=True, ascending=True, na_option='keep')

    scores_list = [
        {
            "symbol":           sym,
            "score":            round(float(latest_scores[sym]), 4),
            "rank_pct":         round(float(latest_ranks[sym]),  4),
            "res14_z":          round(float(z_res_latest[sym]),  3) if pd.notna(z_res_latest.get(sym)) else None,
            "raw14_z":          round(float(z_r14_latest[sym]),  3) if pd.notna(z_r14_latest.get(sym)) else None,
            "raw7_z":           round(float(z_r7_latest[sym]),   3) if pd.notna(z_r7_latest.get(sym))  else None,
            "cvd_pct":          round(float(p_cvd_latest[sym]),  3) if pd.notna(p_cvd_latest.get(sym)) else None,
            "ls_ext_short":     bool(ls_ext_short_latest[sym] < -1.0)
                                if sym in ls_ext_short_latest.index and pd.notna(ls_ext_short_latest[sym])
                                else None,
            "pre_mom_score":    round(float(pre_score[sym]), 4)    if sym in pre_score.index and pd.notna(pre_score.get(sym)) else None,
            "pre_mom_rank_pct": round(float(pre_rank[sym]),  4)    if sym in pre_rank.index  and pd.notna(pre_rank.get(sym))  else None,
            # Individual pre-momentum signal components (for Pre Momentum screener table)
            "pm_rel7":  round(float(rel_7d_xs[sym]),     3) if sym in rel_7d_xs.index     and pd.notna(rel_7d_xs.get(sym))     else None,
            "pm_cvd7":  round(float(cvd_7d_pct_s[sym]),  3) if sym in cvd_7d_pct_s.index  and pd.notna(cvd_7d_pct_s.get(sym))  else None,
            "pm_accel": round(float(comp_accel_xs[sym]),  3) if sym in comp_accel_xs.index and pd.notna(comp_accel_xs.get(sym)) else None,
            "pm_fund":  round(-float(fund_tsz_l[sym]),   3) if funding is not None and not funding.empty and sym in fund_tsz_l.index and pd.notna(fund_tsz_l.get(sym)) else None,
            "pm_oi":    round(float(oi_xs[sym]),          3) if sym in oi_xs.index          and pd.notna(oi_xs.get(sym))          else None,
        }
        for sym in latest_scores.index
    ]

    return {
        "as_of":     datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "regime":    "bull" if gate_on else "bear",
        "gate_on":   gate_on,
        "btc_price": round(btc_price, 2),
        "btc_ma200": round(btc_ma200, 2),
        "n_tokens":  len(scores_list),
        "scores":    scores_list,
    }


# --------------------------------------------------------------------------- #
# History helpers                                                              #
# --------------------------------------------------------------------------- #

HISTORY_RETENTION = 365  # days


def compute_history(
    prices: pd.DataFrame,
    cvd_buy: pd.DataFrame,
    cvd_sell: pd.DataFrame,
    days: int = HISTORY_RETENTION,
) -> pd.DataFrame:
    """
    Compute daily composite rank_pct for all tokens over the last `days` calendar days.
    Returns a DataFrame: index=date, columns=symbol, values=rank_pct [0,1].
    """
    prices_c = prices
    buy_c    = cvd_buy.reindex(index=prices.index, columns=prices.columns)
    sell_c   = cvd_sell.reindex(index=prices.index, columns=prices.columns)

    skip = 2
    btc      = prices_c["BTC"] if "BTC" in prices_c else prices_c.iloc[:, 0]
    log_btc  = np.log(btc).diff()
    btc_var  = log_btc.rolling(60).var()
    tok_14d  = np.log(prices_c.shift(skip)) - np.log(prices_c.shift(skip + 14))
    btc_14d  = np.log(btc.shift(skip)) - np.log(btc.shift(skip + 14))

    residual_parts = {}
    for sym in prices_c.columns:
        cov  = np.log(prices_c[sym]).diff().rolling(60).cov(log_btc)
        beta = (cov / btc_var).shift(skip)
        residual_parts[sym] = tok_14d[sym] - beta * btc_14d
    residual_14d = pd.DataFrame(residual_parts)

    raw_14d     = prices_c.shift(skip) / prices_c.shift(skip + 14) - 1
    raw_7d      = prices_c.shift(skip) / prices_c.shift(skip + 7)  - 1
    cvd_14d_sum = (buy_c - sell_c).shift(skip).rolling(14).sum()

    z_res  = _xs_zscore(residual_14d)
    z_r14  = _xs_zscore(raw_14d)
    z_r7   = _xs_zscore(raw_7d)
    p_cvd  = _xs_pct_rank(cvd_14d_sum)

    n_valid   = (z_res.notna().astype(int) + z_r14.notna().astype(int) +
                 z_r7.notna().astype(int)  + p_cvd.notna().astype(int))
    total     = z_res.fillna(0) + z_r14.fillna(0) + z_r7.fillna(0) + p_cvd.fillna(0)
    composite = (total / n_valid).where(n_valid >= 3)

    # Trim to last `days` calendar days with valid data
    valid_dates = composite.dropna(how="all").index
    cutoff      = valid_dates[-1] - pd.Timedelta(days=days - 1)
    composite   = composite.loc[valid_dates[valid_dates >= cutoff]]

    # Convert each row to rank_pct
    return composite.rank(axis=1, pct=True, na_option="keep")


def append_history(rank_pct_row: pd.Series, date_str: str, data_dir: Path) -> None:
    """Append one day's rank_pct snapshot to scores_history.parquet."""
    path = data_dir / "scores_history.parquet"
    new_row = rank_pct_row.to_frame(name=date_str).T
    new_row.index = pd.DatetimeIndex([date_str])

    if path.exists():
        hist = pd.read_parquet(path)
        # Drop duplicate date if re-running same day, then append
        hist = hist[hist.index != new_row.index[0]]
        hist = pd.concat([hist, new_row]).sort_index()
        # Trim to retention window
        cutoff = hist.index[-1] - pd.Timedelta(days=HISTORY_RETENTION - 1)
        hist = hist[hist.index >= cutoff]
    else:
        hist = new_row

    hist.to_parquet(path)


def load_history(data_dir: Path, days: int = HISTORY_RETENTION) -> pd.DataFrame | None:
    """Load scores_history.parquet, return last `days` rows."""
    path = data_dir / "scores_history.parquet"
    if not path.exists():
        return None
    hist = pd.read_parquet(path).sort_index()
    return hist.iloc[-days:]


# --------------------------------------------------------------------------- #
# I/O helpers                                                                  #
# --------------------------------------------------------------------------- #

def load_panels(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load spot prices, taker buy, taker sell from DATA_DIR. Returns (prices, buy, sell)."""
    def _load(name: str) -> pd.DataFrame:
        p = data_dir / name
        if not p.exists():
            return pd.DataFrame()
        df = pd.read_parquet(p)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df.sort_index()

    return _load("spot_prices.parquet"), _load("taker_buy.parquet"), _load("taker_sell.parquet")


def write_scores(scores: dict, data_dir: Path) -> None:
    """Atomically overwrite scores.json."""
    import json
    out  = data_dir / "scores.json"
    tmp  = data_dir / "scores.tmp"
    tmp.write_text(json.dumps(scores, separators=(",", ":")))
    tmp.replace(out)
    print(f"[scorer] wrote {out} — {scores['n_tokens']} tokens  regime={scores['regime']}")


# --------------------------------------------------------------------------- #
# DB-backed I/O helpers (new — parquet paths left intact above)               #
# --------------------------------------------------------------------------- #

def _panel_to_df(data: dict[str, list[dict]]) -> pd.DataFrame:
    """Rebuild a wide DataFrame from {symbol: [{d, v}, ...]}."""
    series = {}
    for sym, points in data.items():
        idx = pd.DatetimeIndex([p["d"] for p in points])
        series[sym] = pd.Series([p["v"] for p in points], index=idx, dtype=float)
    if not series:
        return pd.DataFrame()
    return pd.DataFrame(series).sort_index()


async def load_panels_from_db(
    pool,
    price_days: int = 430,
    cvd_days: int = 385,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load all six panels from Postgres and return as DataFrames.

    Returns: (prices_df, buy_df, sell_df, fund_df, ls_df, oi_df)
    """
    import db as _db
    prices_raw = await _db.get_raw_panel(pool, "price",       price_days)
    buy_raw    = await _db.get_raw_panel(pool, "taker_buy",   cvd_days)
    sell_raw   = await _db.get_raw_panel(pool, "taker_sell",  cvd_days)
    fund_raw   = await _db.get_raw_panel(pool, "funding",     cvd_days)
    ls_raw     = await _db.get_raw_panel(pool, "ls_global",   cvd_days)
    oi_raw     = await _db.get_raw_panel(pool, "oi",          cvd_days)

    prices_df = _panel_to_df(prices_raw)
    buy_df    = _panel_to_df(buy_raw)
    sell_df   = _panel_to_df(sell_raw)
    fund_df   = _panel_to_df(fund_raw)
    ls_df     = _panel_to_df(ls_raw)
    oi_df     = _panel_to_df(oi_raw)

    return prices_df, buy_df, sell_df, fund_df, ls_df, oi_df


async def write_scores_to_db(pool, scores_dict: dict) -> None:
    """Write compute_scores() output to mom_scores, mom_regime, and mom_scores_history."""
    import db as _db

    today_str = date.today().isoformat()

    # --- mom_scores ---
    score_rows = []
    for s in scores_dict.get("scores", []):
        score_rows.append({
            "symbol":       s["symbol"],
            "as_of":        scores_dict["as_of"],
            "score":        s.get("score"),
            "rank_pct":     s.get("rank_pct"),
            "res14_z":      s.get("res14_z"),
            "raw14_z":      s.get("raw14_z"),
            "raw7_z":       s.get("raw7_z"),
            "cvd_pct":          s.get("cvd_pct"),
            "ls_ext_short":     s.get("ls_ext_short"),
            "pre_mom_score":    s.get("pre_mom_score"),
            "pre_mom_rank_pct": s.get("pre_mom_rank_pct"),
            "pm_rel7":  s.get("pm_rel7"),
            "pm_cvd7":  s.get("pm_cvd7"),
            "pm_accel": s.get("pm_accel"),
            "pm_fund":  s.get("pm_fund"),
            "pm_oi":    s.get("pm_oi"),
        })
    await _db.upsert_scores_batch(pool, score_rows)

    # --- mom_regime ---
    regime = {
        "as_of":     scores_dict["as_of"],
        "regime":    scores_dict["regime"],
        "gate_on":   scores_dict["gate_on"],
        "btc_price": scores_dict.get("btc_price"),
        "btc_ma200": scores_dict.get("btc_ma200"),
        "n_tokens":  scores_dict.get("n_tokens", len(score_rows)),
    }
    await _db.upsert_regime(pool, regime)

    # --- mom_scores_history (today's rank_pct snapshot) ---
    hist_rows = [
        {"symbol": s["symbol"], "date": today_str, "rank_pct": s["rank_pct"]}
        for s in scores_dict.get("scores", [])
        if s.get("rank_pct") is not None
    ]
    await _db.upsert_scores_history_batch(pool, hist_rows)

    print(f"[scorer] DB write complete — {len(score_rows)} tokens  regime={scores_dict['regime']}")
