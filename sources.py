"""Unified source wrapper — the only module that talks to external APIs.

Usage:
    async with SourceAPI(cg_key, coinglass_key) as api:
        await api.prep_run()                          # warms caches once per ingest

        # Today's snapshot per token
        px, vol = await api.price_volume("bitcoin")
        derivs  = await api.derivatives("BTC")        # DerivsResult or None
        tvl, dx = await api.protocol("hyperliquid")   # (float|None, float|None)
        tvl, dx = await api.protocol(None, "bsc")     # chain TVL + chain DEX vol

        # 30-day backfill
        px_hist     = await api.price_history_30d("bitcoin")
        derivs_hist = await api.derivs_history_30d("BTC")
        proto_hist  = await api.protocol_history_30d("hyperliquid")
        proto_hist  = await api.protocol_history_30d(None, "bsc")
"""
from __future__ import annotations

import os
import re
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CG_BASE = "https://pro-api.coingecko.com/api/v3"
CGLASS_BASE = "https://open-api-v4.coinglass.com"
LLAMA_BASE = "https://api.llama.fi"

ALLOWED_CEX: set[str] = {
    "Binance", "Bybit", "Gate", "Coinbase Exchange", "OKX", "MEXC", "Bitget", "Kraken",
    "HTX", "KuCoin", "Crypto.com Exchange", "Bullish", "BingX", "Bitfinex",
    "Bitstamp by Robinhood", "HashKey Exchange", "Backpack Exchange", "Binance US",
    "BitMEX", "Bithumb", "Bybit EU", "Hyperliquid",
}

# DEX patterns on market.identifier — included in spot vol.
# CG Pro returns trust_score=null in tickers, so we match by exchange identifier instead.
DEX_PATTERN = re.compile(
    r"(uniswap|pancakeswap|curve|raydium|aerodrome|velodrome|sushiswap|quickswap|meteora"
    r"|camelot|traderjoe|orca|whirlpool|lifinity|balancer|kyberswap|maverick|thruster"
    r"|syncswap|horizondex|woofi|perpetual|pancake|sunswap|justusd|sunio)",
    re.I,
)

SLUG_OVERRIDES: dict[str, str] = {
    "ether-fi": "ether.fi",
    "syrup": "maple-finance",
}

HOURS_PER_YEAR = 8760

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DerivsResult:
    oi: float | None
    funding_apr: float | None
    perp_vol: float | None
    liq_oi_ratio: float | None


# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

_retry = retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=8),
    retry=retry_if_exception_type((httpx.HTTPError, asyncio.TimeoutError)),
    reraise=True,
)


# ---------------------------------------------------------------------------
# Source wrapper
# ---------------------------------------------------------------------------

