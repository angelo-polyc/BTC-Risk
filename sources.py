"""Unified source wrapper — the only module that talks to external APIs.

The ingest worker, backfill script, and any future consumer all go through `SourceAPI`.
This isolates upstream-API mess (rate limits, response shapes, auth) into one file.

Usage:
    async with SourceAPI(cg_key, coinglass_key) as api:
        await api.prep_run()                          # warms internal caches once per ingest

        # Today's snapshot per token
        px, vol = await api.price_volume("bitcoin")
        derivs  = await api.derivatives("BTC")        # DerivsResult or None
        tvl, dx = await api.protocol("hyperliquid")   # (float, float) or (None, None)

        # 30-day backfill
        px_hist     = await api.price_history_30d("bitcoin")
        derivs_hist = await api.derivs_history_30d("BTC")
        proto_hist  = await api.protocol_history_30d("hyperliquid")
"""
from __future__ import annotations

import os
import re
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CG_BASE = "https://pro-api.coingecko.com/api/v3"
CGLASS_BASE = "https://open-api-v4.coinglass.com"
LLAMA_BASE = "https://api.llama.fi"

# CEX whitelist for spot-volume filtering (user-curated 2026-04-27)
ALLOWED_CEX: set[str] = {
    "Binance", "Bybit", "Gate", "Coinbase Exchange", "OKX", "MEXC", "Bitget", "Kraken",
    "HTX", "KuCoin", "Crypto.com Exchange", "Bullish", "BingX", "Bitfinex",
    "Bitstamp by Robinhood", "HashKey Exchange", "Backpack Exchange", "Binance US",
    "BitMEX", "Bithumb", "Bybit EU", "Hyperliquid",
}

# DEX patterns — included if green-trust at the ticker level
DEX_PATTERN = re.compile(
    r"(uniswap|pancakeswap|curve|raydium|aerodrome|velodrome|sushiswap|quickswap|meteora)",
    re.I,
)

# DefiLlama slug overrides for known edge cases (CG-id → DefiLlama slug)
SLUG_OVERRIDES: dict[str, str] = {
    "ether-fi": "ether.fi",
    "syrup": "maple-finance",
    # add as discovered
}

HOURS_PER_YEAR = 8760

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DerivsResult:
    oi: float | None
    funding_apr: float | None         # OI-weighted, annualized %
    perp_vol: float | None
    liq_oi_ratio: float | None        # 24h liqs USD / OI USD


# ---------------------------------------------------------------------------
# Retry decorator: handles transient HTTP errors with backoff
# ---------------------------------------------------------------------------

_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Source wrapper
# ---------------------------------------------------------------------------

