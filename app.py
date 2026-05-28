"""FastAPI app — serves momentum scores and runs ingest cron 2x/day."""
import asyncio
import json
import os
from contextlib import asynccontextmanager

import asyncpg
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse

import db
from ingest import run_ingest
from backfill import main as run_backfill_main
from sources import _CG_ID_MAP as CG_IDS

API_KEY = os.environ.get("READ_API_KEY")

scheduler = AsyncIOScheduler(timezone="America/New_York")

_pool: asyncpg.Pool | None = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised")
    return _pool


async def _safe_ingest():
    if _pool is None:
        print("[ingest] skipped — no DB pool")
        return
    await run_ingest(_pool)


async def _safe_backfill():
    if _pool is None:
        print("[backfill] skipped — no DB pool")
        return
    await run_backfill_main(_pool)


def _auth(key: str | None) -> None:
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


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

    scheduler.add_job(_safe_ingest, CronTrigger(hour=6,  minute=0), id="ny_morning")
    scheduler.add_job(_safe_ingest, CronTrigger(hour=18, minute=0), id="ny_evening")
    scheduler.start()
    print("[startup] scheduler started:", [j.id for j in scheduler.get_jobs()])
    yield
    scheduler.shutdown()
    if _pool:
        await _pool.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://btc-risk.up.railway.app"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/scores")
async def scores(x_api_key: str | None = None):
    _auth(x_api_key)
    if _pool is None:
        raise HTTPException(status_code=503, detail="no DB pool")
    scores_list, regime = await db.get_all_scores(_pool)
    if not regime:
        raise HTTPException(status_code=503, detail="no data yet — run POST /backfill")
    return JSONResponse({
        "as_of":     regime["as_of"],
        "regime":    regime["regime"],
        "gate_on":   regime["gate_on"],
        "btc_price": regime["btc_price"],
        "btc_ma200": regime["btc_ma200"],
        "n_tokens":  len(scores_list),
        "scores":    scores_list,
    })


@app.get("/status")
async def status(x_api_key: str | None = None):
    _auth(x_api_key)
    if _pool is None:
        return {"status": "no_db"}
    regime = await db.get_regime(_pool)
    if not regime:
        return {"status": "no_data"}
    return {
        "as_of":     regime.get("as_of"),
        "regime":    regime.get("regime"),
        "gate_on":   regime.get("gate_on"),
        "btc_price": regime.get("btc_price"),
        "btc_ma200": regime.get("btc_ma200"),
        "n_tokens":  regime.get("n_tokens"),
    }


@app.post("/ingest")
async def manual_ingest(x_api_key: str | None = None):
    _auth(x_api_key)
    asyncio.create_task(_safe_ingest())
    return {"status": "started"}


@app.get("/scores/history")
async def scores_history(x_api_key: str | None = None, days: int = 90):
    """Rolling rank_pct history. Returns dates × tokens matrix."""
    _auth(x_api_key)
    if _pool is None:
        raise HTTPException(status_code=503, detail="no DB pool")
    hist = await db.get_scores_history(_pool, days=min(days, 365))
    if not hist["dates"]:
        raise HTTPException(status_code=503, detail="no history yet — run POST /backfill")
    return hist


@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None):
    _auth(x_api_key)
    asyncio.create_task(_safe_backfill())
    return {"status": "started — backfill runs in background, takes 10-20 min"}


@app.get("/prices")
async def prices(x_api_key: str | None = None, days: int = 430):
    """Return price panel from DB as JSON for downstream analysis."""
    _auth(x_api_key)
    if _pool is None:
        raise HTTPException(status_code=503, detail="no DB pool")
    data = await db.get_raw_panel(_pool, "price", days)
    if not data:
        raise HTTPException(status_code=503, detail="no price data")

    # Collect all dates across all tokens, sort, then build matrix
    all_dates: set[str] = set()
    for points in data.values():
        for p in points:
            all_dates.add(p["d"])
    date_list = sorted(all_dates)
    tokens    = sorted(data.keys())

    # Build lookup: {(symbol, date): value}
    lookup: dict[tuple[str, str], float] = {}
    for sym, points in data.items():
        for p in points:
            lookup[(sym, p["d"])] = p["v"]

    price_matrix = [
        [None if (t, d) not in lookup else round(float(lookup[(t, d)]), 6)
         for t in tokens]
        for d in date_list
    ]

    return {
        "dates":  date_list,
        "tokens": tokens,
        "prices": price_matrix,
    }


