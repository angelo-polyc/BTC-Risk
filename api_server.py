"""FastAPI REST API for the BTC drawdown-probability model.

Read-only. Bearer-token auth (Authorization: Bearer <token>). Serves the
full committed CSV history from day 1 — no incremental log, no backfill
needed. When the daily pipeline (daily_pipeline.py) rewrites the CSVs,
the data_access layer picks up the new mtime on the next request.

Config (all via env vars, no model knobs):
  BTC_API_TOKEN     — bearer token required on every request (no default;
                      server refuses to start if unset).
  BTC_API_HOST      — bind host, default 0.0.0.0
  BTC_API_PORT      — bind port, default 8787
  BTC_MODEL_DIR     — project root with CSVs, default cwd

Run:
  export BTC_API_TOKEN=<your-token>
  export BTC_MODEL_DIR=/home/runner/btc_model
  python3 api_server.py

Or under supervisord/systemd/Replit's "always on" runner:
  python3 -m uvicorn api_server:app --host 0.0.0.0 --port 8787

OpenAPI docs at /docs once authorized.
"""
from __future__ import annotations

import os
import secrets
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import data_access as da

# ─── Auth ─────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)


def _expected_token() -> str:
    tok = os.environ.get("BTC_API_TOKEN", "")
    if not tok:
        # Refuse to serve. The main guard calls this at startup too.
        raise RuntimeError(
            "BTC_API_TOKEN is unset. Set a bearer token in the environment "
            "before starting the API server."
        )
    return tok


def require_token(
    creds: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> None:
    expected = _expected_token()
    if not secrets.compare_digest(creds.credentials, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="invalid bearer token")


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="BTC Drawdown Model API",
    description=(
        "Read-only access to the BTC drawdown-probability model's canonical "
        "state, hypothesis detail, walk-forward weights, drift monitor, "
        "shadow state, and raw inputs. Protected by bearer token."
    ),
    version="1.0",
)


@app.on_event("startup")
def _check_token_on_startup() -> None:
    # Fail fast if unset; no point serving unauthenticated.
    _expected_token()


# ─── Model state ──────────────────────────────────────────────────────────────

@app.get("/today", dependencies=[Depends(require_token)])
def today(
    variant: str = Query("wf365", regex="^(wf365|sf730)$"),
) -> dict[str, Any]:
    """Latest row of master_daily_view for the given variant.

    Includes regime, ensemble_score, percentile, position, the five
    hypothesis scores, btc_return, and strategy_return.
    """
    row = da.get_today(variant)
    if not row:
        raise HTTPException(status_code=404, detail="no rows available")
    return row


@app.get("/history", dependencies=[Depends(require_token)])
def history(
    from_date: str | None = Query(None, alias="from",
                                  description="YYYY-MM-DD, inclusive"),
    to_date: str | None = Query(None, alias="to",
                                description="YYYY-MM-DD, inclusive"),
    variant: str = Query("wf365", regex="^(wf365|sf730)$"),
) -> dict[str, Any]:
    rows = da.get_history(from_date, to_date, variant)
    return {"variant": variant, "count": len(rows), "rows": rows}


# ─── Hypothesis detail ────────────────────────────────────────────────────────

@app.get("/hypotheses", dependencies=[Depends(require_token)])
def hypotheses() -> dict[str, Any]:
    """List of hypothesis names available at /hypothesis/{name}.

    Note: `eth` is reference-only since v12 — computed and exposed for
    drift monitoring but does NOT feed ensemble_score. The production
    ensemble uses the other five.
    """
    return {
        "hypotheses": da.list_hypotheses(),
        "in_ensemble": ["macro_equities", "cme", "crypto_derivatives",
                        "classic_cycle", "etf_flows"],
        "reference_only": ["eth"],
    }


@app.get("/hypothesis/{name}", dependencies=[Depends(require_token)])
def hypothesis(
    name: str,
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    try:
        rows = da.get_hypothesis(name, from_date, to_date)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"name": name, "count": len(rows), "rows": rows}


# ─── Weights ──────────────────────────────────────────────────────────────────

