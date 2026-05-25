"""One-shot 30d history loader. Run once after first deploy to seed the JSON."""
import asyncio
import json
from datetime import date
from pathlib import Path

from sources import SourceAPI
from universe import resolve_universe
from ingest import DATA_FILE, EXCLUDE_CATEGORIES, PRESET_TOKENS, compute_zscores


async def main() -> None:
    # Load existing data — backfill will preserve ingest-built derivs series
    # where the history endpoints return sparse results
    existing_by_id: dict = {}
    if DATA_FILE.exists():
        try:
            old = json.loads(DATA_FILE.read_text())
            existing_by_id = {t["id"]: t for t in old.get("universe", [])}
            print(f"[backfill] loaded {len(existing_by_id)} existing tokens to merge")
        except Exception as e:
            print(f"[backfill] could not load existing data: {e}")

    async with SourceAPI() as api:
        await api.prep_run()

        universe = await resolve_universe(
            top_n=300,
            exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS,
            api=api,
        )
        print(f"[backfill] universe: {len(universe)} tokens")

        sem = asyncio.Semaphore(20)

        async def pull_history(t):
            metrics: dict[str, list] = {m: [] for m in
                ["price", "spot_vol", "oi", "funding_apr", "perp_vol",
                 "liq_oi_ratio", "tvl", "dex_vol"]}
            async with sem:
                try:
                    for d, px, vol in await api.price_history_30d(t.id):
                        metrics["price"].append({"d": d.isoformat(), "v": px})
                        metrics["spot_vol"].append({"d": d.isoformat(), "v": vol})
                except Exception as e:
                    print(f"[backfill] {t.symbol} price_history failed: {e}")

                # Derivs (oi, funding_apr, perp_vol, liq_oi_ratio) are NOT seeded
                # by backfill — Coinglass aggregated-history endpoints only cover
                # ~75 tokens vs the 237+ the daily ingest reaches via coins-markets
                # + pairs-markets fallback. Derivs history is built by daily ingest.

                if t.defillama_slug or t.chain_name:
                    try:
                        for d, tvl, dexv in await api.protocol_history_30d(t.defillama_slug, t.chain_name):
                            if tvl is not None:
                                metrics["tvl"].append({"d": d.isoformat(), "v": tvl})
                            if dexv is not None:
                                metrics["dex_vol"].append({"d": d.isoformat(), "v": dexv})
                    except Exception as e:
                        print(f"[backfill] {t.symbol} protocol_history failed: {e}")

            print(f"[backfill] {t.symbol} — "
                  f"price={len(metrics['price'])} oi={len(metrics['oi'])} "
                  f"tvl={len(metrics['tvl'])} dex_vol={len(metrics['dex_vol'])}")
            return t, metrics

        results = await asyncio.gather(*(pull_history(t) for t in universe))

    # Backfill owns: price, spot_vol, tvl, dex_vol (reliable 30d history endpoints).
    # Daily ingest owns: oi, funding_apr, perp_vol, liq_oi_ratio (237-token coverage).
    # Always restore derivs from existing data so backfill never overwrites them.
    INGEST_OWNED = {"oi", "funding_apr", "perp_vol", "liq_oi_ratio"}

    out_universe = []
    for t, metrics in results:
        existing = existing_by_id.get(t.id, {})
        existing_metrics = existing.get("metrics", {})
        final_metrics = dict(metrics)
        for m in INGEST_OWNED:
            if existing_metrics.get(m):
                final_metrics[m] = existing_metrics[m]
        out_universe.append({
            "id": t.id,
            "symbol": t.symbol,
            "rank": t.rank,
            "defillama_slug": t.defillama_slug,
            "chain_name": t.chain_name,
            "coinglass_coverage": t.has_coinglass,
            "metrics": final_metrics,
            "zscores": compute_zscores(final_metrics),
        })

    state = {"as_of": date.today().isoformat(), "universe": out_universe}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(state, separators=(",", ":")))
    size_kb = DATA_FILE.stat().st_size // 1024
    print(f"[backfill] complete — {len(out_universe)} tokens, ~{size_kb}KB → {DATA_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
