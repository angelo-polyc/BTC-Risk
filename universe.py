"""Resolve top-300, apply exclusions, attach DefiLlama slug + Coinglass coverage flag."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sources import SourceAPI

# Must be Pro endpoint — demo endpoint gets rate-limited on category calls,
# causing excluded_ids to return empty and stables to leak into the universe.
CG_BASE = "https://pro-api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_API_KEY")

# CoinGecko category name → API slug
_CAT_SLUGS: dict[str, str] = {
    "Stablecoins":          "stablecoins",
    "Wrapped-Tokens":       "wrapped-tokens",
    "Liquid-Staked-Tokens": "liquid-staking-tokens",
    "Real World Assets":    "real-world-assets",   # tokenized treasuries, RWA funds
}

# Hardcoded fallback — always excluded even if category API misses them.
# Update this list whenever a new stable/RWA cracks the top-300.
STABLE_IDS: set[str] = {
    # Core / legacy stablecoins
    "tether", "usd-coin", "dai", "first-digital-usd", "true-usd", "frax",
    "usdd", "paypal-usd", "gemini-dollar", "nusd", "liquity-usd", "fei-usd",
    "magic-internet-money", "usde", "ethena-usde", "curve-usd", "crvusd",
    "usual-usd", "usd0", "resolv-usd",
    # Confirmed leaking as of 2026-04-29 (fixed by Pro API; kept as defence-in-depth)
    "usds", "usd1", "ripple-usd", "rlusd", "bfusd", "usdtb", "usdai",
    "mountain-protocol-usdm", "usdm", "satusd", "frax-dollar", "frxusd",
    "usdf", "celo-dollar", "jusd", "reusd", "stable",
    "re-protocol-reusd", "stable-2",
}

RWA_IDS: set[str] = {
    # Tokenized treasuries / institutional funds — confirmed leaking 2026-04-29
    "blackrock-usd-institutional-digital-liquidity-fund", "buidl",
    "hashnote-usyc", "usyc",
    "janus-henderson-aaa-clo-etf-tokenized", "jaaa",
    "janus-henderson-us-treasury-n-etf-tokenized", "jtrsy",
    # Actual CG IDs (guesses above were wrong — corrected 2026-04-29)
    "janus-henderson-anemoy-treasury-fund",
    "janus-henderson-anemoy-aaa-clo-fund",
}

WRAPPED_IDS: set[str] = {
    "wrapped-bitcoin", "wrapped-ethereum", "weth", "staked-ether",
    "wrapped-steth", "rocket-pool-eth", "binance-staked-eth",
    "coinbase-wrapped-staked-eth", "mantle-staked-ether",
    "wrapped-eeth", "wrapped-beacon-eth",
}

SLUG_OVERRIDES: dict[str, str] = {
    "ether-fi":  "ether.fi",
    "syrup":     "maple-finance",
    # add as discovered
}


@dataclass
class Token:
    id: str
    symbol: str
    rank: int
    defillama_slug: str | None
    has_coinglass: bool


async def _fetch_top_300(client: httpx.AsyncClient) -> list[dict]:
    headers = {"x-cg-pro-api-key": CG_KEY} if CG_KEY else {}
    out = []
    for page in (1, 2):
        r = await client.get(
            f"{CG_BASE}/coins/markets",
            params={"vs_currency": "usd", "order": "market_cap_desc",
                    "per_page": 250, "page": page},
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()
        out.extend(r.json())
        if len(out) >= 300:
            break
    return out[:300]


async def _fetch_excluded_ids(client: httpx.AsyncClient, categories: set[str]) -> set[str]:
    headers = {"x-cg-pro-api-key": CG_KEY} if CG_KEY else {}
    excluded: set[str] = set()
    for cat_name in categories:
        slug = _CAT_SLUGS.get(cat_name, cat_name.lower().replace(" ", "-"))
        r = await client.get(
            f"{CG_BASE}/coins/markets",
            params={"vs_currency": "usd", "category": slug, "per_page": 250},
            headers=headers,
            timeout=30,
        )
        if r.status_code == 200:
            for c in r.json():
                excluded.add(c["id"])
    return excluded


async def _fetch_coinglass_supported(client: httpx.AsyncClient) -> set[str]:
    r = await client.get(
        "https://open-api-v4.coinglass.com/api/futures/supported-coins",
        headers={"CG-API-KEY": os.environ.get("COINGLASS_API_KEY", "")},
        timeout=30,
    )
    if r.status_code != 200:
        return set()
    return {s.upper() for s in r.json().get("data", [])}


async def _fetch_defillama_protocols(client: httpx.AsyncClient) -> dict[str, str]:
    r = await client.get("https://api.llama.fi/protocols", timeout=60)
    r.raise_for_status()
    return {p["gecko_id"]: p["slug"] for p in r.json() if p.get("gecko_id")}


async def resolve_universe(
    top_n: int = 300,
    exclude_categories: set[str] | None = None,
    exclude_tokens: set[str] | None = None,
    api: "SourceAPI | None" = None,
) -> list[Token]:
    """Fetch and filter the token universe.

    If `api` is provided (and prep_run() has been called), reuse its Coinglass and
    DefiLlama caches instead of making duplicate API calls.
    """
    exclude_categories = exclude_categories or set()
    exclude_tokens = exclude_tokens or set()

    async with httpx.AsyncClient() as client:
        if api is not None:
            markets, excluded_ids = await asyncio.gather(
                _fetch_top_300(client),
                _fetch_excluded_ids(client, exclude_categories),
            )
            cg_supported: set[str] | None = None
            llama_map: dict[str, str] | None = None
        else:
            markets, excluded_ids, cg_supported, llama_map = await asyncio.gather(
                _fetch_top_300(client),
                _fetch_excluded_ids(client, exclude_categories),
                _fetch_coinglass_supported(client),
                _fetch_defillama_protocols(client),
            )

    # Union hardcoded fallbacks — defence-in-depth against category API misses
    excluded_ids |= STABLE_IDS | RWA_IDS | WRAPPED_IDS

    out: list[Token] = []
    for c in markets[:top_n]:
        if c["id"] in excluded_ids:
            continue
        if c["id"] in exclude_tokens:
            continue
        sym = c["symbol"].upper()

        if api is not None:
            # Use coins-markets presence, not supported-coins list.
            # supported-coins has 1152 symbols but many lack derivs history;
            # _cglass_today only contains tokens the coins-markets endpoint returned,
            # which is the actual data source for daily derivs snapshots.
            has_coinglass = sym in api._cglass_today
            slug = api.defillama_slug(c["id"])
        else:
            has_coinglass = sym in (cg_supported or set())
            slug = SLUG_OVERRIDES.get(c["id"]) or (llama_map or {}).get(c["id"])

        out.append(Token(
            id=c["id"],
            symbol=sym,
            rank=c["market_cap_rank"] or 999,
            defillama_slug=slug,
            has_coinglass=has_coinglass,
        ))
    return out
