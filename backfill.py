"""Backfill orchestrator — v2 design with token-bucket rate limiting.

Public surface (app.py compatibility):
    main(pool)             — alias for run_backfill()
    run_backfill(pool)     — full backfill
    backfill_symbols(pool) — selective single-symbol backfill
    progress               — module-level Progress for /checkpoint endpoint
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

import asyncpg
import httpx

import db
from bf_sources import (
    fetch_coingecko,
    fetch_coinglass_funding_apr,
    fetch_coinglass_liq_oi_ratio,
    fetch_coinglass_oi,
    fetch_coinglass_perp_vol,
    fetch_llama_dex_vol,
    fetch_llama_tvl,
)
from bf_zscores import compute_zscores
from ingest import EXCLUDE_CATEGORIES, PRESET_TOKENS
from ratelimit import TokenBucket

LOG = logging.getLogger("divergence.backfill")

# ── tunables ──────────────────────────────────────────────────────────────────

BATCH_SIZE            = 10
TOKEN_CONCURRENCY     = 10
COINGECKO_CONCURRENCY = 4
COINGLASS_CONCURRENCY = 6
LLAMA_CONCURRENCY     = 8

COINGLASS_RATE  = 300.0 / 60.0 * 0.85
COINGECKO_RATE  = 8.0
COINGLASS_BURST = 8.0
COINGECKO_BURST = 12.0

TOKEN_TIMEOUT_S         = 75.0
PROGRESS_SNAPSHOT_EVERY = 5
HTTP_LIMITS = httpx.Limits(
    max_connections=40,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)

# ── progress tracking ─────────────────────────────────────────────────────────

class Progress:
    def __init__(self):
        self.state       = "idle"
        self.started_at  = None
        self.finished_at = None
        self.total       = 0
        self.processed   = 0
        self.failed      = 0
        self.in_flight: set[str] = set()
        self.last_error  = None

    def to_dict(self) -> dict:
        return {
            "state":       self.state,
            "started_at":  self.started_at,
            "finished_at": self.finished_at,
            "total":       self.total,
            "processed":   self.processed,
            "failed":      self.failed,
            "in_flight":   sorted(self.in_flight),
            "last_error":  self.last_error,
        }

progress = Progress()

# ── per-token fetch (unchanged) ───────────────────────────────────────────────

async def _fetch_one_token(
    token: dict,
    client: httpx.AsyncClient,
    *,
    cg_sem, cgl_sem, llama_sem,
    cg_bucket, cgl_bucket,
    coingecko_key: str,
    coinglass_key: str,
) -> dict[str, list[dict]]:
    coin_id    = token["id"]
    symbol     = token["symbol"]
    has_cgl    = bool(token.get("coinglass_coverage"))
    llama_slug = token.get("defillama_slug")
    chain_name = token.get("chain_name")
    has_llama  = bool(llama_slug or chain_name)

    cg_task = asyncio.create_task(
        fetch_coingecko(client, cg_sem, coin_id, coingecko_key, bucket=cg_bucket)
    )
    oi_task = funding_task = perp_task = None
    if has_cgl:
        oi_task      = asyncio.create_task(fetch_coinglass_oi(client, cgl_sem, symbol, coinglass_key, bucket=cgl_bucket))
        funding_task = asyncio.create_task(fetch_coinglass_funding_apr(client, cgl_sem, symbol, coinglass_key, bucket=cgl_bucket))
        perp_task    = asyncio.create_task(fetch_coinglass_perp_vol(client, cgl_sem, symbol, coinglass_key, bucket=cgl_bucket))

    tvl_task = dex_task = None
    if has_llama:
        tvl_task = asyncio.create_task(fetch_llama_tvl(client, llama_sem, defillama_slug=llama_slug, chain_name=chain_name))
        dex_task = asyncio.create_task(fetch_llama_dex_vol(client, llama_sem, defillama_slug=llama_slug, chain_name=chain_name))

    price, spot_vol = await cg_task

    oi: list[dict] = []
    if oi_task is not None:
        oi = await oi_task
    liq_task = None
    if has_cgl:
        liq_task = asyncio.create_task(
            fetch_coinglass_liq_oi_ratio(client, cgl_sem, symbol, coinglass_key, oi, bucket=cgl_bucket)
        )

    funding_apr  = await funding_task if funding_task else []
    perp_vol     = await perp_task    if perp_task    else []
    liq_oi_ratio = await liq_task     if liq_task     else []
    tvl          = await tvl_task     if tvl_task     else []
    dex_vol      = await dex_task     if dex_task     else []

    return {
        "price": price, "spot_vol": spot_vol,
        "oi": oi, "funding_apr": funding_apr,
        "perp_vol": perp_vol, "liq_oi_ratio": liq_oi_ratio,
        "tvl": tvl, "dex_vol": dex_vol,
    }


# ── per-token DB write ────────────────────────────────────────────────────────

async def _write_token_to_db(pool: asyncpg.Pool, token: dict, fetched: dict) -> None:
    today_str = date.today().isoformat()

    await db.upsert_token(pool, {
        "id":                 token["id"],
        "symbol":             token["symbol"],
        "rank":               token.get("rank"),
        "market_cap_usd":     token.get("market_cap_usd"),
        "defillama_slug":     token.get("defillama_slug"),
        "chain_name":         token.get("chain_name"),
        "coinglass_coverage": bool(token.get("coinglass_coverage")),
        "updated_at":         today_str,
    })

    for metric, points in fetched.items():
        if points:
            await db.upsert_metric_series(pool, token["id"], metric, points)


# ── orchestration ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _resolve_universe(pool: asyncpg.Pool, client: httpx.AsyncClient) -> list[dict]:
    """Get token universe from DB (existing tokens), fall back to fresh API resolve."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, symbol, rank, market_cap_usd, defillama_slug,
                   chain_name, coinglass_coverage
            FROM tokens ORDER BY rank NULLS LAST
        """)
    if rows:
        return [dict(r) for r in rows]

    LOG.warning("no tokens in DB — resolving universe from APIs")
    from sources import SourceAPI
    from universe import resolve_universe
    async with SourceAPI() as api:
        await api.prep_run()
        tokens = await resolve_universe(
            top_n=300,
            exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS,
            api=api,
        )
    return [
        {
            "id": t.id, "symbol": t.symbol, "rank": t.rank,
            "defillama_slug": t.defillama_slug, "chain_name": t.chain_name,
            "coinglass_coverage": t.has_coinglass,
        }
        for t in tokens
    ]


async def _process_token(
    token: dict,
    pool: asyncpg.Pool,
    *,
    client: httpx.AsyncClient,
    cg_sem, cgl_sem, llama_sem,
    cg_bucket, cgl_bucket,
    coingecko_key: str,
    coinglass_key: str,
) -> None:
    coin_id = token["id"]
    progress.in_flight.add(coin_id)
    try:
        try:
            fetched = await asyncio.wait_for(
                _fetch_one_token(
                    token, client,
                    cg_sem=cg_sem, cgl_sem=cgl_sem, llama_sem=llama_sem,
                    cg_bucket=cg_bucket, cgl_bucket=cgl_bucket,
                    coingecko_key=coingecko_key, coinglass_key=coinglass_key,
                ),
                timeout=TOKEN_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            LOG.warning("token timeout id=%s", coin_id)
            progress.failed += 1
            progress.last_error = f"timeout:{coin_id}"
            return
        except Exception as exc:
            LOG.exception("token failed id=%s", coin_id)
            progress.failed += 1
            progress.last_error = f"{coin_id}:{exc!r}"[:200]
            return

        await _write_token_to_db(pool, token, fetched)

    finally:
        progress.in_flight.discard(coin_id)
        progress.processed += 1


async def run_backfill(
    pool: asyncpg.Pool,
    *,
    coingecko_key: Optional[str] = None,
    coinglass_key: Optional[str] = None,
    universe: Optional[list[dict]] = None,
    token_limit: Optional[int] = None,
) -> None:
    coingecko_key = coingecko_key or os.environ.get("COINGECKO_API_KEY", "")
    coinglass_key = coinglass_key or os.environ.get("COINGLASS_API_KEY", "")
    if not coingecko_key:
        raise RuntimeError("COINGECKO_API_KEY missing")
    if not coinglass_key:
        raise RuntimeError("COINGLASS_API_KEY missing")

    progress.state      = "running"
    progress.started_at = _now_iso()
    progress.finished_at = None
    progress.total = progress.processed = progress.failed = 0
    progress.in_flight.clear()
    progress.last_error = None

    cg_sem    = asyncio.Semaphore(COINGECKO_CONCURRENCY)
    cgl_sem   = asyncio.Semaphore(COINGLASS_CONCURRENCY)
    llama_sem = asyncio.Semaphore(LLAMA_CONCURRENCY)
    token_sem = asyncio.Semaphore(TOKEN_CONCURRENCY)
    cg_bucket  = TokenBucket(rate_per_sec=COINGECKO_RATE,  capacity=COINGECKO_BURST)
    cgl_bucket = TokenBucket(rate_per_sec=COINGLASS_RATE,  capacity=COINGLASS_BURST)

    timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=30.0)
    async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=timeout) as client:
        if universe is None:
            universe = await _resolve_universe(pool, client)
        if token_limit:
            universe = universe[:token_limit]

        progress.total = len(universe)
        t0 = time.monotonic()

        async def worker(tok: dict) -> None:
            async with token_sem:
                await _process_token(
                    tok, pool, client=client,
                    cg_sem=cg_sem, cgl_sem=cgl_sem, llama_sem=llama_sem,
                    cg_bucket=cg_bucket, cgl_bucket=cgl_bucket,
                    coingecko_key=coingecko_key, coinglass_key=coinglass_key,
                )

        await asyncio.gather(*(worker(t) for t in universe), return_exceptions=True)
        elapsed = time.monotonic() - t0

    progress.state       = "complete"
    progress.finished_at = _now_iso()
    LOG.info("backfill done tokens=%d failed=%d elapsed=%.1fs",
             progress.processed, progress.failed, elapsed)


# app.py compatibility alias
async def main(pool: asyncpg.Pool) -> None:
    await run_backfill(pool)


# ── selective single-token backfill ──────────────────────────────────────────

async def backfill_symbols(pool: asyncpg.Pool, symbols: list[str]) -> None:
    syms = {s.upper() for s in symbols}

    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, symbol, rank, market_cap_usd, defillama_slug,
                   chain_name, coinglass_coverage
            FROM tokens WHERE UPPER(symbol) = ANY($1)
        """, list(syms))

    if not rows:
        LOG.warning("no matching tokens in DB for %s", syms)
        return

    universe = [dict(r) for r in rows]
    coingecko_key = os.environ.get("COINGECKO_API_KEY", "")
    coinglass_key = os.environ.get("COINGLASS_API_KEY", "")
    await run_backfill(pool, coingecko_key=coingecko_key, coinglass_key=coinglass_key,
                       universe=universe)
    LOG.info("selective backfill complete for %s", syms)