@app.get("/signal_history")
async def signal_history(symbol: str, days: int = 90, x_api_key: str | None = None):
    _auth(x_api_key)
    import pandas as pd, numpy as np

    fetch_days = days + 75

    async def panel(name: str) -> "pd.Series":
        raw = await db.get_raw_panel(_pool, name, fetch_days)
        pts = raw.get(symbol.upper(), [])
        if not pts:
            return pd.Series(dtype=float)
        s = pd.Series({p["d"]: float(p["v"]) for p in pts})
        s.index = pd.DatetimeIndex(s.index)
        return s.sort_index()

    price = await panel("price")
    buy   = await panel("taker_buy")
    sell  = await panel("taker_sell")
    fund  = await panel("funding")

    cutoff = pd.Timestamp.now().normalize() - pd.Timedelta(days=days - 1)

    def ts(s: "pd.Series") -> "pd.Series":
        return s[s.index >= cutoff] if not s.empty else s

    def fmt(s: "pd.Series") -> list:
        return [round(float(v), 5) if pd.notna(v) else None for v in s]

    # CVD ts-z
    cvd_tsz = pd.Series(dtype=float)
    cvd_7d  = pd.Series(dtype=float)
    if not buy.empty and not sell.empty:
        net     = (buy - sell).fillna(0)
        c14     = net.rolling(14, min_periods=7).sum()
        cvd_tsz = (c14 - c14.rolling(60, min_periods=30).mean()) / c14.rolling(60, min_periods=30).std().replace(0, np.nan)
        cvd_7d  = net.rolling(7, min_periods=4).sum()

    # Funding ts-z
    fund_tsz = pd.Series(dtype=float)
    if not fund.empty:
        fund_tsz = (fund - fund.rolling(60, min_periods=30).mean()) / fund.rolling(60, min_periods=30).std().replace(0, np.nan)

    # Raw 14d return
    raw14 = pd.Series(dtype=float)
    if not price.empty:
        raw14 = price / price.shift(14) - 1

    # Align all to a common date index
    all_series = [ts(cvd_tsz), ts(cvd_7d), ts(fund_tsz), ts(raw14)]
    idx = sorted(set().union(*[set(s.index) for s in all_series if not s.empty]))
    idx = pd.DatetimeIndex(idx)

    def aligned(s: "pd.Series") -> list:
        return fmt(s.reindex(idx)) if not s.empty else [None] * len(idx)

    return JSONResponse(content={
        "symbol":     symbol.upper(),
        "dates":      [str(d.date()) for d in idx],
        "cvd_tsz":    aligned(ts(cvd_tsz)),
        "cvd_7d_sum": aligned(ts(cvd_7d)),
        "fund_tsz":   aligned(ts(fund_tsz)),
        "raw_14d":    aligned(ts(raw14)),
    })


@app.get("/debug")
async def debug(x_api_key: str | None = None):
    """Reports DB counts and CG map size."""
    _auth(x_api_key)
    if _pool is None:
        return {"error": "no DB pool"}
    async with _pool.acquire() as conn:
        counts = {}
        for panel in ["price", "taker_buy", "taker_sell", "funding", "ls_global"]:
            counts[panel] = await conn.fetchval(
                "SELECT COUNT(DISTINCT symbol) FROM mom_raw_series WHERE panel=$1", panel
            )
        n_scores = await conn.fetchval("SELECT COUNT(*) FROM mom_scores")
        n_hist   = await conn.fetchval("SELECT COUNT(DISTINCT date) FROM mom_scores_history")
    return {
        "cg_id_map_size": len(CG_IDS),
        "panels":         counts,
        "n_scored":       n_scores,
        "history_dates":  n_hist,
    }
