"""Shared data-access layer for the BTC model ops stack.

Pure functions that read the committed CSV artifacts produced by the
pipeline. Used by:
  * api_server.py    — HTTP/JSON layer (bearer auth)
  * mcp_server.py    — MCP tools for Claude Code agents

No model logic here. No mutation of any file. Only reads.

Everything is keyed off BTC_MODEL_DIR (env var, defaults to cwd). In a
Replit Reserved VM deployment this is the project checkout where
run_all.sh lives and where the daily pipeline writes its outputs.
"""
from __future__ import annotations

import json
import math
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd

# ─── File layout (mirrors /mnt/project/ flat structure) ───────────────────────

HYPOTHESES = ["macro_equities", "cme", "crypto_derivatives",
              "classic_cycle", "etf_flows", "eth"]
VARIANTS = ["wf365", "sf730"]
LABELS = ["y_60", "y_30"]

MASTER_FILES = {v: f"master_daily_view_{v}.csv" for v in VARIANTS}
HYPOTHESIS_FILES = {h: f"hypothesis_{h}.csv" for h in HYPOTHESES}


def project_dir() -> Path:
    return Path(os.environ.get("BTC_MODEL_DIR", ".")).expanduser().resolve()


def _path(name: str) -> Path:
    return project_dir() / name


# ─── JSON-safe conversion ─────────────────────────────────────────────────────
# pandas.to_dict emits float('nan') for missing numerics, which is not valid
# JSON. We replace NaN/Inf with None at the boundary so API and MCP responses
# are round-trippable.

def _clean_value(v: Any) -> Any:
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    return v


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    if df.empty:
        return []
    out = df.to_dict(orient="records")
    return [{k: _clean_value(v) for k, v in row.items()} for row in out]


def _filter_date_range(df: pd.DataFrame, date_col: str,
                       from_date: str | None, to_date: str | None) -> pd.DataFrame:
    if from_date:
        df = df[df[date_col] >= pd.Timestamp(from_date)]
    if to_date:
        df = df[df[date_col] <= pd.Timestamp(to_date)]
    return df


# ─── Cached loaders ───────────────────────────────────────────────────────────
# Caches are keyed by file mtime so that a fresh pipeline run (which rewrites
# these CSVs) is picked up automatically without restarting the server.

def _cache_key(path: Path) -> tuple[str, float]:
    return (str(path), path.stat().st_mtime if path.exists() else 0.0)


@lru_cache(maxsize=16)
def _load_csv_cached(key: tuple[str, float], parse_dates: tuple[str, ...]) -> pd.DataFrame:
    path = Path(key[0])
    if not path.exists():
        raise FileNotFoundError(f"missing: {path}")
    return pd.read_csv(
        path,
        parse_dates=list(parse_dates) if parse_dates else None,
        low_memory=False,
    )


def _load_csv(path: Path, parse_dates: tuple[str, ...] = ()) -> pd.DataFrame:
    return _load_csv_cached(_cache_key(path), parse_dates)


# ─── Master daily view ────────────────────────────────────────────────────────

def _load_master(variant: str) -> pd.DataFrame:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    return _load_csv(_path(MASTER_FILES[variant]), parse_dates=("date",))


def get_today(variant: str = "wf365") -> dict[str, Any]:
    df = _load_master(variant)
    if df.empty:
        return {}
    row = df.sort_values("date").iloc[-1].to_dict()
    return {k: _clean_value(v) for k, v in row.items()}


def get_history(from_date: str | None = None, to_date: str | None = None,
                variant: str = "wf365") -> list[dict[str, Any]]:
    df = _load_master(variant)
    df = _filter_date_range(df, "date", from_date, to_date)
    return _df_to_records(df.sort_values("date"))


# ─── Hypothesis scores (with sub-signals) ─────────────────────────────────────

def list_hypotheses() -> list[str]:
    return list(HYPOTHESES)


def get_hypothesis(name: str, from_date: str | None = None,
                   to_date: str | None = None) -> list[dict[str, Any]]:
    if name not in HYPOTHESIS_FILES:
        raise ValueError(f"hypothesis must be one of {HYPOTHESES}, got {name!r}")
    df = _load_csv(_path(HYPOTHESIS_FILES[name]), parse_dates=("date",))
    df = _filter_date_range(df, "date", from_date, to_date)
    return _df_to_records(df.sort_values("date"))


# ─── Weights + walk-forward weight history ────────────────────────────────────

def get_weights() -> list[dict[str, Any]]:
    return _df_to_records(_load_csv(_path("weights.csv")))


