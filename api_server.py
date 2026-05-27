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

import json
import os
import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

import data_access as da

# ─── Auth ─────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=True)
_basic = HTTPBasic(auto_error=True)


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


def _verify_dashboard_pw(
    creds: Annotated[HTTPBasicCredentials, Depends(_basic)],
) -> None:
    """Gate /dashboard behind BTC_DASHBOARD_PASSWORD. The browser's built-in
    Basic-Auth prompt is lower friction than a login form, and the dashboard
    is single-user personal infra — the password is separate from
    BTC_API_TOKEN so it can be rotated independently. If the env var is
    unset we refuse rather than serve anonymously."""
    expected = os.environ.get("BTC_DASHBOARD_PASSWORD", "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BTC_DASHBOARD_PASSWORD is unset on the server",
        )
    if not secrets.compare_digest(creds.password, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid password",
            headers={"WWW-Authenticate": 'Basic realm="BTC Dashboard"'},
        )


# ─── App ──────────────────────────────────────────────────────────────────────

# Import MCP app early so its lifespan can be bridged into FastAPI's.
from mcp_server import mcp as _mcp  # noqa: E402

from contextlib import asynccontextmanager  # noqa: E402


@asynccontextmanager
async def _lifespan(app):
    # Fail fast if unset; no point serving unauthenticated.
    _expected_token()
    # Run the MCP streamable-http session manager so mounted /mcp works.
    async with _mcp.session_manager.run():
        yield


app = FastAPI(
    title="BTC Drawdown Model API",
    description=(
        "Read-only access to the BTC drawdown-probability model's canonical "
        "state, hypothesis detail, walk-forward weights, drift monitor, "
        "shadow state, and raw inputs. Protected by bearer token."
    ),
    version="1.0",
    lifespan=_lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


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


# ─── Browser dashboard ────────────────────────────────────────────────────────
#
# Serves dashboard.html from disk, substituting the bearer token into the
# placeholder so the browser JS can then call the /today, /history etc.
# endpoints over the same bearer-auth channel the MCP server and agent
# clients use. Access is gated behind Basic Auth (BTC_DASHBOARD_PASSWORD);
# the bearer token remains visible in the page source to anyone who auths,
# which is the right trust boundary for single-user personal infra.

_DASHBOARD_HTML_PATH = Path(__file__).parent / "dashboard.html"


@app.get("/dashboard", response_class=HTMLResponse,
         dependencies=[Depends(_verify_dashboard_pw)])
def dashboard() -> HTMLResponse:
    if not _DASHBOARD_HTML_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="dashboard.html not found next to api_server.py",
        )
    html = _DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    token = _expected_token()
    # json.dumps yields a quoted JS string literal with any special chars
    # (backslashes, quotes, control bytes) safely escaped. The template
    # placeholder is the quoted form "{{BTC_API_TOKEN}}" — we replace the
    # whole quoted form so the output is valid JS regardless of token
    # shape.
    html = html.replace('"{{BTC_API_TOKEN}}"', json.dumps(token))
    return HTMLResponse(content=html)


# ─── Mount MCP server at /mcp ─────────────────────────────────────────────────
#
# The MCP Streamable HTTP app is defined in mcp_server.py. Importing it here
# reuses the tool definitions and the bearer-auth middleware so one uvicorn
# process serves both REST and MCP behind a single public URL and a single
# bearer token. This is the production topology — mcp_server.py can still
# be run standalone for local development.

from mcp_server import app as _mcp_app  # noqa: E402

app.mount("/mcp", _mcp_app)


# ─── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api_server:app",
        host=os.environ.get("BTC_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("BTC_API_PORT", "8787")),
        log_level="info",
    )
