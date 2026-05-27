"""Write pipeline CSV outputs to shared Postgres after each daily run.

Called as the final step in runtime_config.yaml. Soft-fails on individual
files so one missing CSV never aborts the whole write.

Tables:
  model_daily         — master_daily_view per variant (wf365/sf730)
  model_hypothesis    — hypothesis composite scores (6 hypotheses)
  model_weight_history — walk-forward weight history
  model_health_history — health check replay history
  model_shadow        — shadow state time series
  model_kv            — snapshot tables: weights, health_current, thresholds,
                         manifest, data_inventory, pinning_audit
"""
from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR", "/app/data"))

HYPOTHESES = ["macro_equities", "cme", "crypto_derivatives", "classic_cycle", "etf_flows", "eth"]
VARIANTS   = ["wf365", "sf730"]
LABELS     = ["y_60", "y_30"]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# JSON-safe conversion (mirrors data_access._clean_value)
# ---------------------------------------------------------------------------

def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, pd.Timestamp):
        return v.strftime("%Y-%m-%d")
    if hasattr(v, "item"):          # numpy scalar
        return v.item()
    return v


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def _load(filename: str, parse_dates: list[str] | None = None) -> pd.DataFrame | None:
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  [skip] {filename} not found")
        return None
    try:
        return pd.read_csv(path, parse_dates=parse_dates or [], low_memory=False)
    except Exception as e:
        print(f"  [error] reading {filename}: {e}")
        return None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_daily (
    variant TEXT NOT NULL,
    date    TEXT NOT NULL,
    data    JSONB NOT NULL,
    PRIMARY KEY (variant, date)
);
CREATE TABLE IF NOT EXISTS model_hypothesis (
    name TEXT NOT NULL,
    date TEXT NOT NULL,
    data JSONB NOT NULL,
    PRIMARY KEY (name, date)
);
CREATE TABLE IF NOT EXISTS model_weight_history (
    variant  TEXT NOT NULL,
    label    TEXT NOT NULL,
    fit_date TEXT NOT NULL,
    data     JSONB NOT NULL,
    PRIMARY KEY (variant, label, fit_date)
);
CREATE TABLE IF NOT EXISTS model_health_history (
    replay_date TEXT    NOT NULL,
    extended    BOOLEAN NOT NULL,
    data        JSONB   NOT NULL,
    PRIMARY KEY (replay_date, extended)
);
CREATE TABLE IF NOT EXISTS model_shadow (
    date TEXT PRIMARY KEY,
    data JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS model_kv (
    key        TEXT PRIMARY KEY,
    written_at TEXT NOT NULL,
    data       JSONB NOT NULL
);
"""


def init_schema(cur) -> None:
    cur.execute(SCHEMA)


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_model_daily(cur) -> None:
    for variant in VARIANTS:
        df = _load(f"master_daily_view_{variant}.csv", parse_dates=["date"])
        if df is None or df.empty:
            continue
        rows = [(variant, r["date"], json.dumps({k: v for k, v in r.items() if k != "date"}))
                for r in _df_to_records(df)]
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO model_daily (variant, date, data)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (variant, date) DO UPDATE SET data = EXCLUDED.data
        """, rows, page_size=500)
        print(f"  model_daily [{variant}]: {len(rows)} rows")


