"""Shared utilities for the BTC model rebuild."""
from __future__ import annotations
import warnings
import os
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT = Path(os.environ.get("BTC_MODEL_ROOT", Path(__file__).parent))
RAW = ROOT / "data/raw"
DERIVED = ROOT / "data/derived"
HYP = ROOT / "data/hypotheses"
FINAL = ROOT / "data/final"

UTC = "UTC"

# Calibration / model deployment dates
# 2018-10-01: ~6 months after CME (2018-04-10) and Classic Cycle's fear_greed (2018-02-01)
# come online, giving each their respective expanding-rank warmup.
# Crypto Derivatives + ETH will be NaN before 2021-06-30 — handled by ensemble NaN-skip.
MODEL_START = pd.Timestamp("2018-10-01", tz=UTC)

# Per-hypothesis calibration start dates. Extending calibration helps Macro & Equities
# (which has stable cross-decade priors) but HURTS CME and Classic Cycle (whose modern
# behavior differs from 2018-2020 — see refit notes).
MIN_CALIB = {
    "macro_equities":     pd.Timestamp("2018-10-01", tz=UTC),
    "cme":                pd.Timestamp("2021-06-30", tz=UTC),
    "crypto_derivatives": pd.Timestamp("2021-06-30", tz=UTC),
    "classic_cycle":      pd.Timestamp("2021-06-30", tz=UTC),
    "etf_flows":          pd.Timestamp("2024-01-11", tz=UTC),
    "eth":                pd.Timestamp("2021-06-30", tz=UTC),
}
# Ensemble fit start: latest of the modern-era hypotheses
ENSEMBLE_FIT_START = pd.Timestamp("2021-06-30", tz=UTC)

# A/B label selection: set CALIB_LABEL env var to "y_30" or "y_60"
import os
CALIB_LABEL = os.environ.get("CALIB_LABEL", "y_30")
assert CALIB_LABEL in ("y_30", "y_60"), f"Bad CALIB_LABEL={CALIB_LABEL}"

# HOLDOUT_START = (most recent BTC price date) - 365 days
# Computed dynamically in load_holdout_start() since BTC end depends on pull date.
def load_holdout_start() -> pd.Timestamp:
    btc = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
    btc["date"] = pd.to_datetime(btc["date"], utc=True)
    last = btc["date"].max()
    return last - pd.Timedelta(days=365)


def to_utc_midnight(s: pd.Series) -> pd.Series:
    out = pd.to_datetime(s, errors="coerce")
    if out.dt.tz is None:
        out = out.dt.tz_localize(UTC)
    else:
        out = out.dt.tz_convert(UTC)
    return out.dt.normalize()


def expanding_pctile(s: pd.Series, min_periods: int = 180) -> pd.Series:
    return s.expanding(min_periods=min_periods).rank(pct=True)


