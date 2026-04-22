"""MCP server for the BTC drawdown-probability model.

Exposes the same data-access surface as api_server.py, re-shaped as MCP
tools for Claude Code (and other MCP-speaking agents).

Transport: Streamable HTTP (default MCP transport for networked servers).
Auth: shared bearer token (same env var as the REST API —
  BTC_API_TOKEN — so one secret covers both layers).

The tool set mirrors the REST endpoints but uses semantic names and
structured parameters that an agent can discover and reason over:
  * get_status            — smoke / freshness check, call first
  * get_today             — latest model call (regime, percentile, position)
  * get_history           — master-view range query
  * list_hypotheses       — names of the six hypothesis groups
  * get_hypothesis        — per-hypothesis score + sub-signals over a range
  * get_weights           — current regime × hypothesis matrix
  * get_weight_history    — walk-forward refit history
  * get_health            — latest drift-monitor report
  * get_flags             — only signals currently flagged by the monitor
  * get_health_history    — replayed historical drift reports
  * get_shadow_state      — counterfactual positions under alt decision rules
  * list_raw_columns      — metadata for the raw input series
  * get_raw_data          — column-selected slice of raw inputs
  * get_manifest          — data-freshness fingerprint
  * get_thresholds        — per-variant position thresholds
  * get_data_inventory    — input coverage windows
  * get_pinning_audit     — pinning audit findings

Run:
  export BTC_API_TOKEN=<your-token>
  export BTC_MODEL_DIR=/home/runner/btc_model
  python3 mcp_server.py

Default binds 0.0.0.0:8788 with Streamable HTTP at /mcp.
"""
from __future__ import annotations

import os
import secrets
from typing import Any

import uvicorn
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

import data_access as da

# ─── Auth middleware ──────────────────────────────────────────────────────────

class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Reject any request without `Authorization: Bearer <BTC_API_TOKEN>`.

    This is intentionally simpler than the FastMCP OAuth flow. The BTC model
    is a single-user ops layer; a shared bearer is the right trust model.
    """

    async def dispatch(self, request: Request, call_next):
        expected = os.environ.get("BTC_API_TOKEN", "")
        if not expected:
            return JSONResponse(
                {"error": "server misconfigured: BTC_API_TOKEN unset"},
                status_code=500,
            )
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return JSONResponse({"error": "missing bearer token"}, status_code=401)
        presented = auth.split(None, 1)[1].strip()
        if not secrets.compare_digest(presented, expected):
            return JSONResponse({"error": "invalid bearer token"}, status_code=401)
        return await call_next(request)


# ─── MCP server + tools ───────────────────────────────────────────────────────

mcp = FastMCP(
    "btc-drawdown-model",
    streamable_http_path="/",  # so the app can be mounted at any prefix
    instructions=(
        "Read-only access to the BTC drawdown-probability model. The model "
        "produces a daily long-BTC position (0-100%) based on five hypothesis "
        "composites and a regime classifier (bull/neutral/bear). Call "
        "get_status first to learn what data is available. Use get_today and "
        "get_flags for the current state, get_history and get_hypothesis for "
        "drill-down. All rows carry full history from 2014 onward — date "
        "filters are optional."
    ),
)


@mcp.tool()
def get_status() -> dict[str, Any]:
    """Lightweight freshness check. Returns latest model date, regime,
    position, and file mtimes. Call this first to ground reasoning."""
    return da.get_status()


@mcp.tool()
def get_today(variant: str = "wf365") -> dict[str, Any]:
    """Latest row of the canonical daily view.

    Args:
        variant: "wf365" (canonical walk-forward) or "sf730" (reference).
    """
    return da.get_today(variant)


@mcp.tool()
def get_history(
    from_date: str | None = None,
    to_date: str | None = None,
    variant: str = "wf365",
) -> list[dict[str, Any]]:
    """Range query on master_daily_view. Each row contains regime, ensemble
    score, percentile, position, five hypothesis scores, btc_return, and
    strategy_return.

    Args:
        from_date: inclusive lower bound (YYYY-MM-DD) or None for open start.
        to_date: inclusive upper bound or None for open end.
        variant: "wf365" or "sf730".
    """
    return da.get_history(from_date, to_date, variant)


@mcp.tool()
def list_hypotheses() -> dict[str, Any]:
    """The six hypothesis names. `eth` is reference-only since v12 and does
    not feed ensemble_score; the other five do."""
    return {
        "all": da.list_hypotheses(),
        "in_ensemble": ["macro_equities", "cme", "crypto_derivatives",
                        "classic_cycle", "etf_flows"],
        "reference_only": ["eth"],
    }


@mcp.tool()
def get_hypothesis(
    name: str,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Per-hypothesis composite score and sub-signal ranks over a date range.

    Args:
        name: one of macro_equities, cme, crypto_derivatives, classic_cycle,
              etf_flows, eth.
        from_date: inclusive lower bound or None.
        to_date: inclusive upper bound or None.
    """
    return da.get_hypothesis(name, from_date, to_date)


