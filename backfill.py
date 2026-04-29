"""One-shot 30d history loader. Run once after first deploy to seed the JSON."""
import asyncio
import json
from datetime import date
from pathlib import Path

from sources import SourceAPI
from universe import resolve_universe
from ingest import DATA_FILE, EXCLUDE_CATEGORIES, PRESET_TOKENS, compute_zscores


async def main() -> None:
    async with SourceAPI() as api:
        await api.prep_run()

        universe = await resolve_universe(
            top_n=300,
            exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS,
            api=api,
        )
        print(f"[backfill] universe: {len(universe)} tokens")

        sem = asyncio.Semaphore(4)  # conservative — backfill hammers CG tickers

        async def pull_history(t):
            metrics: dict[str, list] = {m: [] for m in
                ["price", "spot_vol", "oi", "funding_apr", "perp_vol",
                 "liq_oi_ratio", "tvl", "dex_vol"]}
            async with sem:
                # CoinGecko 30d daily history (price + volume in one call)
                for d, px, vol in await api.price_history_30d(t.id):
                    metrics["price"].append({"d": d.isoformat(), "v": px})
                    metrics["spot_vol"].append({"d": d.isoformat(), "v": vol})

                # Coinglass 30d history
                if t.has_coinglass:
                    for d, oi, fapr, pvol, liqr in await api.derivs_history_30d(t.symbol):
                        if oi is not None:
                            metrics["oi"].append({"d": d.isoformat(), "v": oi})
                        if fapr is not None:
                            metrics["funding_apr"].append({"d": d.isoformat(), "v": fapr})
                        if pvol is not None:
                            metrics["perp_vol"].append({"d": d.isoformat(), "v": pvol})
                        if liqr is not None:
                            metrics["liq_oi_ratio"].append({"d": d.isoformat(), "v": liqr})

                # DefiLlama 30d history
                if t.defillama_slug or t.dex_chain:
                    for d, tvl, dexv in await api.protocol_history_30d(t.defillama_slug, t.dex_chain):
                        if tvl is not None:
                            metrics["tvl"].append({"d": d.isoformat(), "v": tvl})
                        if dexv is not None:
                            metrics["dex_vol"].append({"d": d.isoformat(), "v": dexv})

            print(f"[backfill] {t.symbol} done")
            return t, metrics

        results = await asyncio.gather(*(pull_history(t) for t in universe))

    out_universe = []
    for t, metrics in results:
        out_universe.append({
            "id": t.id,
            "symbol": t.symbol,
            "rank": t.rank,
            "defillama_slug": t.defillama_slug,
            "coinglass_coverage": t.has_coinglass,
            "metrics": metrics,
            "zscores": compute_zscores(metrics),
        })

    state = {"as_of": date.today().isoformat(), "universe": out_universe}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(state, separators=(",", ":")))
    size_kb = DATA_FILE.stat().st_size // 1024
    print(f"[backfill] complete — {len(out_universe)} tokens, ~{size_kb}KB → {DATA_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
