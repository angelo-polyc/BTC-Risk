"""All external API calls — CoinGecko for prices, Coinglass for CVD/funding."""
from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, timezone
from typing import NamedTuple

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

CGLASS_BASE = "https://open-api-v4.coinglass.com"
CG_BASE     = "https://pro-api.coingecko.com/api/v3"

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
    reraise=True,
)


class DayBar(NamedTuple):
    date: date
    close: float


class CVDBar(NamedTuple):
    date: date
    buy: float
    sell: float


class SourceAPI:

    def __init__(self, key: str | None = None, timeout: float = 20.0) -> None:
        self._key    = key or os.environ.get("COINGLASS_API_KEY", "")
        self._cg_key = os.environ.get("COINGECKO_API_KEY", "")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._supported: set[str] = set()
        self._cg_id_map: dict[str, str] = {}   # symbol.upper() → coingecko id

    async def __aenter__(self) -> "SourceAPI":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return self

    async def __aexit__(self, *_) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client, "use as async context manager"
        return self._client

    @_retry
    async def _get(self, path: str, params: dict | None = None) -> dict:
        r = await self.client.get(
            f"{CGLASS_BASE}{path}",
            params=params,
            headers={"CG-API-KEY": self._key},
        )
        r.raise_for_status()
        return r.json()

    async def warm_supported(self) -> None:
        """Pre-fetch Coinglass supported coins + CoinGecko symbol→id map."""
        await asyncio.gather(
            self._warm_coinglass_supported(),
            self._warm_cg_id_map(),
        )

    async def _warm_coinglass_supported(self) -> None:
        try:
            r = await self._get("/api/futures/supported-coins")
            self._supported = {s.upper() for s in r.get("data", [])}
            print(f"[sources] coinglass supported: {len(self._supported)}")
        except Exception as e:
            print(f"[sources] coinglass supported-coins failed: {e}")
            self._supported = set()

    async def _warm_cg_id_map(self) -> None:
        """Build symbol → CoinGecko ID map from top-1000 by market cap."""
        headers = {"x-cg-pro-api-key": self._cg_key} if self._cg_key else {}
        mapping: dict[str, str] = {}
        try:
            for page in range(1, 5):   # 250 × 4 = top 1000
                r = await self.client.get(
                    f"{CG_BASE}/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc",
                            "per_page": 250, "page": page},
                    headers=headers,
                )
                r.raise_for_status()
                for coin in r.json():
                    sym = coin.get("symbol", "").upper()
                    cid = coin.get("id", "")
                    if sym and cid and sym not in mapping:
                        mapping[sym] = cid
            self._cg_id_map = mapping
            print(f"[sources] coingecko id map: {len(mapping)} symbols")
        except Exception as e:
            print(f"[sources] coingecko id map failed: {e}")
            self._cg_id_map = {}

    def supports(self, symbol: str) -> bool:
        return symbol.upper() in self._supported

    # ------------------------------------------------------------------ #
    # Spot prices                                                          #
    # ------------------------------------------------------------------ #

    async def spot_history(self, symbol: str, limit: int = 220) -> list[DayBar]:
        """Daily close prices. CoinGecko primary (broad coverage), Coinglass fallback."""
        # -- CoinGecko (primary) --
        cg_id = self._cg_id_map.get(symbol.upper())
        if cg_id:
            try:
                headers = {"x-cg-pro-api-key": self._cg_key} if self._cg_key else {}
                r = await self.client.get(
                    f"{CG_BASE}/coins/{cg_id}/market_chart",
                    params={"vs_currency": "usd", "days": str(limit), "interval": "daily"},
                    headers=headers,
                )
                r.raise_for_status()
                data = r.json()
                prices = data.get("prices", [])
                if len(prices) >= 10:
                    out = []
                    for ts_ms, px in prices:
                        out.append(DayBar(
                            date=datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date(),
                            close=float(px),
                        ))
                    return sorted(out, key=lambda x: x.date)
            except Exception as e:
                print(f"[sources] cg price {symbol} ({cg_id}): {e}")

        # -- Coinglass fallback (spot then perp) --
        for path, exchange, pair in [
            ("/api/spot/price/history",    "Binance", f"{symbol}USDT"),
            ("/api/spot/price/history",    "Bybit",   f"{symbol}USDT"),
            ("/api/futures/price/history", "Binance", f"{symbol}USDT"),
            ("/api/futures/price/history", "Bybit",   f"{symbol}USDT"),
        ]:
            try:
                r = await self._get(path, {"exchange": exchange, "symbol": pair,
                                           "interval": "1d", "limit": limit})
                data = r.get("data") or []
                if len(data) < 10:
                    continue
                out = []
                for row in data:
                    ts = row.get("time") or row.get("t")
                    if ts and ts > 1e12:
                        ts /= 1000
                    close = row.get("close") or row.get("c")
                    if ts and close:
                        out.append(DayBar(
                            date=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                            close=float(close),
                        ))
                if out:
                    return sorted(out, key=lambda x: x.date)
            except Exception as e:
                print(f"[sources] cglass price {symbol} {exchange}: {e}")
        return []

    # ------------------------------------------------------------------ #
    # CVD (aggregated taker buy/sell)                                     #
    # ------------------------------------------------------------------ #

    async def cvd_history(self, symbol: str, limit: int = 100) -> list[CVDBar]:
        """Aggregated taker buy/sell volume across Binance, Bybit, OKX, Bitget."""
        if not self.supports(symbol):
            return []
        try:
            r = await self._get(
                "/api/futures/aggregated-taker-buy-sell-volume/history",
                {
                    "symbol": symbol,
                    "exchange_list": "Binance,Bybit,OKX,Bitget",
                    "interval": "1d",
                    "limit": limit,
                },
            )
            data = r.get("data") or []
            out = []
            for row in data:
                ts = row.get("time") or row.get("t")
                if ts and ts > 1e12:
                    ts /= 1000
                buy  = row.get("aggregated_buy_volume_usd")
                sell = row.get("aggregated_sell_volume_usd")
                if ts and buy is not None and sell is not None:
                    out.append(CVDBar(
                        date=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                        buy=float(buy),
                        sell=float(sell),
                    ))
            return sorted(out, key=lambda x: x.date)
        except Exception as e:
            print(f"[sources] cvd {symbol}: {e}")
            return []

    # ------------------------------------------------------------------ #
    # Funding rate                                                         #
    # ------------------------------------------------------------------ #

    async def funding_history(self, symbol: str, limit: int = 100) -> list[DayBar]:
        """OI-weighted daily funding rate (as decimal, e.g. 0.0001 per 8h)."""
        if not self.supports(symbol):
            return []
        try:
            r = await self._get(
                "/api/futures/funding-rate/oi-weight-history",
                {"symbol": symbol, "interval": "1d", "limit": limit},
            )
            data = r.get("data") or []
            out = []
            for row in data:
                ts = row.get("time") or row.get("t")
                if ts and ts > 1e12:
                    ts /= 1000
                close = row.get("close")
                if ts and close is not None:
                    out.append(DayBar(
                        date=datetime.fromtimestamp(ts, tz=timezone.utc).date(),
                        close=float(close),
                    ))
            return sorted(out, key=lambda x: x.date)
        except Exception as e:
            print(f"[sources] funding {symbol}: {e}")
            return []
