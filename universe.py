"""Resolve universe, attach DefiLlama slug + Coinglass coverage flag."""
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
    # No spot volume — excluded for data quality
    "nxm",                                          # NXM
    "creator-chain",                                # CRTR
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
    "nexo",                                         # NEXO
}

# Tokens to always include regardless of CG rank.
# Used for watchlist tokens that may fall outside top-300 and for
# the daily-analysis presets that are excluded from the top-300 fetch
# via exclude_tokens (they bypass that check via the pinned path).
PINNED_IDS: set[str] = {
    # Daily-analysis presets — include in divergence scanner too
    "bitcoin",          # BTC
    "ethereum",         # ETH
    "solana",           # SOL
    "hyperliquid",      # HYPE
    "syrup",            # SYRUP
    "ether-fi",         # ETHFI
    "ethena",           # ENA
    "grass",            # GRASS
    "bittensor",        # TAO
    "creator-chain",    # CC
    "berachain-bera",   # BERA
    "aleo",             # ALEO
    # Restored from PRESET_EXCLUDED_IDS — user wants these back
    "gala",             # GALA
    "rain",             # RAIN
    "shuffle-2",        # SHFL
    "celestia",         # TIA
    # New watchlist tokens (may be outside top-300)
    "act-i-the-ai-prophecy",    # ACT
    "across-protocol",          # ACX
    "aevo-exchange",            # AEVO
    "aixbt",                    # AIXBT
    "alt",                      # ALT
    "ao-computer",              # AO
    "ai-rig-complex",           # ARC
    "arkham",                   # ARKM
    "avail",                    # AVAIL
    "axelar",                   # AXL
    "b3",                       # B3
    "a-fund-baby",              # BABY
    "balancer",                 # BAL
    "banana",                   # BANANA (Banana Gun)
    "lombard-protocol",         # BARD
    "bio-protocol",             # BIO
    "blast",                    # BLAST
    "bluefin",                  # BLUE
    "blur",                     # BLUR
    "collector-crypt",          # CARDS
    "celo",                     # CELO (via chain map)
    "cetus-protocol",           # CETUS
    "chip-2",                   # CHIP
    "tokenbot-2",               # CLANKER
    "coredaoorg",               # CORE (via chain map)
    "clearpool",                # CPOOL
    "cysic",                    # CYS
    "debridge",                 # DBR
    "deep",                     # DEEP
    "degen-base",               # DEGEN
    "deus-finance-2",           # DEUS
    "drift-protocol",           # DRIFT
    "derive",                   # DRV
    "dymension",                # DYM
    "euler",                    # EUL
    "ftx-token",                # FTT
    "g-token",                  # G
    "geodnet",                  # GEOD
    "goldfinch",                # GFI
    "gmx",                      # GMX
    "goatseus-maximus",         # GOAT

    "illuvium",                 # ILV
    "initia",                   # INIT
    "infinex-2",                # INX
    "io",                       # IO
    "kinetiq",                  # KNTQ
    "layer3",                   # L3
    "lagrange",                 # LA
    "solayer",                  # LAYER
    "liquity",                  # LQTY
    "terra-luna-2",             # LUNA
    "magic",                    # MAGIC
    "mask-network",             # MASK
    "maverick-protocol",        # MAV
    "magic-eden",               # ME
    "megaeth",                  # MEGA
    "meteora",                  # MET
    "meta-2-2",                 # META
    "mina-protocol",            # MINA
    "moo-deng",                 # MOODENG
    "morpheusai",               # MOR
    "movement",                 # MOVE
    "metaplex",                 # MPLX
    "natix-network",            # NATIX
    "neon",                     # NEON
    "nillion",                  # NIL
    "notcoin",                  # NOT
    "suins-token",              # NS
    "nexpace",                  # NXPC
    "nym",                      # NYM
    "autonolas",                # OLAS
    "mantra-dao",               # OM
    "omni-network",             # OMNI
    "opengradient",             # OPG
    "orca",                     # ORCA
    "osmosis",                  # OSMO
    "paal-ai",                  # PAAL
    "peaq-2",                   # PEAQ
    "pippin",                   # PIPPIN
    "plume",                    # PLUME
    "peanut-the-squirrel",      # PNUT
    "polymath",                 # POLY
    "popcat",                   # POPCAT
    "puffer-finance",           # PUFFER
    "redstone-oracles",         # RED
    "retardio",                 # RETARDIO
    "renzo",                    # REZ
    "rollbit-coin",             # RLB
    "robo-token-2",             # ROBO
    "ronin",                    # RON
    "rocket-pool",              # RPL
    "saga-2",                   # SAGA
    "scroll",                   # SCR
    "myshell",                  # SHELL
    "sign-global",              # SIGN
    "status",                   # SNT
    "solv-protocol",            # SOLV
    "sophon",                   # SOPH
    "spectral",                 # SPEC
    "spark-2",                  # SPK
    "subsquid",                 # SQD
    "ssv-network",              # SSV
    "stargate-finance",         # STG
    "sushi",                    # SUSHI
    "sweatcoin",                # SWEAT
    "swell-network",            # SWELL
    "space-and-time",           # SXT
    "taiko",                    # TAIKO
    "tensor",                   # TNSR
    "tornado-cash",             # TORN
    "union-2",                  # U
    "uma",                      # UMA
    "usual",                    # USUAL
    "vana",                     # VANA
    "w-2",                      # W (Wormhole)
    "connect-token-wct",        # WCT
    "humidifi",                 # WET
    "xai-blockchain",           # XAI
    "anoma",                    # XAN
    "yearn-finance",            # YFI
    "yield-guild-games",        # YGG

    "zerebro",                  # ZEREBRO
    "0x",                       # ZRX
    # Tokens with DefiLlama config but outside top-300 — must be pinned
    "kamino",                   # KMNO ($1.4B TVL)
    "cow-protocol",             # COW
    "beam-2",                   # BEAM gaming chain
}