def zscore_rolling(s: pd.Series, window: int = 90, min_periods: int | None = None) -> pd.Series:
    if min_periods is None:
        min_periods = max(30, window // 2)
    m = s.rolling(window, min_periods=min_periods).mean()
    sd = s.rolling(window, min_periods=min_periods).std()
    return (s - m) / sd


def compute_auc(y: pd.Series, score: pd.Series) -> float:
    df = pd.DataFrame({"y": y, "s": score}).dropna()
    if len(df) < 50 or df["y"].nunique() < 2:
        return float("nan")
    try:
        return float(roc_auc_score(df["y"], df["s"]))
    except Exception:
        return float("nan")


def auc_excess_weights(
    signals_df: pd.DataFrame,
    label: pd.Series,
    min_excess: float = 0.01,
    no_flip: set | None = None,
) -> tuple[dict, dict, dict]:
    """Per-column AUC, flip if AUC<0.5 (unless in no_flip), return (weights, flips, aucs).

    no_flip: set of column names whose direction is pinned by prior knowledge.
        Pinned signals never get flipped — if their AUC is below 0.5 they get the
        floor weight (min_excess) so they contribute negligibly without contributing
        in the wrong direction.
    """
    no_flip = no_flip or set()
    flips, aucs_post, raws = {}, {}, {}
    for col in signals_df.columns:
        raw = compute_auc(label, signals_df[col])
        raws[col] = raw
        if np.isnan(raw):
            flips[col] = False
            aucs_post[col] = float("nan")
            continue
        if col in no_flip:
            # PINNED: never flip
            flips[col] = False
            aucs_post[col] = raw
        elif raw < 0.5:
            flips[col] = True
            aucs_post[col] = compute_auc(label, 1.0 - signals_df[col])
        else:
            flips[col] = False
            aucs_post[col] = raw
    excess = {}
    for col, a in aucs_post.items():
        if np.isnan(a):
            excess[col] = 0.0
        else:
            excess[col] = max(a - 0.5, min_excess)
    total = sum(excess.values())
    if total <= 0:
        weights = {c: 0.0 for c in signals_df.columns}
    else:
        weights = {c: excess[c] / total for c in signals_df.columns}
    return weights, flips, aucs_post


def apply_flips(signals_df: pd.DataFrame, flips: dict) -> pd.DataFrame:
    out = signals_df.copy()
    for col, do_flip in flips.items():
        if do_flip:
            out[col] = 1.0 - out[col]
    return out


def composite_score(signals_df: pd.DataFrame, weights: dict) -> pd.Series:
    """NaN-skip composite: when a signal is NaN on a given day, drop its weight from the sum
    and renormalize the active weights for that day. (Used at hypothesis level.)"""
    cols = list(weights.keys())
    W = np.array([weights[c] for c in cols], dtype=float)
    X = signals_df[cols].values
    mask = ~np.isnan(X)
    w_active = mask * W
    denom = w_active.sum(axis=1)
    num = np.nansum(np.where(mask, X, 0.0) * W, axis=1)
    out = np.where(denom > 0, num / denom, np.nan)
    return pd.Series(out, index=signals_df.index)


def composite_score_no_renorm(signals_df: pd.DataFrame, weights: dict) -> pd.Series:
    """NaN-skip composite WITHOUT renormalization (legacy; used at ensemble layer pre-v13
    per playbook §8.1). Retained for backward-compat with `build_nnls_diagnostic.py` and any
    diagnostic code that depends on its behavior. DO NOT use for new ensemble-layer code —
    the renormalizing variant `composite_score_renorm` is the v13+ default."""
    cols = list(weights.keys())
    W = np.array([weights[c] for c in cols], dtype=float)
    X = signals_df[cols].values
    mask = ~np.isnan(X)
    num = np.nansum(np.where(mask, X, 0.0) * W, axis=1)
    return pd.Series(num, index=signals_df.index)


def composite_score_renorm(signals_df: pd.DataFrame, weights: dict) -> pd.Series:
    """NaN-skip composite WITH renormalization at ensemble layer (v13+ convention).

    For each date, only the weights of non-NaN hypotheses contribute, and those weights are
    renormalized to sum to 1. This is the same behavior as `composite_score` (hypothesis layer);
    applying it at the ensemble layer too fixes the v12-era compression bug where pre-2024
    ensemble_score was systematically lower than post-2024 by a regime-dependent factor of
    ~(1 − w_etf_flows) — up to 33% in bear regime.

    On all-present dates, this is identical to `composite_score_no_renorm` (no-op).
    """
    cols = list(weights.keys())
    W = np.array([weights[c] for c in cols], dtype=float)
    X = signals_df[cols].values
    mask = ~np.isnan(X)
    w_active = mask * W
    denom = w_active.sum(axis=1)
    num = np.nansum(np.where(mask, X, 0.0) * W, axis=1)
    out = np.where(denom > 0, num / denom, np.nan)
    return pd.Series(out, index=signals_df.index)


def load_btc_price() -> pd.DataFrame:
    df = pd.read_parquet(RAW / "price/btc_ohlc.parquet")
    df["date"] = to_utc_midnight(df["date"])
    df = df.set_index("date").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def load_eth_price() -> pd.DataFrame:
    df = pd.read_parquet(RAW / "price/eth_ohlc.parquet")
    df["date"] = to_utc_midnight(df["date"])
    df = df.set_index("date").sort_index()
    return df[["open", "high", "low", "close", "volume"]]


def load_labels() -> pd.DataFrame:
    return pd.read_parquet(DERIVED / "labels.parquet")


def load_regime() -> pd.DataFrame:
    return pd.read_parquet(DERIVED / "regime.parquet")


def compute_auc_safe(y, score):
    return compute_auc(y, score)
