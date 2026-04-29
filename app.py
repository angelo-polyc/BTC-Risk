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
from backfill import main as run_backfill

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

@app.get("/debug/liq")
async def debug_liq(x_api_key: str | None = None):
    """Debug: step each of the 4 history calls individually to isolate the failure."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    import traceback
    from sources import SourceAPI
    from datetime import datetime, timezone
    result: dict = {}
    async with SourceAPI() as api:
        await api.prep_run()
        end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = end_ms - 31 * 24 * 3600 * 1000
        for name, coro in [
            ("oi_hist",  api._cglass_oi_history("XRP", start_ms, end_ms)),
            ("fr_hist",  api._cglass_funding_history("XRP", start_ms, end_ms)),
            ("vol_hist", api._cglass_volume_history("XRP", start_ms, end_ms)),
            ("liq_hist", api._cglass_liq_history("XRP", start_ms, end_ms)),
        ]:
            try:
                data = await coro
                result[name] = f"{len(data)} rows; sample={data[:1]}"
            except Exception as exc:
                result[name] = f"ERROR: {type(exc).__name__}: {exc}"
        # Also run derivs_history_30d and capture any exception
        try:
            rows = await api.derivs_history_30d("XRP")
            result["derivs_rows"] = len(rows)
            result["derivs_sample"] = [(str(d), oi, fapr, pvol, liqr) for d, oi, fapr, pvol, liqr in rows[:2]]
        except Exception:
            result["derivs_exception"] = traceback.format_exc()
    return result

@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None):
    """One-shot 30d history seeder. Run once after first deploy."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    asyncio.create_task(run_backfill())
    return {"status": "started — backfill runs in background, takes 5-10 min"}