# CG ID → DefiLlama chain slug.
# Used for BOTH chain TVL (/v2/historicalChainTvl/{chain})
# and chain DEX vol (/overview/dexs/{chain}).
# Tokens here get chain_name set and defillama_slug=None,
# preventing wrong gecko_id matches (bridge/protocol entries).
CHAIN_MAP: dict[str, str] = {
    "binancecoin":              "bsc",
    "ethereum":                 "Ethereum",
    "solana":                   "Solana",
    "avalanche-2":              "avalanche",
    "tron":                     "tron",
    "the-open-network":         "ton",
    "near":                     "near",
    "arbitrum":                 "arbitrum",
    "optimism":                 "OP Mainnet",
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
    "monad":                    "Monad",
    "story-2":                  "Story",
    "plasma":                   "Plasma",
    "berachain-bera":           "Berachain",
    "ronin":                    "Ronin",
    "blast":                    "Blast",
    "osmosis":                  "Osmosis",
    "taiko":                    "Taiko",
    "dymension":                "Dymension",
    # mina-protocol: not tracked as a chain on DefiLlama
    # axelar: cross-chain messaging layer, no chain TVL on DefiLlama
    # Additional chains confirmed by DefiLlama agent audit
    "stellar":                  "Stellar",
    "worldcoin-wld":            "World Chain",
    "elrond-erd-2":             "MultiversX",   # EGLD — chain rebranded from Elrond
    "iota":                     "IOTA",
    "chiliz":                   "Chiliz",
    "apecoin":                  "ApeChain",
    "zcash":                    "Zcash",
    "litecoin":                 "Litecoin",
    "linea":                    "Linea",
    "ripple":                   "Ripple",
    "dydx-chain":               "dYdX",         # DYDX — CG ID is dydx-chain
    "beam-2":                   "Beam",         # BEAM gaming chain (Merit Circle)
    "provenance-blockchain":    "Provenance",
    "hash-2":                   "Provenance",
    # Tokens whose gecko_id matches a bridge/protocol slug — override to chain:
    "starknet":                 "Starknet",
    # Tokens with protocol slugs returning $0 — chain endpoint has real TVL:
    "vechain":                  "VeChain",
    "conflux-token":            "Conflux",
    "kaia":                     "Kaia",
    "tezos":                    "Tezos",
    "filecoin":                 "Filecoin",
    "gnosis":                   "Gnosis",       # xDai is dead (404); Gnosis = $73M
}

