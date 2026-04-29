"""FastAPI app — serves the divergence JSON and runs the ingest cron in-process."""
import os
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ingest import run_ingest

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_FILE = DATA_DIR / "divergence.json"
API_KEY = os.environ.get("READ_API_KEY")

scheduler = AsyncIOScheduler(timezone="UTC")

@asynccontextmanager
async def lifespan(app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    # 02:00 UTC = 06:00 Dubai
    scheduler.add_job(run_ingest, CronTrigger(hour=2, minute=0), id="dubai_open")
    # 13:35 UTC = 09:35 NY EDT (accepts 1hr drift in EST winter)
    scheduler.add_job(run_ingest, CronTrigger(hour=13, minute=35), id="ny_open")
    scheduler.start()
    print("[startup] scheduler started; jobs:", [j.id for j in scheduler.get_jobs()])
    yield
    scheduler.shutdown()

app = FastAPI(lifespan=lifespan)

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

@app.get("/divergence.json")
async def divergence(x_api_key: str | None = None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not DATA_FILE.exists():
        raise HTTPException(status_code=503, detail="no data yet — run POST /ingest or wait for first cron")
    return JSONResponse(content=json.loads(DATA_FILE.read_text()))

@app.post("/ingest")
async def manual_ingest(x_api_key: str | None = None):
    """Manual trigger — useful for first deploy and debugging."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    asyncio.create_task(run_ingest())
    return {"status": "started"}
