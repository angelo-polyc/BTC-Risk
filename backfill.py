"""One-shot 30d history loader. Run once after first deploy to seed the JSON."""
import asyncio
import json
from datetime import date
from pathlib import Path

from sources import SourceAPI
from universe import resolve_universe
from ingest import DATA_FILE, EXCLUDE_CATEGORIES, PRESET_TOKENS, compute_zscores


async def pull_history_single(t, sem: asyncio.Semaphore, api) -> tuple:
    """Fetch 30d history for a single token. Shared by full and selective backfill."""
    metrics: dict[str, list] = {m: [] for m in
        ["price", "spot_vol", "oi", "funding_apr", "perp_vol", "liq_oi_ratio", "tvl", "dex_vol"]}
    async with sem:
        try:
            for d, px, vol in await api.price_history_30d(t.id):
                metrics["price"].append({"d": d.isoformat(), "v": px})
                metrics["spot_vol"].append({"d": d.isoformat(), "v": vol})
        except Exception as e:
            print(f"[backfill] {t.symbol} price_history failed: {e}")

        # Derivs (oi/funding/perp_vol/liq) deliberately excluded from backfill.
        # 289 tokens x 4 Coinglass history calls = 1156 requests — hits rate limits.
        # Coinglass rate-limits by black-holing sockets (no 429, just silence).
        # Daily ingest handles derivs at sustainable rate (1 call/token/day).

        if t.defillama_slug or t.chain_name:
            try:
                for d, tvl, dexv in await api.protocol_history_30d(t.defillama_slug, t.chain_name):
                    if tvl is not None:
                        metrics["tvl"].append({"d": d.isoformat(), "v": tvl})
                    if dexv is not None:
                        metrics["dex_vol"].append({"d": d.isoformat(), "v": dexv})
            except Exception as e:
                print(f"[backfill] {t.symbol} protocol_history failed: {e}")

    print(f"[backfill] {t.symbol} — price={len(metrics['price'])} oi={len(metrics['oi'])} "
          f"tvl={len(metrics['tvl'])} dex_vol={len(metrics['dex_vol'])}")
    return t, metrics


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
        results = await asyncio.gather(*(pull_history_single(t, sem, api) for t in universe))

    # Merge logic: backfill wins if it fetched ≥5 data points for a metric.
    # If backfill got <5 pts (endpoint had no coverage) but existing data has ≥5,
    # keep existing to preserve ingest-accumulated history.
    DERIVS = {"oi", "funding_apr", "perp_vol", "liq_oi_ratio"}

    out_universe = []
    for t, metrics in results:
        existing = existing_by_id.get(t.id, {})
        existing_metrics = existing.get("metrics", {})
        final_metrics = dict(metrics)
        for m in DERIVS:
            bf_pts = len(final_metrics.get(m) or [])
            ex_pts = len(existing_metrics.get(m) or [])
            if bf_pts < 5 and ex_pts >= 5:
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


async def backfill_symbols(symbols: list[str]) -> None:
    """Backfill specific tokens by symbol (e.g. ['BTC', 'ETH']).
    Merges into existing data — only updates the matched tokens."""
    if not DATA_FILE.exists():
        print(f"[backfill] no data file, run full backfill first")
        return

    syms = {s.upper() for s in symbols}
    existing_state = json.loads(DATA_FILE.read_text())
    existing_by_id = {t["id"]: t for t in existing_state.get("universe", [])}
    existing_by_sym = {t["symbol"].upper(): t["id"] for t in existing_state.get("universe", [])}

    async with SourceAPI() as api:
        await api.prep_run()
        universe = await resolve_universe(
            top_n=300, exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS, api=api,
        )
        targets = [t for t in universe if t.symbol.upper() in syms]
        if not targets:
            print(f"[backfill] no matching tokens found for {syms}")
            return
        print(f"[backfill] selective: {[t.symbol for t in targets]}")

        sem = asyncio.Semaphore(20)
        results = await asyncio.gather(*(pull_history_single(t, sem, api) for t in targets))

    DERIVS = {"oi", "funding_apr", "perp_vol", "liq_oi_ratio"}
    updated = 0
    for t, metrics in results:
        existing = existing_by_id.get(t.id, {})
        existing_metrics = existing.get("metrics", {})
        final_metrics = dict(metrics)
        for m in DERIVS:
            bf_pts = len(final_metrics.get(m) or [])
            ex_pts = len(existing_metrics.get(m) or [])
            if bf_pts < 5 and ex_pts >= 5:
                final_metrics[m] = existing_metrics[m]
        existing_by_id[t.id] = {
            "id": t.id, "symbol": t.symbol, "rank": t.rank,
            "defillama_slug": t.defillama_slug, "chain_name": t.chain_name,
            "coinglass_coverage": t.has_coinglass,
            "metrics": final_metrics, "zscores": compute_zscores(final_metrics),
        }
        updated += 1

    out = list(existing_by_id.values())
    state = {"as_of": existing_state.get("as_of", date.today().isoformat()), "universe": out}
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(DATA_FILE)
    print(f"[backfill] selective complete — updated {updated} tokens")


if __name__ == "__main__":
    asyncio.run(main())