# CG ID → correct DefiLlama protocol slug.
# Overrides gecko_id-based lookup from /protocols list.
# Priority: SLUG_OVERRIDES > gecko_id match > None
SLUG_OVERRIDES: dict[str, str] = {
    "ether-fi":                     "ether.fi",
    "syrup":                        "maple-finance",
    "uniswap":                      "uniswap",
    "aave":                         "aave",
    "curve-dao-token":              "curve-dex",
    "pancakeswap-token":            "pancakeswap",
    "lido-dao":                     "lido",
    "compound-governance-token":    "compound-v3",
    "raydium":                      "raydium",
    "jupiter-exchange-solana":      "jupiter",
    "gmx":                          "gmx",
    "hyperliquid":                  "hyperliquid",
    "ondo-finance":                 "ondo-finance",
    "sky":                          "sky",
    "thorchain":                    "thorchain-dex",
    "pendle":                       "pendle",
    "aerodrome-finance":            "aerodrome",
    "velodrome-finance":            "velodrome",
    "balancer":                     "balancer",
    "sushi":                        "sushiswap",
    "convex-finance":               "convex-finance",
    # Exchange tokens — large TVL via exchange protocol slugs
    "leo-token":                    "bitfinex",
    "bitget-token":                 "bitget",
    "htx-dao":                      "htx",
    "kucoin-shares":                "kucoin",
    "pax-gold":                     "paxos-gold",
    # Protocol slugs from audit
    "centrifuge-2":                 "centrifuge-protocol",  # CFG — CG ID is centrifuge-2
    "jito-governance-token":        "jito-liquid-staking",  # JTO — CG ID is jito-governance-token
    "aster-2":                      "aster-bridge",
    "lighter":                      "lighter-bridge",
    "pump-fun":                     "pumpswap",
    "doublezero":                   "doublezero-staked-sol",
    "chainlink":                    "stake.link-liquid",
    "shiba-inu":                    "shibaswap-v1",
    "bonk":                         "bonk-staked-sol",
    "deltaprime":                   "deltaprime",
    "river-omni":                   "river-omni-cdp",
    "river":                        "river-omni-cdp",   # RIVER token
    "edgex":                        "edgex-bridge",     # EDGE — $150M TVL
    "kamino":                       "kamino-lend",      # KMNO — $1.4B Solana DeFi
    "cow-protocol":                 "cowswap",          # COW
    "instadapp":                    "fluid-lending",    # FLUID — $863M TVL
    "maker":                        "makerdao",
    "yearn-finance":                "yearn-finance",
    "synthetix-network-token":      "synthetix",
    "havven":                       "synthetix",
    "liquity":                      "liquity",
    "1inch":                        "1inch",
    "frax-share":                   "frax",
    "morpho":                       "morpho-blue",
    "virtuals-protocol":            "virtuals-protocol",
    # New watchlist DeFi protocols
    "ethena":                       "ethena",
    "orca":                         "orca",
    "stargate-finance":             "stargate",
    "aevo-exchange":                "aevo",
    "renzo":                        "renzo",
    "rocket-pool":                  "rocket-pool",
    "puffer-finance":               "puffer-finance",
    "swell-network":                "swell",
    "solv-protocol":                "solv-protocol",
    "usual":                        "usual",
    "drift-protocol":               "drift",
    "ssv-network":                  "ssv-network",
    "mantra-dao":                   "mantra",
    "cetus-protocol":               "cetus",
    "autonolas":                    "autonolas",
    "across-protocol":              "across",
    "gala":                         "gala",
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


async def _fetch_by_ids(client: httpx.AsyncClient, ids: set[str]) -> list[dict]:
    """Fetch specific coins from CG /coins/markets by explicit IDs (for pinned tokens)."""
    if not ids:
        return []
    headers = {"x-cg-pro-api-key": CG_KEY} if CG_KEY else {}
    out = []
    ids_list = sorted(ids)
    for i in range(0, len(ids_list), 200):
        batch = ids_list[i:i + 200]
        r = await client.get(
            f"{CG_BASE}/coins/markets",
            params={"vs_currency": "usd", "ids": ",".join(batch), "per_page": 250},
            headers=headers,
            timeout=30,
        )
        if r.status_code == 200:
            out.extend(r.json())
    return out


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
            (top_markets, pinned_markets), excluded_ids = await asyncio.gather(
                asyncio.gather(
                    _fetch_top_300(client),
                    _fetch_by_ids(client, PINNED_IDS),
                ),
                _fetch_excluded_ids(client, exclude_categories),
            )
            llama_map: dict[str, str] | None = None
            cg_supported: set[str] | None = None
        else:
            (top_markets, pinned_markets), excluded_ids, cg_supported, llama_map = await asyncio.gather(
                asyncio.gather(
                    _fetch_top_300(client),
                    _fetch_by_ids(client, PINNED_IDS),
                ),
                _fetch_excluded_ids(client, exclude_categories),
                _fetch_coinglass_supported(client),
                _fetch_defillama_protocols(client),
            )

    # Merge: top-300 first, then any pinned tokens not already in top-300.
    # Pinned tokens bypass exclude_tokens (which filters daily-analysis presets).
    top_ids = {c["id"] for c in top_markets}
    pinned_extra = [c for c in pinned_markets if c["id"] not in top_ids]
    pinned_extra_ids = {c["id"] for c in pinned_extra}

    excluded_ids |= STABLE_IDS | RWA_IDS | WRAPPED_IDS | PRESET_EXCLUDED_IDS

    out: list[Token] = []

    for c in top_markets[:top_n] + pinned_extra:
        if c["id"] in excluded_ids:
            continue
        # exclude_tokens only applies to rank-based tokens, not pinned extras
        if c["id"] in exclude_tokens and c["id"] not in pinned_extra_ids:
            continue
        sym = c["symbol"].upper()

        if api is not None:
            has_coinglass = sym in api._cglass_today or sym in api._cglass_supported
        else:
            has_coinglass = sym in (cg_supported or set())

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
