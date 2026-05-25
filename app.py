"""FastAPI app — serves the divergence JSON and runs the ingest cron in-process."""
import os
import json
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ingest import run_ingest
from backfill import main as run_backfill, backfill_symbols, progress as backfill_progress

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
DATA_FILE = DATA_DIR / "divergence.json"

# Keep strong references to background tasks to prevent GC
_background_tasks: set = set()
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://btc-risk.up.railway.app"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"

@app.get("/logs")
async def get_logs(x_api_key: str | None = None):
    """Read backfill log file."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    for log_file in (DATA_DIR / "backfill.log", Path("/tmp/backfill.log")):
        if log_file.exists():
            return PlainTextResponse(log_file.read_text()[-5000:])
    return PlainTextResponse("no log file yet")

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
    task = asyncio.create_task(run_ingest())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"status": "started"}

@app.get("/checkpoint")
async def checkpoint_status(x_api_key: str | None = None):
    """Live backfill progress — reads in-memory Progress object."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    return backfill_progress.to_dict()

@app.post("/backfill")
async def manual_backfill(x_api_key: str | None = None, symbol: str | None = None):
    """Seed 30d history. Optional ?symbol=BTC to backfill a single token."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    if symbol:
        task = asyncio.create_task(backfill_symbols([symbol.upper()]))
    else:
        task = asyncio.create_task(run_backfill())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    if symbol:
        return {"status": f"started — backfilling {symbol.upper()}"}
    return {"status": "started — backfill runs in background, takes 5-10 min"}

@app.post("/clear")
async def clear_data(x_api_key: str | None = None, symbols: str = "all",
                     metrics: str = "oi,funding_apr,perp_vol,liq_oi_ratio"):
    """Wipe metric series for specified tokens.
    ?symbols=all or comma-separated symbols (e.g. BTC,ETH)
    ?metrics=comma-separated metric names (default: all derivs)
    """
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not DATA_FILE.exists():
        raise HTTPException(status_code=404, detail="no data file")

    metric_list = [m.strip() for m in metrics.split(",")]
    sym_filter = None if symbols == "all" else {s.strip().upper() for s in symbols.split(",")}

    d = json.loads(DATA_FILE.read_text())
    cleared = 0
    for t in d.get("universe", []):
        if sym_filter and t["symbol"].upper() not in sym_filter:
            continue
        for m in metric_list:
            if m in t["metrics"]:
                t["metrics"][m] = []
        cleared += 1

    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, separators=(",", ":")))
    tmp.replace(DATA_FILE)
    return {"cleared": cleared, "metrics": metric_list, "symbols": symbols}

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