def get_weight_history(variant: str = "wf365", label: str = "y_60",
                       from_date: str | None = None,
                       to_date: str | None = None) -> list[dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"variant must be one of {VARIANTS}, got {variant!r}")
    if label not in LABELS:
        raise ValueError(f"label must be one of {LABELS}, got {label!r}")
    path = _path(f"weight_history_{variant}_{label}.csv")
    df = _load_csv(path, parse_dates=("fit_date",))
    df = _filter_date_range(df, "fit_date", from_date, to_date)
    return _df_to_records(df.sort_values("fit_date"))


# ─── Health check / drift monitor ─────────────────────────────────────────────

def get_health() -> list[dict[str, Any]]:
    return _df_to_records(_load_csv(_path("health_check.csv")))


def get_health_flags() -> list[dict[str, Any]]:
    df = _load_csv(_path("health_check.csv"))
    if "flagged" in df.columns:
        # bool dtype may round-trip as bool-string in CSV; handle both
        flagged = df["flagged"].astype(str).str.lower().isin(["true", "1"]) \
            if df["flagged"].dtype == object else df["flagged"].astype(bool)
        df = df[flagged]
    return _df_to_records(df)


def get_health_history(from_date: str | None = None,
                       to_date: str | None = None,
                       extended: bool = False) -> list[dict[str, Any]]:
    filename = "health_check_history_extended.csv" if extended else "health_check_history.csv"
    path = _path(filename)
    df = _load_csv(path, parse_dates=("replay_date",))
    df = _filter_date_range(df, "replay_date", from_date, to_date)
    return _df_to_records(df.sort_values("replay_date"))


def get_shadow_state(from_date: str | None = None,
                     to_date: str | None = None) -> list[dict[str, Any]]:
    df = _load_csv(_path("shadow_state.csv"), parse_dates=("date",))
    df = _filter_date_range(df, "date", from_date, to_date)
    return _df_to_records(df.sort_values("date"))


# ─── Raw data (26K rows × 162 cols — column-selectable, range-required) ───────

def list_raw_columns() -> dict[str, Any]:
    df = _load_csv(_path("raw_data_export.csv"), parse_dates=("date",))
    cols = [c for c in df.columns if c != "date"]
    grouped: dict[str, list[str]] = {}
    for c in cols:
        # Columns are named like `fred__SP500__value`, `velo_btc__funding_rate__binance-futures__value`
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
        raise ValueError("columns must be non-empty; use list_raw_columns() to discover")
    df = _load_csv(_path("raw_data_export.csv"), parse_dates=("date",))
    unknown = [c for c in columns if c not in df.columns]
    if unknown:
        raise ValueError(f"unknown columns: {unknown}")
    df = df[["date"] + columns]
    df = _filter_date_range(df, "date", from_date, to_date)
    return _df_to_records(df.sort_values("date"))


# ─── Metadata ─────────────────────────────────────────────────────────────────

def get_manifest() -> dict[str, Any]:
    path = _path("export_manifest.json")
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def get_thresholds() -> list[dict[str, Any]]:
    return _df_to_records(_load_csv(_path("thresholds.csv")))


def get_data_inventory() -> list[dict[str, Any]]:
    return _df_to_records(_load_csv(_path("data_inventory.csv")))


def get_pinning_audit() -> list[dict[str, Any]]:
    return _df_to_records(_load_csv(_path("pinning_audit_findings.csv")))


# ─── Summary / freshness ──────────────────────────────────────────────────────

def get_status() -> dict[str, Any]:
    """Lightweight health check of the data layer itself. Useful as a smoke
    endpoint and as MCP's default 'what's there'."""
    root = project_dir()
    files = {}
    for fname in list(MASTER_FILES.values()) + list(HYPOTHESIS_FILES.values()) + [
        "weights.csv", "weight_history_wf365_y_60.csv", "weight_history_wf365_y_30.csv",
        "health_check.csv", "shadow_state.csv", "raw_data_export.csv",
        "thresholds.csv", "data_inventory.csv", "export_manifest.json",
    ]:
        p = root / fname
        files[fname] = {
            "present": p.exists(),
            "mtime_utc": pd.Timestamp.utcfromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S") if p.exists() else None,
            "size_bytes": p.stat().st_size if p.exists() else None,
        }
    try:
        latest = get_today("wf365")
        latest_date = latest.get("date")
        latest_position = latest.get("position")
        latest_regime = latest.get("regime")
    except Exception as e:
        latest_date, latest_position, latest_regime = None, None, f"error: {e}"

    return {
        "project_dir": str(root),
        "latest_date": latest_date,
        "latest_position": latest_position,
        "latest_regime": latest_regime,
        "files": files,
    }