class SourceAPI:
    """Unified data-fetching layer. One client, three upstream services."""

    def __init__(
        self,
        coingecko_key: str | None = None,
        coinglass_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._cg_key = coingecko_key or os.environ.get("COINGECKO_API_KEY")
        self._cglass_key = coinglass_key or os.environ.get("COINGLASS_API_KEY")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

        # Caches populated by prep_run() — refreshed once per ingest cycle
        self._cglass_today: dict[str, dict] = {}     # symbol → coins-markets row
        self._llama_protocols: dict[str, str] = {}   # CG-id → DefiLlama slug
        self._cglass_supported: set[str] = set()     # symbols Coinglass tracks
        self._price_cache: dict[str, float] = {}     # CG-id → price USD (batched)
        self._tickers_sem = asyncio.Semaphore(3)     # throttle concurrent tickers calls

    # -- lifecycle --

    async def __aenter__(self) -> "SourceAPI":
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("SourceAPI must be used as `async with` context")
        return self._client

    # -- one-shot caches: call once per ingest run --

    async def prep_run(self, token_ids: list[str] | None = None) -> None:
        """Pre-fetch shared caches. Call BEFORE iterating per-token.
        Pass token_ids to batch-fetch prices (saves ~291 individual CG calls)."""
        tasks = [
            self._fetch_coinglass_coins_markets(),
            self._fetch_coinglass_supported(),
            self._fetch_defillama_protocols(),
        ]
        if token_ids:
            tasks.append(self._batch_fetch_prices(token_ids))
        await asyncio.gather(*tasks)

    async def _batch_fetch_prices(self, token_ids: list[str]) -> None:
        """Fetch prices for all token_ids in batches of 250 (2 calls for ~291 tokens)."""
        batch_size = 250
        for i in range(0, len(token_ids), batch_size):
            batch = token_ids[i:i + batch_size]
            try:
                r = await self._cg_get("/simple/price",
                                        params={"ids": ",".join(batch), "vs_currencies": "usd"})
                for cg_id, data in r.items():
                    if "usd" in data:
                        self._price_cache[cg_id] = data["usd"]
            except Exception as e:
                print(f"[cg batch prices] batch {i}: {e}")

    async def _fetch_coinglass_coins_markets(self) -> None:
        try:
            r = await self._cglass_get("/api/futures/coins-markets",
                                        params={"per_page": 300})
            self._cglass_today = {
                row["symbol"].upper(): row for row in r.get("data", [])
            }
        except Exception as e:
            print(f"[sources] coinglass coins-markets failed: {e}")
            self._cglass_today = {}

    async def _fetch_coinglass_supported(self) -> None:
        try:
            r = await self._cglass_get("/api/futures/supported-coins")
            self._cglass_supported = {s.upper() for s in r.get("data", [])}
        except Exception as e:
            print(f"[sources] coinglass supported-coins failed: {e}")
            self._cglass_supported = set()

    async def _fetch_defillama_protocols(self) -> None:
        try:
            r = await self.client.get(f"{LLAMA_BASE}/protocols")
            r.raise_for_status()
            for p in r.json():
                if p.get("gecko_id"):
                    self._llama_protocols[p["gecko_id"]] = p["slug"]
        except Exception as e:
            print(f"[sources] defillama protocols failed: {e}")
            self._llama_protocols = {}

    # -- universe helpers --

    def coinglass_supports(self, symbol: str) -> bool:
        return symbol.upper() in self._cglass_supported

    def defillama_slug(self, cg_id: str) -> str | None:
        return SLUG_OVERRIDES.get(cg_id) or self._llama_protocols.get(cg_id)

    # -- today's metrics, per token --

    async def price_volume(self, token_id: str) -> tuple[float | None, float | None]:
        """CoinGecko: returns (price_usd, whitelist_filtered_vol_usd).
        Price comes from the batch cache if prep_run(token_ids=...) was called.
        Errors on price and vol are handled independently so a tickers failure
        does not discard a successfully cached price."""
        price = self._price_cache.get(token_id)
        if price is None:
            try:
                price = await self._cg_simple_price(token_id)
            except Exception as e:
                print(f"[cg price] {token_id}: {e}")
        vol = None
        try:
            vol = await self._cg_filtered_volume(token_id)
        except Exception as e:
            print(f"[cg vol] {token_id}: {e}")
        return price, vol

    async def derivatives(self, symbol: str) -> DerivsResult | None:
        """Coinglass: OI, OI-weighted annualized funding, perp volume, liqs/OI ratio."""
        sym = symbol.upper()
        if not self.coinglass_supports(sym):
            return None
        try:
            row = self._cglass_today.get(sym)
            if not row:
                return None
            oi = row.get("open_interest_usd")
            long_vol = row.get("long_volume_usd_24h") or 0
            short_vol = row.get("short_volume_usd_24h") or 0
            perp_vol = (long_vol + short_vol) or None
            long_liq = row.get("long_liquidation_usd_24h") or 0
            short_liq = row.get("short_liquidation_usd_24h") or 0
            liq_oi = (long_liq + short_liq) / oi if oi else None

            # Annualized funding via per-venue OI-weighting
            apr = await self._cglass_funding_apr(sym)

            return DerivsResult(
                oi=oi,
                funding_apr=apr,
                perp_vol=perp_vol,
                liq_oi_ratio=liq_oi,
            )
        except Exception as e:
            print(f"[cglass] {sym}: {e}")
            return None

    async def protocol(self, slug: str | None, dex_chain: str | None = None) -> tuple[float | None, float | None]:
        """DefiLlama: returns (tvl_usd, 24h_dex_vol_usd).
        dex_chain: DefiLlama chain slug used as fallback when slug has no DEX listing."""
        if not slug and not dex_chain:
            return None, None
        try:
            tvl, dex_vol = await asyncio.gather(
                self._llama_protocol_tvl(slug) if slug else asyncio.sleep(0),
                self._llama_dex_vol_24h(slug, dex_chain),
                return_exceptions=True,
            )
            tvl = tvl if not isinstance(tvl, Exception) else None
            dex_vol = dex_vol if not isinstance(dex_vol, Exception) else None
            return tvl, dex_vol
        except Exception as e:
            print(f"[llama] {slug}: {e}")
            return None, None

    # -- 30-day history (for backfill) --

    async def price_history_30d(self, token_id: str) -> list[tuple[date, float, float]]:
        """CoinGecko marketChart: returns [(date, price_close, volume_usd), ...]."""
        try:
            params = {"vs_currency": "usd", "days": "30", "interval": "daily"}
            r = await self._cg_get(f"/coins/{token_id}/market_chart", params=params)
            prices = r["prices"]
            vols = r["total_volumes"]
            out = []
            for (ts, px), (_, vol) in zip(prices, vols):
                d = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).date()
                out.append((d, px, vol))
            return out
        except Exception as e:
            print(f"[cg history] {token_id}: {e}")
            return []

    async def derivs_history_30d(self, symbol: str) -> list[tuple[date, float | None, float | None, float | None, float | None]]:
        """Coinglass: returns [(date, oi, funding_apr, perp_vol, liq_oi_ratio), ...]."""
        sym = symbol.upper()
        if not self.coinglass_supports(sym):
            return []
        end_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        start_ms = end_ms - 31 * 24 * 3600 * 1000
        try:
            oi_hist, fr_hist, vol_hist, liq_hist = await asyncio.gather(
                self._cglass_oi_history(sym, start_ms, end_ms),
                self._cglass_funding_history(sym, start_ms, end_ms),
                self._cglass_volume_history(sym, start_ms, end_ms),
                self._cglass_liq_history(sym, start_ms, end_ms),
                return_exceptions=True,
            )
            # Build a date-keyed dict from each
            def by_date(series, key="close"):
                out = {}
                if isinstance(series, Exception) or not series:
                    return out
                for row in series:
                    ts = row.get("time") or row.get("t") or row.get("timestamp")
                    if not ts:
                        continue
                    if ts > 1e12:  # ms → s
                        ts /= 1000
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                    out[d] = row.get(key)
                return out

            oi_by = by_date(oi_hist, "close")
            fr_by = by_date(fr_hist, "close")  # raw rate; annualization needs interval — see note
            vol_by = by_date(vol_hist, "futures_vol_usd")
            liq_by = {}
            if not isinstance(liq_hist, Exception) and liq_hist:
                for row in liq_hist:
                    ts = row.get("time") or row.get("t") or row.get("timestamp")
                    if not ts: continue
                    if ts > 1e12: ts /= 1000
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                    long_l = row.get("long_liquidation_usd") or row.get("longLiquidationUsd") or 0
                    short_l = row.get("short_liquidation_usd") or row.get("shortLiquidationUsd") or 0
                    liq_by[d] = long_l + short_l

            # Stitch into per-day rows
            all_dates = sorted(set(oi_by) | set(fr_by) | set(vol_by) | set(liq_by))
            out = []
            for d in all_dates:
                oi = oi_by.get(d)
                fr_raw = fr_by.get(d)
                # The OI-weighted OHLC funding endpoint returns rates already in
                # OI-weighted percent terms; period = 8h (Coinglass convention for
                # this aggregate). Annualize via × 1095.
                fapr = fr_raw * 1095 if fr_raw is not None else None
                pvol = vol_by.get(d)
                liqs = liq_by.get(d)
                liq_oi = (liqs / oi) if (liqs is not None and oi) else None
                out.append((d, oi, fapr, pvol, liq_oi))
            return out
        except Exception as e:
            print(f"[cglass history] {sym}: {e}")
            return []

    async def protocol_history_30d(self, slug: str | None, dex_chain: str | None = None) -> list[tuple[date, float | None, float | None]]:
        """DefiLlama: returns [(date, tvl, dex_vol), ...] for last 30 days."""
        if not slug and not dex_chain:
            return []
        try:
            tvl_hist, dex_hist = await asyncio.gather(
                self._llama_tvl_history(slug) if slug else asyncio.sleep(0),
                self._llama_dex_vol_history(slug, dex_chain),
                return_exceptions=True,
            )
            def by_date(series):
                out = {}
                if isinstance(series, Exception) or not series:
                    return out
                for row in series:
                    ts = row.get("date") if isinstance(row, dict) else row[0]
                    val = row.get("totalLiquidityUSD") if isinstance(row, dict) else row[1]
                    if not ts: continue
                    if ts > 1e12: ts /= 1000
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                    out[d] = val
                return out
            tvl_by = by_date(tvl_hist)
            dex_by = by_date(dex_hist)
            today = date.today()
            cutoff = today - timedelta(days=30)
            out = []
            for d in sorted(set(tvl_by) | set(dex_by)):
                if d < cutoff: continue
                out.append((d, tvl_by.get(d), dex_by.get(d)))
            return out
        except Exception as e:
            print(f"[llama history] {slug}: {e}")
            return []

    # =======================================================================
    # Private — per-API request helpers
    # =======================================================================

    # --- CoinGecko ---

    @_retry
    async def _cg_get(self, path: str, params: dict | None = None) -> dict:
        headers = {"x-cg-pro-api-key": self._cg_key} if self._cg_key else {}
        r = await self.client.get(f"{CG_BASE}{path}", params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _cg_simple_price(self, token_id: str) -> float | None:
        r = await self._cg_get("/simple/price",
                                params={"ids": token_id, "vs_currencies": "usd"})
        return r.get(token_id, {}).get("usd")

    async def _cg_filtered_volume(self, token_id: str) -> float | None:
        """Sum converted_volume.usd over whitelisted CEX + green-trust DEX tickers."""
        async with self._tickers_sem:
            r = await self._cg_get(f"/coins/{token_id}/tickers",
                                    params={"depth": "false", "include_exchange_logo": "false"})
            total = 0.0
            for t in r.get("tickers", []):
                market_name = (t.get("market") or {}).get("name", "")
                market_id = (t.get("market") or {}).get("identifier", "")
                trust = t.get("trust_score")
                usd_vol = (t.get("converted_volume") or {}).get("usd")
                if usd_vol is None:
                    continue
                if market_name in ALLOWED_CEX:
                    total += usd_vol
                elif trust == "green" and DEX_PATTERN.search(market_id or ""):
                    total += usd_vol
        return total or None

    # --- Coinglass ---

    @_retry
    async def _cglass_get(self, path: str, params: dict | None = None) -> dict:
        headers = {"CG-API-KEY": self._cglass_key} if self._cglass_key else {}
        r = await self.client.get(f"{CGLASS_BASE}{path}", params=params, headers=headers)
        r.raise_for_status()
        return r.json()

    async def _cglass_funding_apr(self, symbol: str) -> float | None:
        """OI-weighted annualized funding rate. Pulls per-venue rates+OI, computes APR per venue,
        weights by OI USD. Methodology lives in the daily-analysis skill."""
        fr_resp, oi_resp = await asyncio.gather(
            self._cglass_get("/api/futures/funding-rate/exchange-list",
                              params={"symbol": symbol}),
            self._cglass_get("/api/futures/open-interest/exchange-list",
                              params={"symbol": symbol}),
        )
        # Find the BTC/ETH/etc. entry (filter is broken upstream — returns all coins)
        fr_list = next((d.get("stablecoin_margin_list", [])
                        for d in fr_resp.get("data", [])
                        if d.get("symbol", "").upper() == symbol), [])
        oi_list = [r for r in oi_resp.get("data", [])
                    if r.get("symbol", "").upper() == symbol and r.get("exchange") != "All"]
        if not fr_list or not oi_list:
            return None
        oi_by = {r["exchange"]: r.get("open_interest_usd", 0) for r in oi_list}
        weighted, total_oi = 0.0, 0.0
        for fr in fr_list:
            ex = fr.get("exchange")
            rate = fr.get("funding_rate")
            interval = fr.get("funding_rate_interval") or 8  # null → assume 8h
            oi = oi_by.get(ex)
            if rate is None or oi is None or oi <= 0:
                continue
            apr = rate * (HOURS_PER_YEAR / interval)  # rate already in %
            weighted += apr * oi
            total_oi += oi
        return (weighted / total_oi) if total_oi > 0 else None

    async def _cglass_oi_history(self, symbol: str, start_ms: int, end_ms: int):
        r = await self._cglass_get("/api/futures/open-interest/aggregated-history",
                                    params={"symbol": symbol, "interval": "1d",
                                            "start_time": start_ms, "end_time": end_ms,
                                            "limit": 35, "unit": "usd"})
        rows = r.get("data", [])
        for row in rows:
            if "close" in row and row["close"] is not None:
                row["close"] = float(row["close"])
        return rows

    async def _cglass_funding_history(self, symbol: str, start_ms: int, end_ms: int):
        r = await self._cglass_get("/api/futures/funding-rate/oi-weight-history",
                                    params={"exchange": "Binance", "symbol": symbol,
                                            "interval": "1d", "limit": 35})
        # close field is returned as a string — cast to float for downstream math
        rows = r.get("data", [])
        for row in rows:
            if "close" in row and row["close"] is not None:
                row["close"] = float(row["close"])
        return rows

    async def _cglass_volume_history(self, symbol: str, start_ms: int, end_ms: int):
        r = await self._cglass_get("/api/futures_spot_volume_ratio",
                                    params={"exchange_list": "Binance,OKX,Bybit",
                                            "symbol": symbol, "interval": "1d",
                                            "start_time": start_ms, "end_time": end_ms,
                                            "limit": 35})
        return r.get("data", [])

    async def _cglass_liq_history(self, symbol: str, start_ms: int, end_ms: int):
        r = await self._cglass_get("/api/futures/liquidation/aggregated-history",
                                    params={"symbol": symbol, "interval": "1d",
                                            "exchange_list": "Binance,OKX,Bybit",
                                            "limit": 35})
        out = []
        for row in r.get("data", []):
            out.append({
                "time": row.get("time"),
                "long_liquidation_usd": row.get("aggregated_long_liquidation_usd"),
                "short_liquidation_usd": row.get("aggregated_short_liquidation_usd"),
            })
        return out

    # --- DefiLlama ---

    @_retry
    async def _llama_get(self, path: str) -> dict:
        r = await self.client.get(f"{LLAMA_BASE}{path}")
        r.raise_for_status()
        return r.json()

    async def _llama_protocol_tvl(self, slug: str) -> float | None:
        r = await self._llama_get(f"/protocol/{slug}")
        chain_tvls = r.get("chainTvls") or {}
        # Sum the latest tvl across chains (DefiLlama returns daily series per chain)
        total = 0.0
        for chain_data in chain_tvls.values():
            tvl_series = chain_data.get("tvl", [])
            if tvl_series:
                latest = tvl_series[-1]
                total += latest.get("totalLiquidityUSD") or 0
        return total or None

    async def _llama_dex_vol_24h(self, slug: str | None, dex_chain: str | None = None) -> float | None:
        if slug:
            try:
                r = await self._llama_get(f"/summary/dexs/{slug}")
                return r.get("total24h")
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                # 404: slug is not a DEX protocol → fall through to chain
        if dex_chain:
            try:
                r = await self._llama_get(f"/overview/dexs/{dex_chain}")
                return r.get("total24h")
            except Exception:
                return None
        return None

    async def _llama_tvl_history(self, slug: str) -> list[dict]:
        r = await self._llama_get(f"/protocol/{slug}")
        # Pick the aggregated tvl series (DefiLlama returns 'tvl' array at top level)
        return r.get("tvl") or []

    async def _llama_dex_vol_history(self, slug: str | None, dex_chain: str | None = None) -> list[list]:
        if slug:
            try:
                r = await self._llama_get(f"/summary/dexs/{slug}?dataType=dailyVolume")
                return r.get("totalDataChart") or []
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 404:
                    raise
                # 404: slug is not a DEX protocol → fall through to chain
        if dex_chain:
            try:
                r = await self._llama_get(f"/overview/dexs/{dex_chain}?dataType=dailyVolume")
                return r.get("totalDataChart") or []
            except Exception:
                return []
        return []


# ---------------------------------------------------------------------------
# Convenience: thin module-level functions for simple consumers
# ---------------------------------------------------------------------------

async def fetch_today(token_id: str, symbol: str, slug: str | None) -> dict:
    """One-shot helper: fetch all metrics for one token. For ad-hoc / debug use."""
    async with SourceAPI() as api:
        await api.prep_run()
        px, vol = await api.price_volume(token_id)
        derivs = await api.derivatives(symbol)
        tvl, dx = await api.protocol(slug)
        return {
            "price": px, "spot_vol": vol,
            "oi": derivs.oi if derivs else None,
            "funding_apr": derivs.funding_apr if derivs else None,
            "perp_vol": derivs.perp_vol if derivs else None,
            "liq_oi_ratio": derivs.liq_oi_ratio if derivs else None,
            "tvl": tvl, "dex_vol": dx,
        }


if __name__ == "__main__":
    import sys, json
    async def _smoke():
        async with SourceAPI() as api:
            await api.prep_run()
            tid, sym = sys.argv[1] if len(sys.argv) > 1 else "bitcoin", \
                       sys.argv[2] if len(sys.argv) > 2 else "BTC"
            slug = api.defillama_slug(tid)
            px, vol = await api.price_volume(tid)
            derivs = await api.derivatives(sym)
            tvl, dx = await api.protocol(slug)
            print(json.dumps({
                "id": tid, "symbol": sym, "slug": slug,
                "price": px, "spot_vol": vol,
                "derivs": {
                    "oi": derivs.oi if derivs else None,
                    "funding_apr_pct": derivs.funding_apr if derivs else None,
                    "perp_vol": derivs.perp_vol if derivs else None,
                    "liq_oi_ratio": derivs.liq_oi_ratio if derivs else None,
                } if derivs else None,
                "tvl": tvl, "dex_vol": dx,
            }, indent=2, default=str))
    asyncio.run(_smoke())
