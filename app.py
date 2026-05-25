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

DATA_DIR  = Path(os.environ.get("DATA_DIR", "/data"))
SCORES    = DATA_DIR / "scores.json"
API_KEY   = os.environ.get("READ_API_KEY")

scheduler = AsyncIOScheduler(timezone="UTC")


def _auth(key: str | None) -> None:
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    scheduler.add_job(run_ingest, CronTrigger(hour=2,  minute=0),  id="dubai_open")
    scheduler.add_job(run_ingest, CronTrigger(hour=13, minute=35), id="ny_open")
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


@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None):
    _auth(x_api_key)
    asyncio.create_task(run_backfill())
    return {"status": "started — backfill runs in background, takes 10-20 min"}
