"""One-shot historical seed. Run once after first deploy via POST /backfill.

Pulls:
  - spot prices: 430 days
  - CVD (taker buy/sell): 385 days
  - funding: 385 days
  - L/S global: 385 days

Writes all data to Postgres, then scores and seeds history.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import asyncpg
import pandas as pd

import db
from sources import SourceAPI
from scorer import compute_scores, compute_history, load_panels_from_db, write_scores_to_db
from universe import load_symbols

PRICE_DAYS = 430   # 365d scores + 62d warmup (60d rolling beta + 2-day skip)
CVD_DAYS   = 385   # 365d + 16d warmup + buffer
FUND_DAYS  = 385
LS_DAYS    = 385   # 365d + 20d warmup for 60d ts-z (fills in over time)
OI_DAYS    = 385   # 365d + 14d warmup for oi_growth_14d computation

_HERE  = Path(__file__).parent
CG_IDS = json.loads((_HERE / "cg_ids.json").read_text())


async def main(pool: asyncpg.Pool) -> None:
    symbols = load_symbols()
    print(f"[backfill] {len(symbols)} symbols")

    # Seed token registry from cg_ids.json
    token_records = [
        {"symbol": sym, "cg_id": CG_IDS.get(sym)}
        for sym in symbols
    ]
    await db.upsert_tokens_batch(pool, token_records)
    print(f"[backfill] upserted {len(token_records)} tokens into mom_tokens")

    async with SourceAPI() as api:
        await api.warm_supported()

        # Sequential loop — must stay sequential
        for i, sym in enumerate(symbols, 1):
            try:
                prices  = await api.spot_history(sym,      limit=PRICE_DAYS)
                cvd     = await api.cvd_history(sym,       limit=CVD_DAYS)
                funding = await api.funding_history(sym,   limit=FUND_DAYS)
                ls      = await api.ls_global_history(sym, limit=LS_DAYS)
                oi      = await api.oi_history(sym,        limit=OI_DAYS)

                src = "cg" if prices and prices[0].close > 0 else "cglass"
                print(f"[backfill] [{i}/{len(symbols)}] {sym}: price={len(prices)}({src}) cvd={len(cvd)} funding={len(funding)} ls={len(ls)} oi={len(oi)}")

                # Upsert immediately after each token pull
                if prices:
                    points = [{"d": str(r.date), "v": r.close} for r in prices]
                    await db.upsert_raw_series(pool, sym, "price", points)
                if cvd:
                    buy_pts  = [{"d": str(r.date), "v": r.buy}  for r in cvd]
                    sell_pts = [{"d": str(r.date), "v": r.sell} for r in cvd]
                    await db.upsert_raw_series(pool, sym, "taker_buy",  buy_pts)
                    await db.upsert_raw_series(pool, sym, "taker_sell", sell_pts)
                if funding:
                    points = [{"d": str(r.date), "v": r.close} for r in funding]
                    await db.upsert_raw_series(pool, sym, "funding", points)
                if ls:
                    points = [{"d": str(r.date), "v": r.close} for r in ls]
                    await db.upsert_raw_series(pool, sym, "ls_global", points)
                if oi:
                    points = [{"d": str(r.date), "v": r.close} for r in oi]
                    await db.upsert_raw_series(pool, sym, "oi", points)

            except Exception as e:
                print(f"[backfill] [{i}/{len(symbols)}] {sym}: error — {e}")

    # Score
    try:
        prices_df, buy_df, sell_df, fund_df, ls_df, oi_df = await load_panels_from_db(pool)

        # Today's scores
        scores = compute_scores(prices_df, buy_df, sell_df, ls_df, funding=fund_df, oi=oi_df)
        await write_scores_to_db(pool, scores)

        # Seed full 1-year rank_pct history from the loaded panels
        from db import HISTORY_RETENTION
        print(f"[backfill] seeding {HISTORY_RETENTION}d score history...")
        hist_df = compute_history(prices_df, buy_df, sell_df, days=HISTORY_RETENTION)
        print(f"[backfill] history computed: {len(hist_df)} dates × {hist_df.shape[1]} tokens")

        hist_rows = []
        for dt, row in hist_df.iterrows():
            date_str = str(dt.date()) if hasattr(dt, "date") else str(dt)[:10]
            for sym, rank_pct in row.items():
                if rank_pct == rank_pct and rank_pct is not None:  # not NaN
                    hist_rows.append({"symbol": sym, "date": date_str, "rank_pct": float(rank_pct)})

        await db.upsert_scores_history_batch(pool, hist_rows)
        print(f"[backfill] history seeded: {len(hist_rows)} rows")

    except Exception as e:
        print(f"[backfill] scoring failed: {e}")

    print("[backfill] complete")


if __name__ == "__main__":
    import db as _db

    async def _run():
        pool = await _db.create_pool()
        await _db.init_db(pool)
        await main(pool)
        await pool.close()

    asyncio.run(_run())
