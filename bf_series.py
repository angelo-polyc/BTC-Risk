"""Date and series normalization helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional


def iso_day(ts: float | int, *, unit: str = "s") -> str:
    """Convert epoch (seconds or milliseconds) to YYYY-MM-DD in UTC."""
    if unit == "ms":
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def coerce_float(x) -> Optional[float]:
    """Coinglass sometimes returns numeric fields as strings; some series have None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def last_n(series: Iterable[dict], n: int = 92) -> list[dict]:
    """Sort ascending by `d`, drop entries with missing values, dedupe by date
    (last value wins), and return the trailing `n`. Idempotent."""
    by_date: dict[str, float] = {}
    for pt in series:
        d = pt.get("d") if isinstance(pt, dict) else None
        v = pt.get("v") if isinstance(pt, dict) else None
        if d is None or v is None:
            continue
        by_date[d] = v
    return [{"d": d, "v": v} for d, v in sorted(by_date.items())][-n:]