def write_hypotheses(cur) -> None:
    for name in HYPOTHESES:
        df = _load(f"hypothesis_{name}.csv", parse_dates=["date"])
        if df is None or df.empty:
            continue
        rows = [(name, r["date"], json.dumps({k: v for k, v in r.items() if k != "date"}))
                for r in _df_to_records(df)]
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO model_hypothesis (name, date, data)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (name, date) DO UPDATE SET data = EXCLUDED.data
        """, rows, page_size=500)
        print(f"  model_hypothesis [{name}]: {len(rows)} rows")


def write_weight_history(cur) -> None:
    for variant in VARIANTS:
        for label in LABELS:
            df = _load(f"weight_history_{variant}_{label}.csv", parse_dates=["fit_date"])
            if df is None or df.empty:
                continue
            rows = [(variant, label, r["fit_date"],
                     json.dumps({k: v for k, v in r.items() if k != "fit_date"}))
                    for r in _df_to_records(df)]
            psycopg2.extras.execute_batch(cur, """
                INSERT INTO model_weight_history (variant, label, fit_date, data)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (variant, label, fit_date) DO UPDATE SET data = EXCLUDED.data
            """, rows, page_size=500)
            print(f"  model_weight_history [{variant}/{label}]: {len(rows)} rows")


def write_health_history(cur) -> None:
    for extended, filename in [(False, "health_check_history.csv"),
                                (True,  "health_check_history_extended.csv")]:
        df = _load(filename, parse_dates=["replay_date"])
        if df is None or df.empty:
            continue
        rows = [(r["replay_date"], extended,
                 json.dumps({k: v for k, v in r.items() if k != "replay_date"}))
                for r in _df_to_records(df)]
        psycopg2.extras.execute_batch(cur, """
            INSERT INTO model_health_history (replay_date, extended, data)
            VALUES (%s, %s, %s::jsonb)
            ON CONFLICT (replay_date, extended) DO UPDATE SET data = EXCLUDED.data
        """, rows, page_size=500)
        print(f"  model_health_history [extended={extended}]: {len(rows)} rows")


def write_shadow(cur) -> None:
    df = _load("shadow_state.csv", parse_dates=["date"])
    if df is None or df.empty:
        return
    rows = [(r["date"], json.dumps({k: v for k, v in r.items() if k != "date"}))
            for r in _df_to_records(df)]
    psycopg2.extras.execute_batch(cur, """
        INSERT INTO model_shadow (date, data)
        VALUES (%s, %s::jsonb)
        ON CONFLICT (date) DO UPDATE SET data = EXCLUDED.data
    """, rows, page_size=500)
    print(f"  model_shadow: {len(rows)} rows")


def write_kv(cur, key: str, data) -> None:
    cur.execute("""
        INSERT INTO model_kv (key, written_at, data)
        VALUES (%s, %s, %s::jsonb)
        ON CONFLICT (key) DO UPDATE SET written_at = EXCLUDED.written_at, data = EXCLUDED.data
    """, (key, _now(), json.dumps(data)))
    print(f"  model_kv [{key}]: written")


def write_snapshots(cur) -> None:
    # weights
    df = _load("weights.csv")
    if df is not None:
        write_kv(cur, "weights", _df_to_records(df))

    # health current
    df = _load("health_check.csv")
    if df is not None:
        write_kv(cur, "health_current", _df_to_records(df))

    # thresholds
    df = _load("thresholds.csv")
    if df is not None:
        write_kv(cur, "thresholds", _df_to_records(df))

    # manifest
    manifest_path = DATA_DIR / "export_manifest.json"
    if not manifest_path.exists():
        manifest_path = Path(__file__).parent / "export_manifest.json"
    if manifest_path.exists():
        write_kv(cur, "manifest", json.loads(manifest_path.read_text()))

    # data_inventory
    df = _load("data_inventory.csv")
    if df is not None:
        write_kv(cur, "data_inventory", _df_to_records(df))

    # pinning_audit
    df = _load("pinning_audit_findings.csv")
    if df is not None:
        write_kv(cur, "pinning_audit", _df_to_records(df))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    if not DATABASE_URL:
        print("[db_writer] DATABASE_URL not set — skipping")
        return 0

    print(f"[db_writer] connecting to Postgres")
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"[db_writer] connection failed: {e}")
        return 1

    errors = 0
    with conn:
        with conn.cursor() as cur:
            print("[db_writer] initialising schema")
            init_schema(cur)

        for label, fn in [
            ("model_daily",        write_model_daily),
            ("hypotheses",         write_hypotheses),
            ("weight_history",     write_weight_history),
            ("health_history",     write_health_history),
            ("shadow",             write_shadow),
            ("snapshots",          write_snapshots),
        ]:
            try:
                print(f"[db_writer] writing {label}")
                with conn.cursor() as cur:
                    fn(cur)
            except Exception as e:
                print(f"[db_writer] ERROR in {label}: {e}")
                errors += 1

    conn.close()
    print(f"[db_writer] done — {errors} error(s)")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
