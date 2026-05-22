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

@app.get("/status")
async def status(x_api_key: str | None = None):
    """Lightweight status — as_of + counts only, no data payload."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not DATA_FILE.exists():
        return {"as_of": None, "tokens": 0, "oi": 0, "tvl": 0}
    d = json.loads(DATA_FILE.read_text())
    u = d.get("universe", [])
    return {
        "as_of": d.get("as_of"),
        "tokens": len(u),
        "oi": sum(1 for t in u if t["metrics"].get("oi")),
        "tvl": sum(1 for t in u if t["metrics"].get("tvl")),
        "funding": sum(1 for t in u if t["metrics"].get("funding_apr") is not None),
    }

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

@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None):
    """One-shot 30d history seeder. Run once after first deploy."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    asyncio.create_task(run_backfill())
    return {"status": "started — backfill runs in background, takes 5-10 min"}

@app.get("/debug/exchanges")
async def debug_exchanges(x_api_key: str | None = None):
    """Diagnose DEX exchange ID fetching — calls /exchanges live and reports results."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    from sources import SourceAPI
    result = {"pages": [], "dex_count": 0, "error": None, "sample_dex_ids": []}
    async with SourceAPI() as api:
        try:
            for page in range(1, 6):
                try:
                    r = await api._cg_get("/exchanges", params={"per_page": 250, "page": page})
                    dex_on_page = [e["id"] for e in r if e.get("id") and e.get("country") is None]
                    result["pages"].append({"page": page, "total": len(r), "dex_count": len(dex_on_page)})
                    result["dex_count"] += len(dex_on_page)
                    if page == 1:
                        result["sample_dex_ids"] = dex_on_page[:10]
                    if len(r) < 250:
                        break
                except Exception as e:
                    result["error"] = f"page {page}: {type(e).__name__}: {e}"
                    break
        except Exception as e:
            result["error"] = f"outer: {type(e).__name__}: {e}"
    # Also test a single tickers call for LINK
    async with SourceAPI() as api:
        try:
            r = await api._cg_get("/coins/chainlink/tickers",
                                   params={"depth": "false", "include_exchange_logo": "false"})
            tickers = r.get("tickers", [])
            result["link_tickers_count"] = len(tickers)
            result["link_sample"] = [
                {"name": (t.get("market") or {}).get("name"), "id": (t.get("market") or {}).get("identifier"),
                 "vol": (t.get("converted_volume") or {}).get("usd")}
                for t in tickers[:5]
            ]
        except Exception as e:
            result["link_tickers_error"] = f"{type(e).__name__}: {e}"
    return result