@app.get("/weights", dependencies=[Depends(require_token)])
def weights() -> dict[str, Any]:
    """Current regime × hypothesis weight matrix. 12 rows (2 variants ×
    1 label × 3 regimes + wf365 default row; see weights.csv schema)."""
    return {"rows": da.get_weights()}


@app.get("/weight_history", dependencies=[Depends(require_token)])
def weight_history(
    variant: str = Query("wf365", regex="^(wf365|sf730)$"),
    label: str = Query("y_60", regex="^(y_30|y_60)$"),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    try:
        rows = da.get_weight_history(variant, label, from_date, to_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"variant": variant, "label": label, "count": len(rows), "rows": rows}


# ─── Health / drift monitor ───────────────────────────────────────────────────

@app.get("/health", dependencies=[Depends(require_token)])
def health() -> dict[str, Any]:
    """Latest drift-monitor report (42 rows: 6 composites + 36 sub-signals)."""
    rows = da.get_health()
    flagged = [r for r in rows if r.get("flagged") in (True, "True", "true", 1)]
    return {"count": len(rows), "flagged_count": len(flagged), "rows": rows}


@app.get("/flags", dependencies=[Depends(require_token)])
def flags() -> dict[str, Any]:
    """Only currently-flagged signals from the latest health_check run."""
    rows = da.get_health_flags()
    return {"count": len(rows), "rows": rows}


@app.get("/health_history", dependencies=[Depends(require_token)])
def health_history(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
    extended: bool = Query(False, description="use extended history file if true"),
) -> dict[str, Any]:
    rows = da.get_health_history(from_date, to_date, extended=extended)
    return {"extended": extended, "count": len(rows), "rows": rows}


@app.get("/shadow", dependencies=[Depends(require_token)])
def shadow(
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    rows = da.get_shadow_state(from_date, to_date)
    return {"count": len(rows), "rows": rows}


# ─── Raw data ─────────────────────────────────────────────────────────────────

@app.get("/raw/columns", dependencies=[Depends(require_token)])
def raw_columns() -> dict[str, Any]:
    """Metadata for raw_data_export.csv: total columns, row count, date range,
    prefix groupings, and the full column list. Use this to pick columns for
    the /raw endpoint."""
    return da.list_raw_columns()


@app.get("/raw", dependencies=[Depends(require_token)])
def raw(
    columns: str = Query(..., description="comma-separated column names (required)"),
    from_date: str | None = Query(None, alias="from"),
    to_date: str | None = Query(None, alias="to"),
) -> dict[str, Any]:
    """Range-limited slice of raw_data_export.csv. Columns are required
    (the full 162-column × 26K-row file is too large to return whole)."""
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    try:
        rows = da.get_raw_data(cols, from_date, to_date)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"columns": cols, "count": len(rows), "rows": rows}


# ─── Metadata ─────────────────────────────────────────────────────────────────

@app.get("/manifest", dependencies=[Depends(require_token)])
def manifest() -> dict[str, Any]:
    """Export manifest — SHA-256 fingerprints tying the CSVs to the pipeline
    run that produced them. Use `generated_at` to check data freshness."""
    return da.get_manifest()


@app.get("/thresholds", dependencies=[Depends(require_token)])
def thresholds() -> dict[str, Any]:
    return {"rows": da.get_thresholds()}


@app.get("/data_inventory", dependencies=[Depends(require_token)])
def data_inventory() -> dict[str, Any]:
    """Which input series the pipeline pulls, with coverage windows and row counts."""
    return {"rows": da.get_data_inventory()}


@app.get("/pinning_audit", dependencies=[Depends(require_token)])
def pinning_audit() -> dict[str, Any]:
    return {"rows": da.get_pinning_audit()}


@app.get("/status", dependencies=[Depends(require_token)])
def status_endpoint() -> dict[str, Any]:
    """Lightweight freshness check: latest date + file mtimes for each CSV.
    Useful as the first thing an agent calls to ground its reasoning."""
    return da.get_status()


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host=os.environ.get("BTC_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("BTC_API_PORT", "8787")),
        log_level="info",
    )
