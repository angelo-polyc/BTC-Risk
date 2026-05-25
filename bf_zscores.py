"""Z-score computation for divergence detection.

Conventions (since the spec only fixes the field names):
- `_z`  suffix: z-score of the LATEST value vs the full 30-day window
                (used where the level itself is normalized — price, funding,
                liq/oi ratio).
- `_dz` suffix: z-score of the LATEST day's % change vs the prior daily changes
                (used for trend-heavy series — vol, OI, TVL, dex vol).

Returns `None` if fewer than 5 useful points are available (matching the spec's
≥5-point threshold).
"""
from __future__ import annotations

import math
from typing import Optional

MIN_POINTS = 5


def _zscore_latest(values: list[float]) -> Optional[float]:
    """Z-score of the last element relative to the full sample."""
    if len(values) < MIN_POINTS:
        return None
    n = len(values)
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    if var <= 0.0:
        return 0.0
    std = math.sqrt(var)
    return (values[-1] - mean) / std


def _pct_changes(values: list[float]) -> list[float]:
    """Daily simple returns; skip points where the prior day is zero/negative."""
    out: list[float] = []
    for prev, cur in zip(values, values[1:]):
        if prev is None or cur is None or prev == 0:
            continue
        out.append((cur - prev) / prev)
    return out


def z_level(series: list[dict]) -> Optional[float]:
    vals = [pt["v"] for pt in series if pt.get("v") is not None]
    return _zscore_latest(vals)


def z_delta(series: list[dict]) -> Optional[float]:
    vals = [pt["v"] for pt in series if pt.get("v") is not None]
    if len(vals) < MIN_POINTS + 1:
        return None
    return _zscore_latest(_pct_changes(vals))


def compute_zscores(metrics: dict[str, list[dict]]) -> dict[str, Optional[float]]:
    """Compute the full z-score block for a single token."""
    return {
        "price_z":      z_level(metrics.get("price", [])),
        "price_dz":     z_delta(metrics.get("price", [])),
        "spot_vol_dz":  z_delta(metrics.get("spot_vol", [])),
        "oi_dz":        z_delta(metrics.get("oi", [])),
        "funding_z":    z_level(metrics.get("funding_apr", [])),
        "perp_vol_dz":  z_delta(metrics.get("perp_vol", [])),
        "liq_ratio_z":  z_level(metrics.get("liq_oi_ratio", [])),
        "tvl_dz":       z_delta(metrics.get("tvl", [])),
        "dex_vol_dz":   z_delta(metrics.get("dex_vol", [])),
    }