class SourceAPI:

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

        self._cglass_today: dict[str, dict] = {}
        self._llama_protocols: dict[str, str] = {}
        self._llama_chains: dict[str, float] = {}       # chain_name → current TVL (ingest)
        self._llama_chain_hist: dict[str, list] = {}    # chain_name → history (backfill cache)
        self._cglass_supported: set[str] = set()
        self._price_cache: dict[str, float] = {}
        self._tickers_sem = asyncio.Semaphore(3)

    async def __aenter__(self) -> "SourceAPI":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0),
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

    async def prep_run(self, token_ids: list[str] | None = None) -> None:
        """Pre-fetch shared caches. Call BEFORE iterating per-token."""
        tasks = [
            self._fetch_coinglass_coins_markets(),
            self._fetch_coinglass_supported(),
            self._fetch_defillama_protocols(),
            self._fetch_llama_chains(),
        ]
        if token_ids:
            tasks.append(self._batch_fetch_prices(token_ids))
        await asyncio.gather(*tasks)

    async def _batch_fetch_prices(self, token_ids: list[str]) -> None:
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
                                        params={"per_page": 500})
            self._cglass_today = {
                row["symbol"].upper(): row for row in r.get("data", [])
            }
            print(f"[sources] coinglass coins-markets: {len(self._cglass_today)} symbols")
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

    async def _fetch_llama_chains(self) -> None:
        """Bulk-fetch current TVL for all chains in one call. Much faster than
        per-chain /v2/historicalChainTvl calls during daily ingest."""
        try:
            r = await self.client.get(f"{LLAMA_BASE}/v2/chains", timeout=30)
            r.raise_for_status()
            self._llama_chains = {
                c["name"]: float(c.get("tvl") or 0)
                for c in r.json() if c.get("name")
            }
            print(f"[sources] llama chains cache: {len(self._llama_chains)} chains")
        except Exception as e:
            print(f"[sources] defillama chains failed: {e}")
            self._llama_chains = {}

    def coinglass_supports(self, symbol: str) -> bool:
        return symbol.upper() in self._cglass_supported

    def defillama_slug(self, cg_id: str) -> str | None:
        return SLUG_OVERRIDES.get(cg_id) or self._llama_protocols.get(cg_id)

    # -- today's metrics --

    async def price_volume(self, token_id: str) -> tuple[float | None, float | None]:
        """Returns (price_usd, spot_vol_usd). Spot vol = CEX allowlist + DEX tickers."""
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
        sym = symbol.upper()
        if not self.coinglass_supports(sym):
            return None
        try:
            row = self._cglass_today.get(sym)
            if row:
                # Primary path: coins-markets (pre-aggregated)
                oi = row.get("open_interest_usd")
                long_vol = row.get("long_volume_usd_24h") or 0
                short_vol = row.get("short_volume_usd_24h") or 0
                perp_vol = (long_vol + short_vol) or None
                long_liq = row.get("long_liquidation_usd_24h") or 0
                short_liq = row.get("short_liquidation_usd_24h") or 0
                liq_oi = (long_liq + short_liq) / oi if oi else None
                apr = await self._cglass_funding_apr(sym)
                return DerivsResult(oi=oi, funding_apr=apr, perp_vol=perp_vol, liq_oi_ratio=liq_oi)
            else:
                # Fallback: pairs-markets aggregation for tokens outside coins-markets top-500
                return await self._cglass_derivs_from_pairs(sym)
        except Exception as e:
            print(f"[cglass] {sym}: {e}")
            return None

    async def _cglass_derivs_from_pairs(self, symbol: str) -> DerivsResult | None:
        """Aggregate OI, funding, vol, liqs from pairs-markets for tokens outside top-500."""
        try:
            r = await self._cglass_get("/api/futures/pairs-markets",
                                        params={"symbol": symbol})
            rows = r.get("data", [])
            if not rows:
                return None

            # 1h settlement venues (others assumed 8h)
            HOURLY = {"Hyperliquid", "Kraken", "Coinbase", "Crypto.com"}

            total_oi, total_vol, total_liq_l, total_liq_s = 0.0, 0.0, 0.0, 0.0
            weighted_apr, weight = 0.0, 0.0

            for row in rows:
                oi = float(row.get("open_interest_usd") or 0)
                total_oi += oi
                total_vol += float(row.get("volume_usd") or 0)
                total_liq_l += float(row.get("long_liquidation_usd_24h") or 0)
                total_liq_s += float(row.get("short_liquidation_usd_24h") or 0)
                rate = row.get("funding_rate")
                if rate is not None and oi > 0:
                    ex = row.get("exchange_name", "")
                    interval = 1 if ex in HOURLY else 8
                    apr = float(rate) * (HOURS_PER_YEAR / interval)
                    weighted_apr += apr * oi
                    weight += oi

            if total_oi == 0:
                return None

            return DerivsResult(
                oi=total_oi,
                funding_apr=(weighted_apr / weight) if weight > 0 else None,
                perp_vol=total_vol or None,
                liq_oi_ratio=(total_liq_l + total_liq_s) / total_oi if total_oi else None,
            )
        except Exception as e:
            print(f"[cglass pairs] {symbol}: {e}")
            return None

    async def protocol(
        self,
        slug: str | None,
        chain_name: str | None = None,
    ) -> tuple[float | None, float | None]:
        """Returns (tvl_usd, dex_vol_24h_usd).

        slug:       DeFi protocol → /protocol/{slug} TVL, /summary/dexs/{slug} vol
        chain_name: L1/L2 chain  → /v2/historicalChainTvl/{chain} TVL, /overview/dexs/{chain} vol
        """
        if not slug and not chain_name:
            return None, None
        try:
            if slug:
                tvl, dex_vol = await asyncio.gather(
                    self._llama_protocol_tvl(slug),
                    self._llama_dex_vol_24h(slug, None),
                    return_exceptions=True,
                )
            else:
                tvl, dex_vol = await asyncio.gather(
                    self._llama_chain_tvl(chain_name),
                    self._llama_dex_vol_24h(None, chain_name),
                    return_exceptions=True,
                )
            tvl = tvl if not isinstance(tvl, Exception) else None
            dex_vol = dex_vol if not isinstance(dex_vol, Exception) else None
            return tvl, dex_vol
        except Exception as e:
            print(f"[llama] slug={slug} chain={chain_name}: {e}")
            return None, None

    # -- 30-day history (for backfill) --

    async def price_history_30d(self, token_id: str) -> list[tuple[date, float, float]]:
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

            def by_date(series, key="close"):
                out = {}
                if isinstance(series, Exception) or not series:
                    return out
                for row in series:
                    ts = row.get("time") or row.get("t") or row.get("timestamp")
                    if not ts:
                        continue
                    if ts > 1e12:
                        ts /= 1000
                    d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                    out[d] = row.get(key)
                return out

            oi_by = by_date(oi_hist, "close")
            fr_by = by_date(fr_hist, "close")
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

            all_dates = sorted(set(oi_by) | set(fr_by) | set(vol_by) | set(liq_by))
            out = []
            for d in all_dates:
                oi = oi_by.get(d)
                fr_raw = fr_by.get(d)
                fapr = fr_raw * 1095 if fr_raw is not None else None
                pvol = vol_by.get(d)
                liqs = liq_by.get(d)
                liq_oi = (liqs / oi) if (liqs is not None and oi) else None
                out.append((d, oi, fapr, pvol, liq_oi))
            return out
        except Exception as e:
            print(f"[cglass history] {sym}: {e}")
            return []

    async def protocol_history_30d(
        self,
        slug: str | None,
        chain_name: str | None = None,
    ) -> list[tuple[date, float | None, float | None]]:
        """Returns [(date, tvl, dex_vol), ...] for last 30 days."""
        if not slug and not chain_name:
            return []
        try:
            if slug:
                tvl_hist, dex_hist = await asyncio.gather(
                    self._llama_tvl_history(slug),
                    self._llama_dex_vol_history(slug, None),
                    return_exceptions=True,
                )
            else:
                tvl_hist, dex_hist = await asyncio.gather(
                    self._llama_chain_tvl_history(chain_name),
                    self._llama_dex_vol_history(None, chain_name),
                    return_exceptions=True,
                )

            def by_date(series):
                out = {}
                if isinstance(series, Exception) or not series:
                    return out
                for row in series:
                    if isinstance(row, dict):
                        ts = row.get("date")
                        # Protocol TVL uses "totalLiquidityUSD"; chain TVL uses "tvl"
                        val = row.get("totalLiquidityUSD") or row.get("tvl")
                    else:
                        ts, val = row[0], row[1]
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
            print(f"[llama history] slug={slug} chain={chain_name}: {e}")
            return []

    # =======================================================================
    # Private helpers
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
        """Spot vol = CEX allowlist + DEX tickers (matched by DEX_PATTERN on identifier).
        CG Pro returns trust_score=null — do not filter on it."""
        async with self._tickers_sem:
            r = await self._cg_get(f"/coins/{token_id}/tickers",
                                    params={"depth": "false", "include_exchange_logo": "false"})
            total = 0.0
            for t in r.get("tickers", []):
                market_name = (t.get("market") or {}).get("name", "")
                market_id = (t.get("market") or {}).get("identifier", "")
                usd_vol = (t.get("converted_volume") or {}).get("usd")
                if usd_vol is None:
                    continue
                if market_name in ALLOWED_CEX:
                    total += usd_vol
                elif DEX_PATTERN.search(market_id or ""):
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
        fr_resp, oi_resp = await asyncio.gather(
            self._cglass_get("/api/futures/funding-rate/exchange-list",
                              params={"symbol": symbol}),
            self._cglass_get("/api/futures/open-interest/exchange-list",
                              params={"symbol": symbol}),
        )
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
            interval = fr.get("funding_rate_interval") or 8
            oi = oi_by.get(ex)
            if rate is None or oi is None or oi <= 0:
                continue
            apr = rate * (HOURS_PER_YEAR / interval)
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
        """OI-weighted funding history. No exchange param — endpoint aggregates all."""
        r = await self._cglass_get("/api/futures/funding-rate/oi-weight-history",
                                    params={"symbol": symbol, "interval": "1d", "limit": 35})
        rows = r.get("data", [])
        for row in rows:
            if "close" in row and row["close"] is not None:
                row["close"] = float(row["close"])
        return rows

    async def _cglass_volume_history(self, symbol: str, start_ms: int, end_ms: int):
        # futures_spot_volume_ratio doesn't respect limit reliably — pull and slice last 30
        r = await self._cglass_get("/api/futures_spot_volume_ratio",
                                    params={"exchange_list": "Binance,OKX,Bybit",
                                            "symbol": symbol, "interval": "1d",
                                            "start_time": start_ms, "end_time": end_ms})
        return (r.get("data", []) or [])[-35:]

    async def _cglass_liq_history(self, symbol: str, start_ms: int, end_ms: int):
        r = await self._cglass_get("/api/futures/liquidation/aggregated-history",
                                    params={"symbol": symbol, "interval": "1d",
                                            "exchange_list": "Binance,OKX,Bybit,Bitget,dYdX,Hyperliquid,Kraken",
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
    async def _llama_get(self, path: str) -> dict | list:
        r = await self.client.get(f"{LLAMA_BASE}{path}", timeout=20)
        r.raise_for_status()
        return r.json()

    async def _llama_protocol_tvl(self, slug: str) -> float | None:
        """Current TVL for a DeFi protocol via /tvl/{slug} (returns single float).
        Fast — no historical data downloaded. Falls back to /protocol/{slug}
        only if the lightweight endpoint fails."""
        try:
            r = await self.client.get(f"{LLAMA_BASE}/tvl/{slug}", timeout=15)
            if r.status_code == 200:
                val = float(r.text.strip())
                return val if val > 0 else None
        except Exception:
            pass
        # Fallback: full protocol endpoint (slower, handles staking-only edge cases)
        try:
            r = await self._llama_get(f"/protocol/{slug}")
            tvl_series = r.get("tvl") or []
            if tvl_series:
                val = tvl_series[-1].get("totalLiquidityUSD")
                if val:
                    return float(val)
            current = r.get("currentChainTvls") or {}
            if current:
                total = sum(float(v) for v in current.values() if isinstance(v, (int, float)))
                return total or None
        except Exception:
            pass
        return None

    async def _llama_chain_tvl(self, chain_name: str) -> float | None:
        """Current TVL for an L1/L2 chain. Uses bulk chains cache (populated in
        prep_run) — no per-chain API call needed during ingest."""
        for name in (chain_name, chain_name.title()):
            val = self._llama_chains.get(name)
            if val:
                return float(val)
        # Cache miss — fall back to historical endpoint (backfill path)
        for name in (chain_name, chain_name.title()):
            try:
                r = await self._llama_get(f"/v2/historicalChainTvl/{name}")
                if r:
                    return float(r[-1].get("tvl") or 0) or None
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue
                raise
            except Exception:
                break
        return None

    async def _llama_dex_vol_24h(
        self,
        slug: str | None,
        chain_name: str | None = None,
    ) -> float | None:
        """24h DEX volume. Protocol: /summary/dexs/{slug} total24h field.
        Chain: /overview/dexs/{chain} — total24h absent, use totalDataChart[-1][1]."""
        if slug:
            try:
                r = await self._llama_get(f"/summary/dexs/{slug}")
                return r.get("total24h")
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    raise
                # 4xx → fall through to chain endpoint
            except Exception:
                pass
        if chain_name:
            for name in (chain_name, chain_name.title()):
                try:
                    r = await self._llama_get(f"/overview/dexs/{name}")
                    # total24h does NOT exist on chain overview — use last chart point
                    chart = r.get("totalDataChart") or []
                    if chart:
                        last = chart[-1]
                        return float(last[1]) if isinstance(last, list) else None
                    return None
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
                except Exception:
                    break
        return None

    async def _llama_tvl_history(self, slug: str) -> list[dict]:
        """30d protocol TVL history. Returns [{date: ts, totalLiquidityUSD: v}, ...]."""
        r = await self._llama_get(f"/protocol/{slug}")
        return r.get("tvl") or []

    async def _llama_chain_tvl_history(self, chain_name: str) -> list[dict]:
        """30d chain TVL history. Cached — each chain fetched only once per run."""
        if chain_name in self._llama_chain_hist:
            return self._llama_chain_hist[chain_name]
        for name in (chain_name, chain_name.title()):
            try:
                r = await self._llama_get(f"/v2/historicalChainTvl/{name}")
                result = r or []
                self._llama_chain_hist[chain_name] = result
                return result
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    continue
                raise
            except Exception:
                break
        self._llama_chain_hist[chain_name] = []
        return []

    async def _llama_dex_vol_history(
        self,
        slug: str | None,
        chain_name: str | None = None,
    ) -> list[list]:
        """30d DEX vol history. Returns [[timestamp, value], ...]."""
        if slug:
            try:
                r = await self._llama_get(f"/summary/dexs/{slug}?dataType=dailyVolume")
                return r.get("totalDataChart") or []
            except httpx.HTTPStatusError as e:
                if e.response.status_code >= 500:
                    raise
                # 4xx → fall through to chain endpoint
            except Exception:
                pass
        if chain_name:
            for name in (chain_name, chain_name.title()):
                try:
                    r = await self._llama_get(f"/overview/dexs/{name}?dataType=dailyVolume")
                    return r.get("totalDataChart") or []
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        continue
                    raise
                except Exception:
                    break
        return []
