"""Daily incremental update. Runs 2x/day via APScheduler.

Pulls the last 5 days of data per token, upserts into Postgres,
then recomputes and writes scores to DB.
"""
from __future__ import annotations

import asyncio
from datetime import date

import asyncpg

import db
from sources import SourceAPI
from scorer import compute_scores, load_panels_from_db, write_scores_to_db
from universe import load_symbols

PULL_DAYS = 5  # how many recent days to pull per ingest run


async def run_ingest(pool: asyncpg.Pool) -> None:
    symbols = load_symbols()
    print(f"[ingest] starting — {len(symbols)} symbols")

    async with SourceAPI() as api:
        await api.warm_supported()

        sem = asyncio.Semaphore(8)

        async def pull(sym: str):
            async with sem:
                prices  = await api.spot_history(sym,      limit=PULL_DAYS)
                cvd     = await api.cvd_history(sym,       limit=PULL_DAYS)
                funding = await api.funding_history(sym,   limit=PULL_DAYS)
                ls      = await api.ls_global_history(sym, limit=PULL_DAYS)
                return sym, prices, cvd, funding, ls

        results = await asyncio.gather(*(pull(s) for s in symbols))

    # Upsert each token's data directly into Postgres
    for sym, prices, cvd, funding, ls in results:
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

    print(f"[ingest] upserted raw series for {len(results)} symbols")

    # Load panels from DB, recompute scores, persist
    try:
        prices_df, buy_df, sell_df, fund_df, ls_df = await load_panels_from_db(pool)
        scores = compute_scores(prices_df, buy_df, sell_df, ls_df)
        await write_scores_to_db(pool, scores)
        print(f"[ingest] scores written — {scores['n_tokens']} tokens  regime={scores['regime']}")
    except Exception as e:
        print(f"[ingest] scoring failed: {e}")
        raise

    # Prune stale rows
    await db.apply_retention(pool, date.today())

    print("[ingest] done")
