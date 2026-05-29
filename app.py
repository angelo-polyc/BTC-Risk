"""FastAPI app — serves divergence data from Postgres, runs ingest cron in-process."""
import asyncio
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

import db
from backfill import backfill_symbols, main as run_backfill_main, progress as backfill_progress
from ingest import run_ingest, seed_zscore_history

_background_tasks: set = set()
API_KEY = os.environ.get("READ_API_KEY")
scheduler = AsyncIOScheduler(timezone="America/New_York")

_pool: asyncpg.Pool | None = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _pool
    db_url = db.DATABASE_URL
    masked = db_url[:30] + "..." if len(db_url) > 30 else db_url or "(empty)"
    print(f"[startup] connecting to DB: {masked}")
    try:
        _pool = await db.create_pool()
        await db.init_db(_pool)
        print("[startup] DB ready")
    except Exception as e:
        print(f"[startup] DB connection FAILED: {e}")
        _pool = None

    scheduler.add_job(lambda: asyncio.create_task(_safe_ingest()), CronTrigger(hour=0,  minute=0), id="ny_midnight")
    scheduler.add_job(lambda: asyncio.create_task(_safe_ingest()), CronTrigger(hour=6,  minute=0), id="ny_morning")
    scheduler.add_job(lambda: asyncio.create_task(_safe_ingest()), CronTrigger(hour=12, minute=0), id="ny_noon")
    scheduler.add_job(lambda: asyncio.create_task(_safe_ingest()), CronTrigger(hour=18, minute=0), id="ny_evening")
    scheduler.start()
    print("[startup] scheduler started")
    yield
    scheduler.shutdown()
    if _pool:
        await _pool.close()


async def _safe_ingest():
    if _pool is None:
        print("[ingest] skipped — no DB pool")
        return
    await run_ingest(_pool)


app = FastAPI(lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://btc-risk.up.railway.app"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _auth(x_api_key: str | None) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


# ── read endpoints ────────────────────────────────────────────────────────────

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/status")
async def status(x_api_key: str | None = None):
    _auth(x_api_key)
    return await db.get_status(get_pool())


@app.get("/zscores")
async def zscores(x_api_key: str | None = None):
    """Lightweight dashboard payload — z-scores + token metadata only (~15KB)."""
    _auth(x_api_key)
    rows = await db.get_all_zscores(get_pool())
    if not rows:
        raise HTTPException(status_code=503, detail="no data yet — run POST /backfill then POST /ingest")
    as_of = rows[0]["as_of"] if rows else None
    return {"as_of": as_of, "universe": rows}


@app.get("/zscore_history")
async def zscore_history(
    x_api_key: str | None = None,
    token_id: str | None = None,
    since: str | None = None,
):
    _auth(x_api_key)
    return await db.get_zscore_history(get_pool(), token_id=token_id, since=since)


# ── write / trigger endpoints ─────────────────────────────────────────────────

@app.post("/ingest")
async def manual_ingest(x_api_key: str | None = None):
    _auth(x_api_key)
    task = asyncio.create_task(run_ingest(get_pool()))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "started"}


@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None, symbol: str | None = None):
    _auth(x_api_key)
    if symbol:
        task = asyncio.create_task(backfill_symbols(get_pool(), [symbol.upper()]))
        msg  = f"started — backfilling {symbol.upper()}"
    else:
        task = asyncio.create_task(run_backfill_main(get_pool()))
        msg  = "started — backfill runs in background, takes 5-10 min"
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": msg}


@app.post("/seed_zscore_history")
async def post_seed_zscore_history(x_api_key: str | None = None):
    _auth(x_api_key)
    result = await seed_zscore_history(get_pool())
    return JSONResponse(content=result)


@app.get("/checkpoint")
async def checkpoint_status(x_api_key: str | None = None):
    _auth(x_api_key)
    return backfill_progress.to_dict()
