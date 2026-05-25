"""Backfill orchestrator — v2 design with token-bucket rate limiting.

Public surface (app.py compatibility):
    main()            — alias for run_backfill(), fire-and-forget entry point
    run_backfill()    — full backfill, returns payload dict
    backfill_symbols() — selective single-symbol backfill (existing endpoint)
    progress          — module-level Progress for /checkpoint endpoint
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from bf_http import request_json
from bf_io import atomic_write, index_existing, load_existing, merge_metric, write_progress_snapshot
from bf_series import coerce_float, iso_day, last_n
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
from ingest import DATA_FILE, EXCLUDE_CATEGORIES, PRESET_TOKENS
from ratelimit import TokenBucket

LOG = logging.getLogger("divergence.backfill")

# ── tunables ──────────────────────────────────────────────────────────────────

UNIVERSE_URL = (
    "https://dud.up.railway.app/divergence.json"
    "?x_api_key=88554ffb2c8fb071f37cb6f6b3dced4d5ad8f57c8cbd37e4edad34a7e860edb1"
)

BATCH_SIZE          = 10   # tokens per batch — written atomically after each
TOKEN_CONCURRENCY   = 10   # match batch size so one batch runs at a time
COINGECKO_CONCURRENCY = 4
COINGLASS_CONCURRENCY = 6
LLAMA_CONCURRENCY   = 8

COINGLASS_RATE  = 300.0 / 60.0 * 0.85   # ~4.25 rps
COINGECKO_RATE  = 8.0
COINGLASS_BURST = 8.0
COINGECKO_BURST = 12.0

TOKEN_TIMEOUT_S        = 75.0
PROGRESS_SNAPSHOT_EVERY = 5
HTTP_LIMITS = httpx.Limits(
    max_connections=40,
    max_keepalive_connections=20,
    keepalive_expiry=30.0,
)

# ── progress tracking ─────────────────────────────────────────────────────────

class Progress:
    """Mutable progress state — asyncio is single-threaded, no lock needed."""
    def __init__(self):
        self.state = "idle"
        self.started_at = None
        self.finished_at = None
        self.total = 0
        self.processed = 0
        self.failed = 0
        self.in_flight: set[str] = set()
        self.last_error = None

    def to_dict(self) -> dict:
        return {
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": self.total,
            "processed": self.processed,
            "failed": self.failed,
            "in_flight": sorted(self.in_flight),
            "last_error": self.last_error,
        }


progress = Progress()

# ── per-token fetch ───────────────────────────────────────────────────────────

async def _fetch_one_token(
    token: dict,
    client: httpx.AsyncClient,
    *,
    cg_sem: asyncio.Semaphore,
    cgl_sem: asyncio.Semaphore,
    llama_sem: asyncio.Semaphore,
    cg_bucket: TokenBucket,
    cgl_bucket: TokenBucket,
    coingecko_key: str,
    coinglass_key: str,
) -> dict[str, list[dict]]:
    coin_id = token["id"]
    symbol = token["symbol"]
    has_cgl = bool(token.get("coinglass_coverage"))
    llama_slug = token.get("defillama_slug")
    chain_name = token.get("chain_name")
    has_llama = bool(llama_slug or chain_name)

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

    funding_apr   = await funding_task if funding_task else []
    perp_vol      = await perp_task    if perp_task    else []
    liq_oi_ratio  = await liq_task     if liq_task     else []
    tvl           = await tvl_task     if tvl_task     else []
    dex_vol       = await dex_task     if dex_task     else []

    return {
        "price": price, "spot_vol": spot_vol,
        "oi": oi, "funding_apr": funding_apr,
        "perp_vol": perp_vol, "liq_oi_ratio": liq_oi_ratio,
        "tvl": tvl, "dex_vol": dex_vol,
    }


# ── orchestration ─────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


async def _fetch_universe_remote(client: httpx.AsyncClient) -> Optional[list[dict]]:
    body = await request_json(client, UNIVERSE_URL)
    if isinstance(body, dict):
        u = body.get("universe")
        if isinstance(u, list) and u:
            return u
    return None


async def _fetch_universe_local() -> list[dict]:
    """Fallback: resolve universe locally (fresh deploy with no data yet)."""
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
    *,
    existing_idx: dict,
    client: httpx.AsyncClient,
    cg_sem, cgl_sem, llama_sem,
    cg_bucket, cgl_bucket,
    coingecko_key: str,
    coinglass_key: str,
) -> dict:
    coin_id = token["id"]
    if True:  # direct update (asyncio single-threaded)
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
            fetched = {k: [] for k in ("price","spot_vol","oi","funding_apr","perp_vol","liq_oi_ratio","tvl","dex_vol")}
            if True:  # direct update (asyncio single-threaded)
                progress.failed += 1
                progress.last_error = f"timeout:{coin_id}"
        except Exception as exc:
            LOG.exception("token failed id=%s", coin_id)
            fetched = {k: [] for k in ("price","spot_vol","oi","funding_apr","perp_vol","liq_oi_ratio","tvl","dex_vol")}
            if True:  # direct update (asyncio single-threaded)
                progress.failed += 1
                progress.last_error = f"{coin_id}:{exc!r}"[:200]

        prior = existing_idx.get(coin_id, {})
        prior_metrics = prior.get("metrics", {}) if isinstance(prior, dict) else {}
        merged = {
            name: merge_metric(fetched.get(name, []), prior_metrics.get(name, []) or [])
            for name in ("price","spot_vol","oi","funding_apr","perp_vol","liq_oi_ratio","tvl","dex_vol")
        }

        return {
            "id": coin_id,
            "symbol": token.get("symbol"),
            "rank": token.get("rank"),
            "defillama_slug": token.get("defillama_slug"),
            "chain_name": token.get("chain_name"),
            "coinglass_coverage": bool(token.get("coinglass_coverage")),
            "metrics": merged,
            "zscores": compute_zscores(merged),
        }
    finally:
        if True:  # direct update (asyncio single-threaded)
            progress.in_flight.discard(coin_id)
            progress.processed += 1
            if progress.processed % PROGRESS_SNAPSHOT_EVERY == 0:
                write_progress_snapshot(progress.to_dict())


async def run_backfill(
    *,
    coingecko_key: Optional[str] = None,
    coinglass_key: Optional[str] = None,
    universe: Optional[list[dict]] = None,
    token_limit: Optional[int] = None,
) -> dict:
    """Full backfill. Returns written payload."""
    coingecko_key = coingecko_key or os.environ.get("COINGECKO_API_KEY", "")
    coinglass_key = coinglass_key or os.environ.get("COINGLASS_API_KEY", "")
    if not coingecko_key:
        raise RuntimeError("COINGECKO_API_KEY missing")
    if not coinglass_key:
        raise RuntimeError("COINGLASS_API_KEY missing")

    if True:  # direct update (asyncio single-threaded)
        progress.state = "running"
        progress.started_at = _now_iso()
        progress.finished_at = None
        progress.total = 0
        progress.processed = 0
        progress.failed = 0
        progress.in_flight.clear()
        progress.last_error = None

    existing = load_existing()
    existing_idx = index_existing(existing)

    cg_sem    = asyncio.Semaphore(COINGECKO_CONCURRENCY)
    cgl_sem   = asyncio.Semaphore(COINGLASS_CONCURRENCY)
    llama_sem = asyncio.Semaphore(LLAMA_CONCURRENCY)
    token_sem = asyncio.Semaphore(TOKEN_CONCURRENCY)
    cg_bucket  = TokenBucket(rate_per_sec=COINGECKO_RATE,  capacity=COINGECKO_BURST)
    cgl_bucket = TokenBucket(rate_per_sec=COINGLASS_RATE, capacity=COINGLASS_BURST)

    timeout = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=30.0)
    async with httpx.AsyncClient(limits=HTTP_LIMITS, timeout=timeout) as client:
        if universe is None:
            universe = await _fetch_universe_remote(client)
            if not universe:
                LOG.warning("remote universe fetch failed, falling back to local resolve")
                universe = await _fetch_universe_local()
        if token_limit:
            universe = universe[:token_limit]

        if True:  # direct update (asyncio single-threaded)
            progress.total = len(universe)

        async def worker(tok: dict) -> dict:
            async with token_sem:
                return await _process_token(
                    tok, existing_idx=existing_idx, client=client,
                    cg_sem=cg_sem, cgl_sem=cgl_sem, llama_sem=llama_sem,
                    cg_bucket=cg_bucket, cgl_bucket=cgl_bucket,
                    coingecko_key=coingecko_key, coinglass_key=coinglass_key,
                )

        # Build running output from existing data; batch writes keep it current
        out_by_id: dict[str, dict] = dict(existing_idx)
        total_batches = (len(universe) + BATCH_SIZE - 1) // BATCH_SIZE

        t0 = time.monotonic()
        for batch_num, batch_start in enumerate(range(0, len(universe), BATCH_SIZE), 1):
            batch = universe[batch_start: batch_start + BATCH_SIZE]
            LOG.info("batch %d/%d (tokens %d-%d)", batch_num, total_batches,
                     batch_start + 1, batch_start + len(batch))

            batch_results = await asyncio.gather(
                *(worker(t) for t in batch), return_exceptions=True,
            )
            for r in batch_results:
                if isinstance(r, dict) and r.get("id"):
                    out_by_id[r["id"]] = r

            # Atomic write after every batch — crash only loses current batch
            atomic_write({"as_of": date.today().isoformat(), "universe": list(out_by_id.values())})
            LOG.info("batch %d/%d saved — %d tokens total", batch_num, total_batches, len(out_by_id))

        elapsed = time.monotonic() - t0

    payload = {"as_of": date.today().isoformat(), "universe": list(out_by_id.values())}

    progress.state = "complete"
    progress.finished_at = _now_iso()
    write_progress_snapshot(progress.to_dict())

    LOG.info("backfill done tokens=%d failed=%d elapsed=%.1fs", len(out_by_id), progress.failed, elapsed)
    return payload


# app.py compatibility alias
main = run_backfill


# ── selective single-token backfill (POST /backfill?symbol=X) ─────────────────

async def backfill_symbols(symbols: list[str]) -> None:
    """Backfill specific tokens by symbol. Merges into existing data."""
    if not DATA_FILE.exists():
        LOG.warning("no data file, run full backfill first")
        return

    syms = {s.upper() for s in symbols}
    existing_state = json.loads(DATA_FILE.read_text())
    existing_by_id = {t["id"]: t for t in existing_state.get("universe", [])}
    universe_list = [t for t in existing_state.get("universe", []) if t.get("symbol", "").upper() in syms]

    if not universe_list:
        LOG.warning("no matching tokens found for %s", syms)
        return

    coingecko_key = os.environ.get("COINGECKO_API_KEY", "")
    coinglass_key = os.environ.get("COINGLASS_API_KEY", "")
    result = await run_backfill(
        coingecko_key=coingecko_key,
        coinglass_key=coinglass_key,
        universe=universe_list,
    )
    LOG.info("selective backfill complete for %s", syms)
