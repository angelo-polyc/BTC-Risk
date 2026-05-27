"""Postgres-backed data access layer for the BTC model ops stack.

Replaces the CSV/file reads with queries against the shared Postgres DB
populated by db_writer.py on the risk-model service after each daily run.

/raw endpoints remain file-backed (162 cols × 26K rows, not in Postgres).

Function signatures are identical to the previous version so api_server.py
and mcp_server.py need no changes.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg2
import psycopg2.extras
import psycopg2.pool

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

HYPOTHESES = ["macro_equities", "cme", "crypto_derivatives", "classic_cycle", "etf_flows", "eth"]
VARIANTS   = ["wf365", "sf730"]
LABELS     = ["y_60", "y_30"]


def project_dir() -> Path:
    return Path(os.environ.get("BTC_MODEL_DIR", ".")).expanduser().resolve()


# ---------------------------------------------------------------------------
# Connection pool (lazy init, thread-safe)
# ---------------------------------------------------------------------------

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL not set")
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
    return _pool


def _query(sql: str, params=None) -> list[dict]:
    pool = _get_pool()
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        pool.putconn(conn)


# ---------------------------------------------------------------------------
# JSON-safe conversion
# ---------------------------------------------------------------------------

def _clean(v: Any) -> Any:
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _clean_row(row: dict) -> dict:
    return {k: _clean(v) for k, v in row.items()}


def _parse_jsonb(v) -> Any:
    """psycopg2 returns JSONB as Python objects, but handle string case too."""
    if isinstance(v, str):
        return json.loads(v)
    return v


# ---------------------------------------------------------------------------
# Master daily view
# ---------------------------------------------------------------------------

def get_today(variant: str = "wf365") -> dict[str, Any]:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    rows = _query(
        "SELECT date, data FROM model_daily WHERE variant=%s ORDER BY date DESC LIMIT 1",
        (variant,)
    )
    if not rows:
        return {}
    r = rows[0]
    result = {"date": r["date"]}
    result.update(_parse_jsonb(r["data"]))
    return _clean_row(result)


def get_history(from_date: str | None = None, to_date: str | None = None,
                variant: str = "wf365") -> list[dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    sql    = "SELECT date, data FROM model_daily WHERE variant=%s"
    params: list = [variant]
    if from_date:
        sql += " AND date >= %s"; params.append(from_date)
    if to_date:
        sql += " AND date <= %s"; params.append(to_date)
    sql += " ORDER BY date"
    out = []
    for r in _query(sql, params):
        row = {"date": r["date"]}
        row.update(_parse_jsonb(r["data"]))
        out.append(_clean_row(row))
    return out


# ---------------------------------------------------------------------------
# Hypothesis scores
# ---------------------------------------------------------------------------

def list_hypotheses() -> list[str]:
    return list(HYPOTHESES)


def get_hypothesis(name: str, from_date: str | None = None,
                   to_date: str | None = None) -> list[dict[str, Any]]:
    if name not in HYPOTHESES:
        raise ValueError(f"hypothesis must be one of {HYPOTHESES}, got {name!r}")
    sql    = "SELECT date, data FROM model_hypothesis WHERE name=%s"
    params: list = [name]
    if from_date:
        sql += " AND date >= %s"; params.append(from_date)
    if to_date:
        sql += " AND date <= %s"; params.append(to_date)
    sql += " ORDER BY date"
    out = []
    for r in _query(sql, params):
        row = {"date": r["date"]}
        row.update(_parse_jsonb(r["data"]))
        out.append(_clean_row(row))
    return out


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------

def get_weights() -> list[dict[str, Any]]:
    rows = _query("SELECT data FROM model_kv WHERE key='weights'")
    if not rows:
        return []
    return [_clean_row(r) for r in _parse_jsonb(rows[0]["data"])]


def get_weight_history(variant: str = "wf365", label: str = "y_60",
                       from_date: str | None = None,
                       to_date: str | None = None) -> list[dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, got {label!r}")
    sql    = "SELECT fit_date, data FROM model_weight_history WHERE variant=%s AND label=%s"
    params: list = [variant, label]
    if from_date:
        sql += " AND fit_date >= %s"; params.append(from_date)
    if to_date:
        sql += " AND fit_date <= %s"; params.append(to_date)
    sql += " ORDER BY fit_date"
    out = []
    for r in _query(sql, params):
        row = {"fit_date": r["fit_date"]}
        row.update(_parse_jsonb(r["data"]))
        out.append(_clean_row(row))
    return out


# ---------------------------------------------------------------------------
# Health / drift monitor
# ---------------------------------------------------------------------------

def get_health() -> list[dict[str, Any]]:
    rows = _query("SELECT data FROM model_kv WHERE key='health_current'")
    if not rows:
        return []
    return [_clean_row(r) for r in _parse_jsonb(rows[0]["data"])]


def get_health_flags() -> list[dict[str, Any]]:
    rows = get_health()
    return [r for r in rows
            if str(r.get("flagged", "")).lower() in ("true", "1")]


def get_health_history(from_date: str | None = None,
                       to_date: str | None = None,
                       extended: bool = False) -> list[dict[str, Any]]:
    sql    = "SELECT replay_date, data FROM model_health_history WHERE extended=%s"
    params: list = [extended]
    if from_date:
        sql += " AND replay_date >= %s"; params.append(from_date)
    if to_date:
        sql += " AND replay_date <= %s"; params.append(to_date)
    sql += " ORDER BY replay_date"
    out = []
    for r in _query(sql, params):
        row = {"replay_date": r["replay_date"]}
        row.update(_parse_jsonb(r["data"]))
        out.append(_clean_row(row))
    return out


# ---------------------------------------------------------------------------
# Shadow state
# ---------------------------------------------------------------------------

def get_shadow_state(from_date: str | None = None,
                     to_date: str | None = None) -> list[dict[str, Any]]:
    conditions, params = [], []
    if from_date:
        conditions.append("date >= %s"); params.append(from_date)
    if to_date:
        conditions.append("date <= %s"); params.append(to_date)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT date, data FROM model_shadow {where} ORDER BY date"
    out = []
    for r in _query(sql, params or None):
        row = {"date": r["date"]}
        row.update(_parse_jsonb(r["data"]))
        out.append(_clean_row(row))
    return out


# ---------------------------------------------------------------------------
# Raw data — file-backed (too large for Postgres)
# ---------------------------------------------------------------------------

def _raw_path() -> Path:
    return project_dir() / "raw_data_export.csv"


def _load_raw() -> pd.DataFrame:
    p = _raw_path()
    if not p.exists():
        raise ValueError(
            "raw_data_export.csv is not available on this service — "
            "query the risk-model service directly for raw data access"
        )
    return pd.read_csv(p, parse_dates=["date"], low_memory=False)


def list_raw_columns() -> dict[str, Any]:
    df = _load_raw()
    cols = [c for c in df.columns if c != "date"]
    grouped: dict[str, list[str]] = {}
    for c in cols:
        prefix = c.split("__", 1)[0] if "__" in c else c
        grouped.setdefault(prefix, []).append(c)
    return {
        "total_columns": len(cols),
        "row_count": len(df),
        "date_range": [df["date"].min().strftime("%Y-%m-%d"),
                       df["date"].max().strftime("%Y-%m-%d")],
        "groups": {g: len(cs) for g, cs in grouped.items()},
        "columns": cols,
    }


def get_raw_data(columns: list[str],
                 from_date: str | None = None,
                 to_date: str | None = None) -> list[dict[str, Any]]:
    if not columns:
        raise ValueError("columns must be non-empty")
    df = _load_raw()
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise ValueError(f"unknown columns: {unknown}")
    df = df[["date"] + columns]
    if from_date:
        df = df[df["date"] >= pd.Timestamp(from_date)]
    if to_date:
        df = df[df["date"] <= pd.Timestamp(to_date)]
    return [{k: (v.strftime("%Y-%m-%d") if isinstance(v, pd.Timestamp) else
                 (None if isinstance(v, float) and math.isnan(v) else v))
             for k, v in row.items()}
            for row in df.sort_values("date").to_dict(orient="records")]


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def get_manifest() -> dict[str, Any]:
    rows = _query("SELECT data FROM model_kv WHERE key='manifest'")
    if not rows:
        return {}
    return _parse_jsonb(rows[0]["data"])


def get_thresholds() -> list[dict[str, Any]]:
    rows = _query("SELECT data FROM model_kv WHERE key='thresholds'")
    if not rows:
        return []
    return [_clean_row(r) for r in _parse_jsonb(rows[0]["data"])]


def get_data_inventory() -> list[dict[str, Any]]:
    rows = _query("SELECT data FROM model_kv WHERE key='data_inventory'")
    if not rows:
        return []
    return [_clean_row(r) for r in _parse_jsonb(rows[0]["data"])]


def get_pinning_audit() -> list[dict[str, Any]]:
    rows = _query("SELECT data FROM model_kv WHERE key='pinning_audit'")
    if not rows:
        return []
    return [_clean_row(r) for r in _parse_jsonb(rows[0]["data"])]


# ---------------------------------------------------------------------------
# Status / freshness
# ---------------------------------------------------------------------------

def get_status() -> dict[str, Any]:
    try:
        today = get_today("wf365")
        latest_date     = today.get("date")
        latest_position = today.get("position")
        latest_regime   = today.get("regime")
    except Exception as e:
        latest_date = latest_position = None
        latest_regime = f"error: {e}"

    try:
        kv = _query("SELECT key, written_at FROM model_kv ORDER BY key")
        tables = {r["key"]: r["written_at"] for r in kv}
    except Exception:
        tables = {}

    return {
        "source":           "postgres",
        "latest_date":      latest_date,
        "latest_position":  latest_position,
        "latest_regime":    latest_regime,
        "tables":           tables,
    }
