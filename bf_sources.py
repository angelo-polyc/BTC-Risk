"""Data source clients: CoinGecko, Coinglass, DefiLlama.

Each `fetch_*` returns a list[{"d": "YYYY-MM-DD", "v": float}] of at most 30
ascending daily points. Failures return [] — never raise to the orchestrator.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from bf_http import request_json
from bf_series import coerce_float, iso_day, last_n

LOG = logging.getLogger("divergence.sources")

# ---------- CoinGecko ----------

CG_BASE = "https://pro-api.coingecko.com/api/v3"


async def fetch_coingecko(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    coin_id: str,
    api_key: str,
    bucket=None,
) -> tuple[list[dict], list[dict]]:
    """Returns (price_series, spot_vol_series)."""
    data = await request_json(
        client,
        f"{CG_BASE}/coins/{coin_id}/market_chart",
        params={"vs_currency": "usd", "days": "90", "interval": "daily"},
        headers={"x-cg-pro-api-key": api_key, "accept": "application/json"},
        sem=sem,
        bucket=bucket,
    )
    if not isinstance(data, dict):
        return [], []

    prices_raw = data.get("prices") or []
    vols_raw = data.get("total_volumes") or []

    def _to_series(pairs):
        out = []
        for pair in pairs:
            if not (isinstance(pair, list) and len(pair) == 2):
                continue
            ts, val = pair
            v = coerce_float(val)
            if v is None:
                continue
            out.append({"d": iso_day(ts, unit="ms"), "v": v})
        return last_n(out)

    return _to_series(prices_raw), _to_series(vols_raw)


# ---------- Coinglass ----------

COINGLASS_BASE = "https://open-api-v4.coinglass.com"
COINGLASS_LIQ_EXCHANGES = "Binance,OKX,Bybit,Bitget,dYdX,Hyperliquid,Kraken"
COINGLASS_PERP_EXCHANGES = "Binance,OKX,Bybit"


async def _coinglass_get(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    api_key: str,
    path: str,
    params: dict,
    bucket=None,
) -> Optional[list]:
    """Coinglass v4 wraps results in {code, msg, data}. Returns `data` list or None."""
    body = await request_json(
        client,
        f"{COINGLASS_BASE}{path}",
        params=params,
        headers={"CG-API-KEY": api_key, "accept": "application/json"},
        sem=sem,
        bucket=bucket,
    )
    if not isinstance(body, dict):
        return None
    if body.get("code") not in (0, "0"):
        # Successful HTTP but Coinglass-level error (unknown symbol, no coverage, etc).
        return None
    data = body.get("data")
    return data if isinstance(data, list) else None


async def fetch_coinglass_oi(client, sem, symbol: str, api_key: str, bucket=None) -> list[dict]:
    rows = await _coinglass_get(
        client, sem, api_key,
        "/api/futures/open-interest/aggregated-history",
        {"symbol": symbol, "interval": "1d", "limit": 95, "unit": "usd"},
        bucket=bucket,
    )
    if not rows:
        return []
    out = []
    for r in rows:
        ts = r.get("time")
        v = coerce_float(r.get("close"))
        if ts is None or v is None:
            continue
        out.append({"d": iso_day(ts, unit="ms"), "v": v})
    return last_n(out)


async def fetch_coinglass_funding_apr(client, sem, symbol: str, api_key: str, bucket=None) -> list[dict]:
    """OI-weighted funding rate × 1095 = annualized APR (per spec)."""
    rows = await _coinglass_get(
        client, sem, api_key,
        "/api/futures/funding-rate/oi-weight-history",
        {"symbol": symbol, "interval": "1d", "limit": 95},
        bucket=bucket,
    )
    if not rows:
        return []
    out = []
    for r in rows:
        ts = r.get("time")
        close = coerce_float(r.get("close"))
        if ts is None or close is None:
            continue
        out.append({"d": iso_day(ts, unit="ms"), "v": close * 1095.0})
    return last_n(out)


async def fetch_coinglass_perp_vol(client, sem, symbol: str, api_key: str, bucket=None) -> list[dict]:
    """Daily aggregate perp volume in USD across major venues."""
    rows = await _coinglass_get(
        client, sem, api_key,
        "/api/futures_spot_volume_ratio",
        {
            "symbol": symbol,
            "exchange_list": COINGLASS_PERP_EXCHANGES,
            "interval": "1d",
        },
        bucket=bucket,
    )
    if not rows:
        return []
    out = []
    for r in rows:
        ts = r.get("time")
        v = coerce_float(r.get("futures_vol_usd"))
        if ts is None or v is None:
            continue
        out.append({"d": iso_day(ts, unit="ms"), "v": v})
    return last_n(out)


async def fetch_coinglass_liq_oi_ratio(
    client, sem, symbol: str, api_key: str, oi_series: list[dict], bucket=None
) -> list[dict]:
    """liq/oi = (long_liq + short_liq) / oi_usd on the same day. Requires the OI
    series so we can divide per day. Days where OI is unknown are dropped."""
    if not oi_series:
        return []
    rows = await _coinglass_get(
        client, sem, api_key,
        "/api/futures/liquidation/aggregated-history",
        {
            "symbol": symbol,
            "interval": "1d",
            "exchange_list": COINGLASS_LIQ_EXCHANGES,
            "limit": 95,
        },
        bucket=bucket,
    )
    if not rows:
        return []
    oi_by_day = {pt["d"]: pt["v"] for pt in oi_series}
    out = []
    for r in rows:
        ts = r.get("time")
        if ts is None:
            continue
        d = iso_day(ts, unit="ms")
        oi_v = oi_by_day.get(d)
        if not oi_v:  # missing or zero -> skip
            continue
        long_liq = coerce_float(r.get("aggregated_long_liquidation_usd")) or 0.0
        short_liq = coerce_float(r.get("aggregated_short_liquidation_usd")) or 0.0
        out.append({"d": d, "v": (long_liq + short_liq) / oi_v})
    return last_n(out)


# ---------- DefiLlama ----------

LLAMA_BASE = "https://api.llama.fi"


async def fetch_llama_tvl(
    client,
    sem,
    *,
    defillama_slug: Optional[str],
    chain_name: Optional[str],
) -> list[dict]:
    """Prefer protocol TVL if a slug is set, else chain TVL. Returns [] if neither."""
    out: list[dict] = []
    if defillama_slug:
        body = await request_json(
            client, f"{LLAMA_BASE}/protocol/{defillama_slug}", sem=sem,
        )
        if isinstance(body, dict):
            for r in body.get("tvl") or []:
                ts = r.get("date")
                v = coerce_float(r.get("totalLiquidityUSD"))
                if ts is None or v is None:
                    continue
                out.append({"d": iso_day(ts, unit="s"), "v": v})
    elif chain_name:
        body = await request_json(
            client, f"{LLAMA_BASE}/v2/historicalChainTvl/{chain_name}", sem=sem,
        )
        if isinstance(body, list):
            for r in body:
                ts = r.get("date")
                v = coerce_float(r.get("tvl"))
                if ts is None or v is None:
                    continue
                out.append({"d": iso_day(ts, unit="s"), "v": v})
    return last_n(out)


async def fetch_llama_dex_vol(
    client,
    sem,
    *,
    defillama_slug: Optional[str],
    chain_name: Optional[str],
) -> list[dict]:
    """Daily DEX volume for the protocol (if slug) or chain. Empty if neither
    side has DEX volume data — many protocols/chains legitimately don't."""
    body = None
    if defillama_slug:
        body = await request_json(
            client,
            f"{LLAMA_BASE}/summary/dexs/{defillama_slug}",
            params={"dataType": "dailyVolume"},
            sem=sem,
        )
    elif chain_name:
        body = await request_json(
            client,
            f"{LLAMA_BASE}/overview/dexs/{chain_name}",
            params={"dataType": "dailyVolume"},
            sem=sem,
        )
    if not isinstance(body, dict):
        return []
    out = []
    for pair in body.get("totalDataChart") or []:
        if not (isinstance(pair, list) and len(pair) == 2):
            continue
        ts, val = pair
        v = coerce_float(val)
        if v is None:
            continue
        out.append({"d": iso_day(ts, unit="s"), "v": v})
    return last_n(out)
