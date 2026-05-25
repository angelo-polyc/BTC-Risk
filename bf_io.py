"""IO: atomic write of `divergence.json`, load of existing data, and
per-metric merge logic per the spec."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("divergence.io")

MIN_USEFUL_POINTS = 5
TMP_NAME = "divergence.tmp"
FINAL_NAME = "divergence.json"
PROGRESS_NAME = "divergence.progress.json"


def data_dir() -> Path:
    p = Path(os.environ.get("DATA_DIR", "/data"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def tmp_path() -> Path:
    return data_dir() / TMP_NAME


def final_path() -> Path:
    return data_dir() / FINAL_NAME


def progress_path() -> Path:
    return data_dir() / PROGRESS_NAME


def load_existing() -> Optional[dict]:
    """Load the existing divergence.json if present, else None."""
    p = final_path()
    if not p.exists():
        return None
    try:
        with p.open("r") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        LOG.warning("could not read existing divergence.json: %s", exc)
        return None


def index_existing(existing: Optional[dict]) -> dict[str, dict]:
    """Index existing token entries by id for fast lookup during merge."""
    if not existing:
        return {}
    return {t["id"]: t for t in existing.get("universe", []) if isinstance(t, dict) and t.get("id")}


def merge_metric(backfilled: list[dict], existing_metric: list[dict]) -> list[dict]:
    """Per-spec merge rule:
       - backfill has >=5 points -> use backfill
       - backfill has <5 AND existing has >=5 -> preserve existing
       - else -> whatever backfill returned (could be [] or 1-4 points)
    """
    if len(backfilled) >= MIN_USEFUL_POINTS:
        return backfilled
    if len(existing_metric) >= MIN_USEFUL_POINTS:
        return existing_metric
    return backfilled


def write_atomic(payload: dict) -> Path:
    """Write to a tmp file in the same directory, fsync, then `os.replace` to
    the final path. `os.replace` is atomic on POSIX and Windows for same-dir
    moves."""
    tmp = tmp_path()
    fin = final_path()
    # Write to tmp.
    with tmp.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, fin)
    return fin


def atomic_write(payload: dict) -> Path:
    """Public alias for write_atomic."""
    return write_atomic(payload)


def write_progress_snapshot(snapshot: dict) -> None:
    """Best-effort progress write. Never raise — progress should never break the run."""
    try:
        p = progress_path()
        with p.open("w") as f:
            json.dump(snapshot, f)
    except OSError as exc:
        LOG.debug("progress write failed: %s", exc)
