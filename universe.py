"""Resolve top-300, apply exclusions, attach DefiLlama slug + Coinglass coverage flag."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from sources import SourceAPI

CG_BASE = "https://pro-api.coingecko.com/api/v3"
CG_KEY = os.environ.get("COINGECKO_API_KEY")

_CAT_SLUGS: dict[str, str] = {
    "Stablecoins":          "stablecoins",
    "Wrapped-Tokens":       "wrapped-tokens",
    "Liquid-Staked-Tokens": "liquid-staking-tokens",
    "Real World Assets":    "real-world-assets",
}

STABLE_IDS: set[str] = {
    "tether", "usd-coin", "dai", "first-digital-usd", "true-usd", "frax",
    "usdd", "paypal-usd", "gemini-dollar", "nusd", "liquity-usd", "fei-usd",
    "magic-internet-money", "usde", "ethena-usde", "curve-usd", "crvusd",
    "usual-usd", "usd0", "resolv-usd",
    "usds", "usd1", "ripple-usd", "rlusd", "bfusd", "usdtb", "usdai",
    "mountain-protocol-usdm", "usdm", "satusd", "frax-dollar", "frxusd",
    "usdf", "celo-dollar", "jusd", "reusd", "stable",
    "re-protocol-reusd", "stable-2",
}

RWA_IDS: set[str] = {
    "blackrock-usd-institutional-digital-liquidity-fund", "buidl",
    "hashnote-usyc", "usyc",
    "janus-henderson-aaa-clo-etf-tokenized", "jaaa",
    "janus-henderson-us-treasury-n-etf-tokenized", "jtrsy",
    "janus-henderson-anemoy-treasury-fund",
    "janus-henderson-anemoy-aaa-clo-fund",
}

WRAPPED_IDS: set[str] = {
    "wrapped-bitcoin", "wrapped-ethereum", "weth", "staked-ether",
    "wrapped-steth", "rocket-pool-eth", "binance-staked-eth",
    "coinbase-wrapped-staked-eth", "mantle-staked-ether",
    "wrapped-eeth", "wrapped-beacon-eth",
}

# Manually curated exclusions: low-signal, illiquid, or out-of-scope tokens.
PRESET_EXCLUDED_IDS: set[str] = {
    # Tokenized funds / RWA
    "superstate-short-duration-us-government-securities-fund-ustb",  # USTB
    "eutbl",                                        # EUTBL
    "spiko-amundi-overnight-swap-fund-eur",         # EURSAFO
    "spiko-us-t-bills-money-market-fund",           # USTBL
    "theo-short-duration-us-treasury-fund",         # THBILL
    "apollo-diversified-credit-securitize-fund",    # ACRED
    "securitize-tokenized-aaa-clo-fund",            # STAC
    "fidelity-digital-interest-token",              # FDIT
    "circle-internet-group-ondo-tokenized-stock",   # CRCLON
    "strategy-pp-variable-xstock",                  # STRCX
    # Tradable platform tokens (PC prefixes)
    "tradable-singapore-fintech-ssl",               # PC0000077
    "tradable-latam-middle-market-lender-sstl",     # PC0000085
    "tradable-na-third-party-online-merchant-sstn", # PC0000015
    "tradable-apac-diversified-finance-provider-sstn", # PC0000033
    "tradable-na-rent-financing-platform-sstn",     # PC0000031
    "tradable-latam-fintech-sstn",                  # PC0000097
    "tradable-singapore-fintech-ssl-2",             # PC0000023
    # Exchange tokens with no derivatives coverage
    "whitebit",                                     # WBT
    "mx-token",                                     # MX
    "btse-token",                                   # BTSE
    "tokenize-xchange",                             # TKX
    "bnb48-club-token",                             # KOGE
    # Low-signal / meme / illiquid
    "memecore",                                     # M
    "bianrensheng",                                 # 币安人生
    "ribbita-by-virtuals",                          # TIBBIR
    "banana-for-scale-2",                           # BANANAS31
    "yooldo-games",                                 # ESPORTS
    "troll-2",                                      # TROLL
    "sosovalue",                                    # SOSO
    "gmt-token",                                    # GOMINING
    "ozone-chain",                                  # OZO
    "asteroid-shiba",                               # ASTEROID
    "wemix-token",                                  # WEMIX
    "vaulta",                                       # A
    "safo",                                         # SAFO
    "newton-project",                               # AB
    "tagger",                                       # TAG
    "golem",                                        # GLM
    "safepal",                                      # SFP
    "cheems-token",                                 # CHEEMS
    "ecash",                                        # XEC
    "gala",                                         # GALA
    "zano",                                         # ZANO
    "vision-3",                                     # VSN
    "onyc",                                         # ONYC
    "chain-2",                                      # XCN
    "undeads-games",                                # UDS
    "billions-network",                             # BILL
    "trust-wallet-token",                           # TWT
    "neo",                                          # NEO
    "pieverse",                                     # PIEVERSE
    "apyusd",                                       # APYUSD
    "block-street",                                 # BSB
    "ultima",                                       # ULTIMA
    "reallink",                                     # REAL
    "ape-and-pepe",                                 # APEPE
    "audiera",                                      # BEAT
    "nexus-4",                                      # NEX
    "unibase",                                      # UB
    "skyai",                                        # SKYAI
    "build-on",                                     # B
    "apenft",                                       # NFT
    "olympus",                                      # OHM
    "kinesis-silver",                               # KAG
    "jasmycoin",                                    # JASMY
    "decred",                                       # DCR
    "the9bit",                                      # 9BIT
    "hastra-prime",                                 # PRIME
    "usdgo",                                        # USDGO
    "kinesis-gold",                                 # KAU
    "lab",                                          # LAB
    "siren-2",                                      # SIREN
    "sun-token",                                    # SUN
    "adi-token",                                    # ADI
    "ousg",                                         # OUSG
    "xdce-crowd-sale",                              # XDC
    "beldex",                                       # BDX
    "flare-networks",                               # FLR
    "gatechain-token",                              # GT
    "just",                                         # JST
    "blockchain-capital",                           # BCAP
    "wefi",                                         # WFI
    "figure-heloc",                                 # FIGR_HELOC
    "lido-earn-eth",                                # EARNETH
    "rain",                                         # RAIN
    "nexo",                                         # NEXO
}

# CG ID → DefiLlama chain slug.
# Used for BOTH chain TVL (/v2/historicalChainTvl/{chain})
# and chain DEX vol (/overview/dexs/{chain}).
# Tokens here get chain_name set and defillama_slug=None,
# preventing wrong gecko_id matches (bridge/protocol entries).
CHAIN_MAP: dict[str, str] = {
    "binancecoin":              "bsc",
    "avalanche-2":              "avalanche",
    "tron":                     "tron",
    "the-open-network":         "ton",
    "near":                     "near",
    "arbitrum":                 "arbitrum",
    "optimism":                 "Optimism",
    "aptos":                    "aptos",
    "sui":                      "sui",
    "fantom":                   "fantom",
    "sonic-3":                  "sonic",
    "soniclabs":                "sonic",
    "injective-protocol":       "injective",
    "sei-network":              "sei",
    "matic-network":            "polygon",
    "polygon-ecosystem-token":  "polygon",
    "celo":                     "celo",
    "kava":                     "kava",
    "mantle":                   "Mantle",
    "crypto-com-chain":         "Cronos",
    "coredaoorg":               "Core",
    "core-dao":                 "Core",
    "zksync":                   "zkSync Era",
    "blockstack":               "Stacks",
    "metis-token":              "Metis",
    "cardano":                  "Cardano",
    "immutable-x":              "Immutable zkEVM",
    "hedera-hashgraph":         "Hedera",
    "polkadot":                 "Polkadot",
    "internet-computer":        "ICP",
    "cosmos":                   "CosmosHub",
    "algorand":                 "Algorand",
    "celestia":                 "Celestia",
    "monad":                    "Monad",
    "story-2":                  "Story",
    "plasma":                   "Plasma",
    # Additional chains confirmed by DefiLlama agent audit
    "stellar":                  "Stellar",      # $168M
    "worldcoin-wld":            "World Chain",  # $38.7M
    "elrond":                   "Elrond",       # $19M (MultiversX)
    "iota":                     "IOTA",         # $13.8M
    "chiliz":                   "Chiliz",       # $4.1M
    "apecoin":                  "ApeChain",     # $3.5M
    "zcash":                    "Zcash",        # $2.7M
    "litecoin":                 "Litecoin",     # $2.2M
    "linea":                    "Linea",        # $53.9M
    "ripple":                   "Ripple",       # $47.5M
    "dydx":                     "dYdX",         # $99M — dYdX is a chain, not a protocol
    "provenance-blockchain":    "Provenance",   # $1.44B (HASH token)
    "hash-2":                   "Provenance",   # alt CG ID for HASH
    # Tokens whose gecko_id matches a bridge/protocol slug — override to chain:
    "starknet":                 "Starknet",
    # Tokens with protocol slugs returning $0 — chain endpoint has real TVL:
    "vechain":                  "VeChain",      # $1.6M
    "conflux-token":            "Conflux",      # $7.6M
    "kaia":                     "Kaia",         # $13.4M
    "tezos":                    "Tezos",        # $28.5M
    "filecoin":                 "Filecoin",     # $5.0M
    "gnosis":                   "xDai",         # gnosis slug → 400; chain → real TVL
}

# CG ID → correct DefiLlama protocol slug.
# Overrides gecko_id-based lookup from /protocols list.
# Priority: SLUG_OVERRIDES > gecko_id match > None
SLUG_OVERRIDES: dict[str, str] = {
    "ether-fi":                     "ether.fi",
    "syrup":                        "maple-finance",
    "uniswap":                      "uniswap",
    "aave":                         "aave",            # was matching to aave-v2 only
    "curve-dao-token":              "curve-dex",
    "pancakeswap-token":            "pancakeswap",
    "lido-dao":                     "lido",
    "compound-governance-token":    "compound-v3",
    "raydium":                      "raydium",
    "jupiter-exchange-solana":      "jupiter",         # was matching to jupiter-lend
    "gmx":                          "gmx",
    "hyperliquid":                  "hyperliquid",     # was matching to hyperliquid-bridge
    "ondo-finance":                 "ondo-finance",    # was matching to ondo-yield-assets
    "sky":                          "sky",             # was matching to sky-lending
    "thorchain":                    "thorchain-dex",       # was "thorchain-dex"
        "pendle":                       "pendle",
    "aerodrome-finance":            "aerodrome",
    "velodrome-finance":            "velodrome",
    "balancer":                     "balancer",
    "sushi":                        "sushiswap",
    "convex-finance":               "convex-finance",
    # Parent slugs absent from /protocols but accessible at /protocol/{slug}
    # Exchange tokens — large TVL via exchange protocol slugs
    "leo-token":                    "bitfinex",             # $18.67B
    "bitget-token":                 "bitget",               # $5.73B
    "htx-dao":                      "htx",                  # $5.61B
    "kucoin-shares":                "kucoin",               # $2.71B
    "pax-gold":                     "paxos-gold",           # $2.12B
    # Protocol slugs from audit
    "centrifuge":                   "centrifuge-protocol",  # $1.48B (was missing from overrides)
    "jito-governance":              "jito-liquid-staking",  # $877M
    "aster-2":                      "aster-bridge",         # $873M
    "lighter":                      "lighter-bridge",       # $496M
    "pump-fun":                     "pumpswap",             # $243M
    "doublezero":                   "doublezero-staked-sol", # $619M
    "chainlink":                    "stake.link-liquid",    # $68.5M (chainlink itself has no TVL)
    "shiba-inu":                    "shibaswap-v1",         # $5.7M
    "bonk":                         "bonk-staked-sol",      # $12.1M
    "deltaprime":                   "deltaprime",           # $4.3M
    "river-omni":                   "river-omni-cdp",       # $136M
    "maker":                        "makerdao",
    "yearn-finance":                "yearn-finance",
    "synthetix-network-token":      "synthetix",
    "havven":                       "synthetix",    # alt gecko_id for SNX
    "liquity":                      "liquity",
    "1inch":                        "1inch",
    "frax-share":                   "frax",
    # Fixable gaps
    "morpho":                       "morpho-blue",  # morpho slug → $0; morpho-blue → $7.43B
    "virtuals-protocol":            "virtuals-protocol",
}


@dataclass
class Token:
    id: str
    symbol: str
    rank: int
    defillama_slug: str | None   # DeFi protocol slug → /protocol/{slug} TVL + /summary/dexs/{slug} vol
    chain_name: str | None       # DefiLlama chain slug → /v2/historicalChainTvl + /overview/dexs
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
    exclude_categories = exclude_categories or set()
    exclude_tokens = exclude_tokens or set()

    async with httpx.AsyncClient() as client:
        if api is not None:
            markets, excluded_ids = await asyncio.gather(
                _fetch_top_300(client),
                _fetch_excluded_ids(client, exclude_categories),
            )
            llama_map: dict[str, str] | None = None
            cg_supported: set[str] | None = None
        else:
            markets, excluded_ids, cg_supported, llama_map = await asyncio.gather(
                _fetch_top_300(client),
                _fetch_excluded_ids(client, exclude_categories),
                _fetch_coinglass_supported(client),
                _fetch_defillama_protocols(client),
            )

    excluded_ids |= STABLE_IDS | RWA_IDS | WRAPPED_IDS | PRESET_EXCLUDED_IDS

    out: list[Token] = []
    for c in markets[:top_n]:
        if c["id"] in excluded_ids:
            continue
        if c["id"] in exclude_tokens:
            continue
        sym = c["symbol"].upper()

        if api is not None:
            # coins-markets top-500 gets the fast path; supported-coins gets pairs-markets fallback
            has_coinglass = sym in api._cglass_today or sym in api._cglass_supported
        else:
            has_coinglass = sym in (cg_supported or set())

        # Slug resolution priority:
        # 1. CHAIN_MAP → L1/L2 chain: set chain_name, clear defillama_slug
        #    (prevents wrong gecko_id matches like BNB → "binance-smart-chain" bridge)
        # 2. SLUG_OVERRIDES → explicit protocol slug
        # 3. gecko_id lookup (api cache or standalone fetch)
        cg_id = c["id"]
        chain_name: str | None = CHAIN_MAP.get(cg_id)
        if chain_name is not None:
            defillama_slug: str | None = None
        elif cg_id in SLUG_OVERRIDES:
            defillama_slug = SLUG_OVERRIDES[cg_id]
        elif api is not None:
            defillama_slug = api.defillama_slug(cg_id)
        else:
            defillama_slug = (llama_map or {}).get(cg_id)

        out.append(Token(
            id=cg_id,
            symbol=sym,
            rank=c["market_cap_rank"] or 999,
            defillama_slug=defillama_slug,
            chain_name=chain_name,
            has_coinglass=has_coinglass,
        ))
    return out
