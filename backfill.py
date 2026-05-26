"""One-shot historical seed. Run once after first deploy via POST /backfill.

Pulls:
  - spot prices: 220 days (200 for BTC MA gate + 20 buffer)
  - CVD (taker buy/sell): 100 days
  - funding: 100 days (stored for future use, not used in composite yet)

Writes parquets to DATA_DIR, then scores.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pandas as pd

from sources import SourceAPI
from scorer import compute_scores, write_scores, compute_history, append_history
from universe import load_symbols

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
PRICE_DAYS = 430   # 365d scores + 62d warmup (60d rolling beta + 2-day skip)
CVD_DAYS   = 385   # 365d + 16d warmup + buffer
FUND_DAYS  = 385


async def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    symbols = load_symbols()
    print(f"[backfill] {len(symbols)} symbols, DATA_DIR={DATA_DIR}")

    async with SourceAPI() as api:
        await api.warm_supported()

        sem = asyncio.Semaphore(4)

        async def pull(sym: str):
            async with sem:
                prices  = await api.spot_history(sym,    limit=PRICE_DAYS)
                cvd     = await api.cvd_history(sym,     limit=CVD_DAYS)
                funding = await api.funding_history(sym, limit=FUND_DAYS)
                src = "cg" if prices and prices[0].close > 0 else "cglass"
                print(f"[backfill] {sym}: price={len(prices)}({src}) cvd={len(cvd)} funding={len(funding)}")
                return sym, prices, cvd, funding

        results = await asyncio.gather(*(pull(s) for s in symbols), return_exceptions=True)
    results = [r for r in results if not isinstance(r, Exception)]

    # Build panels
    price_dict, buy_dict, sell_dict, fund_dict = {}, {}, {}, {}

    def dedup(series: pd.Series) -> pd.Series:
        return series[~series.index.duplicated(keep="last")].sort_index()

    for sym, prices, cvd, funding in results:
        if prices:
            idx = pd.DatetimeIndex([r.date for r in prices])
            price_dict[sym] = dedup(pd.Series([r.close for r in prices], index=idx))
        if cvd:
            idx = pd.DatetimeIndex([r.date for r in cvd])
            buy_dict[sym]  = dedup(pd.Series([r.buy  for r in cvd], index=idx))
            sell_dict[sym] = dedup(pd.Series([r.sell for r in cvd], index=idx))
        if funding:
            idx = pd.DatetimeIndex([r.date for r in funding])
            fund_dict[sym] = dedup(pd.Series([r.close for r in funding], index=idx))

    def _save(d: dict, name: str) -> None:
        if not d:
            print(f"[backfill] {name}: no data — skipping")
            return
        df = pd.DataFrame(d).sort_index()
        df.to_parquet(DATA_DIR / name)
        print(f"[backfill] saved {name}: {df.shape[1]} tokens × {df.shape[0]} days")

    _save(price_dict, "spot_prices.parquet")
    _save(buy_dict,   "taker_buy.parquet")
    _save(sell_dict,  "taker_sell.parquet")
    _save(fund_dict,  "funding.parquet")

    # Score + seed 90d history
    try:
        prices_df = pd.DataFrame(price_dict).sort_index()
        buy_df    = pd.DataFrame(buy_dict).sort_index()
        sell_df   = pd.DataFrame(sell_dict).sort_index()

        # Today's scores
        scores = compute_scores(prices_df, buy_df, sell_df)
        write_scores(scores, DATA_DIR)

        # Seed full 1-year history from parquets — write in one shot
        from scorer import HISTORY_RETENTION
        print(f"[backfill] seeding {HISTORY_RETENTION}d score history...")
        hist_df = compute_history(prices_df, buy_df, sell_df, days=HISTORY_RETENTION)
        hist_df.to_parquet(DATA_DIR / "scores_history.parquet")
        print(f"[backfill] history seeded: {len(hist_df)} dates × {hist_df.shape[1]} tokens")
    except Exception as e:
        print(f"[backfill] scoring failed: {e}")

    print("[backfill] complete")


if __name__ == "__main__":
    asyncio.run(main())