@mcp.tool()
def get_weights() -> list[dict[str, Any]]:
    """Current regime × hypothesis weight matrix (both variants, both labels)."""
    return da.get_weights()


@mcp.tool()
def get_weight_history(
    variant: str = "wf365",
    label: str = "y_60",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Monthly walk-forward weight history — how the ensemble has re-weighted
    the five hypotheses over time per regime.

    Args:
        variant: "wf365" or "sf730".
        label: "y_60" (canonical) or "y_30".
        from_date: inclusive lower bound on fit_date, or None.
        to_date: inclusive upper bound on fit_date, or None.
    """
    return da.get_weight_history(variant, label, from_date, to_date)


@mcp.tool()
def get_health() -> list[dict[str, Any]]:
    """Full drift-monitor report: per-signal in-sample, hold-out, and rolling
    AUCs against y_60, plus flag status. 42 rows (6 composites + 36 sub-signals).
    Regenerated whenever the pipeline runs."""
    return da.get_health()


@mcp.tool()
def get_flags() -> list[dict[str, Any]]:
    """Only currently-flagged signals from the latest health_check run. A
    signal flags if its hold-out or rolling AUC <= 0.50, or if in-sample minus
    out-of-sample AUC > 0.15."""
    return da.get_health_flags()


@mcp.tool()
def get_health_history(
    from_date: str | None = None,
    to_date: str | None = None,
    extended: bool = False,
) -> list[dict[str, Any]]:
    """Replayed historical drift-monitor reports. Use this to inspect how
    AUC drift trended over time — which signals have been consistently
    suspect vs recently degraded.

    Args:
        from_date: inclusive lower bound on replay_date, or None.
        to_date: inclusive upper bound on replay_date, or None.
        extended: if true, use the longer history file.
    """
    return da.get_health_history(from_date, to_date, extended=extended)


@mcp.tool()
def get_shadow_state(
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Shadow-tracker output: counterfactual positions under baseline,
    naive-drop, persist-2, and persist-3 monitor decision rules at both
    production and wide tapers. Reporting-only — not in production.

    Args:
        from_date, to_date: optional inclusive date bounds.
    """
    return da.get_shadow_state(from_date, to_date)


@mcp.tool()
def list_raw_columns() -> dict[str, Any]:
    """Metadata for raw_data_export.csv: column names grouped by source
    (price, fred, cftc, coinglass_cycle, coinglass_h2, coinglass_h3,
    velo_btc, velo_eth), date range, and row count."""
    return da.list_raw_columns()


@mcp.tool()
def get_raw_data(
    columns: list[str],
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Column-selected, range-limited slice of raw_data_export.csv. Always
    specify columns — the full file is 26,470 rows × 161 cols and too
    large to return whole. Use list_raw_columns first to discover names.

    Args:
        columns: list of column names (e.g. ["price__btc_ohlc__close",
                 "fred__DFF__value"]).
        from_date, to_date: optional inclusive date bounds.
    """
    return da.get_raw_data(columns, from_date, to_date)


@mcp.tool()
def get_manifest() -> dict[str, Any]:
    """Export manifest — SHA-256 fingerprints + generated_at timestamp
    tying the CSVs to their pipeline run. Check this to confirm how
    fresh the data the agent is reading is."""
    return da.get_manifest()


@mcp.tool()
def get_thresholds() -> list[dict[str, Any]]:
    """Per-variant position-function thresholds (long_thr, def_thr) with
    the calibration note. wf365 currently (0.45, 0.80) under v14 wider
    taper; sf730 reference (0.55, 0.65)."""
    return da.get_thresholds()


@mcp.tool()
def get_data_inventory() -> list[dict[str, Any]]:
    """Which input series the pipeline pulls, coverage windows, row counts.
    Good for answering 'do we have <X> data back to <date>?' questions."""
    return da.get_data_inventory()


@mcp.tool()
def get_pinning_audit() -> list[dict[str, Any]]:
    """Pinning audit findings — which sub-signals have pinned orientations
    and their in-sample vs hold-out AUCs. Key reference when discussing
    monitor flags."""
    return da.get_pinning_audit()


# ─── App assembly ─────────────────────────────────────────────────────────────

def build_app() -> Starlette:
    """Return the Streamable-HTTP ASGI app with bearer auth applied."""
    inner = mcp.streamable_http_app()
    app = Starlette(
        routes=inner.routes,
        middleware=[Middleware(BearerAuthMiddleware)],
        lifespan=inner.router.lifespan_context,
    )
    return app


# Module-level app for `uvicorn mcp_server:app --host ... --port ...`
app = build_app()


if __name__ == "__main__":
    # Fail fast if token unset — same contract as api_server.py.
    if not os.environ.get("BTC_API_TOKEN"):
        raise SystemExit("BTC_API_TOKEN is unset; refusing to start.")
    uvicorn.run(
        "mcp_server:app",
        host=os.environ.get("MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("MCP_PORT", "8788")),
        log_level="info",
    )
