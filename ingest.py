"""Daily incremental update. Runs 2x/day via APScheduler.

Pulls the last 5 days of data per token, merges new rows into existing parquets,
trims to retention limits, then recomputes and writes scores.json.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pandas as pd

from sources import SourceAPI
from scorer import compute_scores, load_panels, write_scores, append_history
from universe import load_symbols

DATA_DIR       = Path(os.environ.get("DATA_DIR", "/data"))
PRICE_RETENTION = 430   # days kept in spot_prices.parquet
CVD_RETENTION   = 385   # days kept in taker_buy/sell.parquet
FUND_RETENTION  = 385   # days kept in funding.parquet
PULL_DAYS       = 5     # how many recent days to pull per ingest run


def _merge(existing: pd.Series, new_bars: list, key: str = "close") -> pd.Series:
    """Merge new data points into an existing series, dedup by date, sort."""
    if not new_bars:
        return existing
    idx = pd.DatetimeIndex([r.date for r in new_bars])
    new_s = pd.Series(
        [getattr(r, key) for r in new_bars],
        index=idx,
        dtype=float,
    )
    combined = pd.concat([existing, new_s])
    combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


def _trim(df: pd.DataFrame, keep_days: int) -> pd.DataFrame:
    if df.empty:
        return df
    cutoff = df.index[-1] - pd.Timedelta(days=keep_days)
    return df[df.index >= cutoff]


def _load_or_empty(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df.sort_index()


async def run_ingest() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols()
    print(f"[ingest] starting — {len(symbols)} symbols")

    # Load existing panels
    prices_df = _load_or_empty(DATA_DIR / "spot_prices.parquet")
    buy_df    = _load_or_empty(DATA_DIR / "taker_buy.parquet")
    sell_df   = _load_or_empty(DATA_DIR / "taker_sell.parquet")
    fund_df   = _load_or_empty(DATA_DIR / "funding.parquet")

    async with SourceAPI() as api:
        await api.warm_supported()

        sem = asyncio.Semaphore(8)

        async def pull(sym: str):
            async with sem:
                prices  = await api.spot_history(sym,    limit=PULL_DAYS)
                cvd     = await api.cvd_history(sym,     limit=PULL_DAYS)
                funding = await api.funding_history(sym, limit=PULL_DAYS)
                return sym, prices, cvd, funding

        results = await asyncio.gather(*(pull(s) for s in symbols))

    # Merge new data into panels
    for sym, prices, cvd, funding in results:
        if prices:
            existing = prices_df[sym] if sym in prices_df.columns else pd.Series(dtype=float)
            prices_df[sym] = _merge(existing, prices, "close")
        if cvd:
            ex_buy  = buy_df[sym]  if sym in buy_df.columns  else pd.Series(dtype=float)
            ex_sell = sell_df[sym] if sym in sell_df.columns else pd.Series(dtype=float)
            buy_df[sym]  = _merge(ex_buy,  cvd, "buy")
            sell_df[sym] = _merge(ex_sell, cvd, "sell")
        if funding:
            ex_fund = fund_df[sym] if sym in fund_df.columns else pd.Series(dtype=float)
            fund_df[sym] = _merge(ex_fund, funding, "close")

    # Trim to retention limits and save
    prices_df = _trim(prices_df.sort_index(), PRICE_RETENTION)
    buy_df    = _trim(buy_df.sort_index(),    CVD_RETENTION)
    sell_df   = _trim(sell_df.sort_index(),   CVD_RETENTION)
    fund_df   = _trim(fund_df.sort_index(),   FUND_RETENTION)

    prices_df.to_parquet(DATA_DIR / "spot_prices.parquet")
    buy_df.to_parquet(DATA_DIR   / "taker_buy.parquet")
    sell_df.to_parquet(DATA_DIR  / "taker_sell.parquet")
    fund_df.to_parquet(DATA_DIR  / "funding.parquet")

    print(f"[ingest] saved panels — prices={prices_df.shape} cvd_buy={buy_df.shape}")

    # Recompute scores
    try:
        scores = compute_scores(prices_df, buy_df, sell_df)
        write_scores(scores, DATA_DIR)
        # Append today's rank_pct snapshot to history
        rank_row = pd.Series({s["symbol"]: s["rank_pct"] for s in scores["scores"]})
        append_history(rank_row, scores["as_of"][:10], DATA_DIR)
        print(f"[ingest] history updated")
    except Exception as e:
        print(f"[ingest] scoring failed: {e}")
        raise

    print("[ingest] done")
