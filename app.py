"""FastAPI app — serves momentum scores and runs ingest cron 2x/day."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from ingest import run_ingest
from backfill import main as run_backfill
from scorer import load_history

DATA_DIR  = Path(os.environ.get("DATA_DIR", "/data"))
SCORES    = DATA_DIR / "scores.json"
API_KEY   = os.environ.get("READ_API_KEY")

scheduler = AsyncIOScheduler(timezone="America/New_York")
_pipeline_lock = asyncio.Lock()  # prevents backfill and ingest from running simultaneously


async def _safe_ingest():
    if _pipeline_lock.locked():
        print("[app] ingest skipped — pipeline already running")
        return
    async with _pipeline_lock:
        await run_ingest()


async def _safe_backfill():
    async with _pipeline_lock:
        await run_backfill()


def _auth(key: str | None) -> None:
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    scheduler.add_job(_safe_ingest, CronTrigger(hour=6,  minute=0),  id="ny_morning")
    scheduler.add_job(_safe_ingest, CronTrigger(hour=18, minute=0),  id="ny_evening")
    scheduler.start()
    print("[startup] scheduler started:", [j.id for j in scheduler.get_jobs()])
    yield
    scheduler.shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/scores")
async def scores(x_api_key: str | None = None):
    _auth(x_api_key)
    if not SCORES.exists():
        raise HTTPException(status_code=503, detail="no data yet — run POST /backfill")
    return JSONResponse(content=json.loads(SCORES.read_text()))


@app.get("/status")
async def status(x_api_key: str | None = None):
    _auth(x_api_key)
    if not SCORES.exists():
        return {"status": "no_data"}
    data = json.loads(SCORES.read_text())
    return {
        "as_of":     data.get("as_of"),
        "regime":    data.get("regime"),
        "gate_on":   data.get("gate_on"),
        "btc_price": data.get("btc_price"),
        "btc_ma200": data.get("btc_ma200"),
        "n_tokens":  data.get("n_tokens"),
    }


@app.post("/ingest")
async def manual_ingest(x_api_key: str | None = None):
    _auth(x_api_key)
    asyncio.create_task(run_ingest())
    return {"status": "started"}


@app.get("/scores/history")
async def scores_history(x_api_key: str | None = None, days: int = 90):
    """Rolling rank_pct history. Returns dates × tokens matrix."""
    _auth(x_api_key)
    hist = load_history(DATA_DIR, days=min(days, 365))
    if hist is None or hist.empty:
        raise HTTPException(status_code=503, detail="no history yet — run POST /backfill")
    return {
        "dates":     [str(d.date()) for d in hist.index],
        "tokens":    list(hist.columns),
        "rank_pcts": [[round(v, 4) if v == v else None for v in row]
                      for row in hist.values.tolist()],
    }


@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None):
    _auth(x_api_key)
    asyncio.create_task(_safe_backfill())
    return {"status": "started — backfill runs in background, takes 10-20 min"}


@app.get("/prices")
async def prices(x_api_key: str | None = None, days: int = 430):
    """Return spot_prices parquet as JSON for downstream analysis."""
    _auth(x_api_key)
    import pandas as pd
    p = DATA_DIR / "spot_prices.parquet"
    if not p.exists():
        raise HTTPException(status_code=503, detail="no price data")
    df = pd.read_parquet(p).tail(days)
    return {
        "dates":   [str(d.date()) for d in df.index],
        "tokens":  list(df.columns),
        "prices":  [[None if v != v else round(float(v), 6) for v in row]
                    for row in df.values.tolist()],
    }


@app.get("/debug")
async def debug(x_api_key: str | None = None):
    """Reports parquet shapes, CG map size, and last log lines."""
    _auth(x_api_key)
    import pandas as pd
    from sources import _CG_ID_MAP
    result = {"cg_id_map_size": len(_CG_ID_MAP)}
    for name in ["spot_prices", "taker_buy", "taker_sell", "funding"]:
        p = DATA_DIR / f"{name}.parquet"
        if p.exists():
            df = pd.read_parquet(p)
            result[name] = {"tokens": df.shape[1], "days": df.shape[0],
                            "first": str(df.index[0].date()), "last": str(df.index[-1].date())}
        else:
            result[name] = "missing"
    return result
