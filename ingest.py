"""Pull today's metrics, write to Postgres, compute and store z-scores."""
import asyncio
import statistics
from datetime import date

import asyncpg

import db
from sources import SourceAPI
from universe import resolve_universe

ZSCORE_WINDOW = db.ZSCORE_WINDOW
_MIN_POINTS   = 5

EXCLUDE_CATEGORIES = {
    "Stablecoins", "Wrapped-Tokens", "Liquid-Staked-Tokens",
    "Real World Assets",
}
PRESET_TOKENS: set[str] = set()

METRICS = ["price", "spot_vol", "oi", "funding_apr", "perp_vol", "liq_oi_ratio", "tvl", "dex_vol"]


# ---------------------------------------------------------------------------
# Z-score computation (logic unchanged, input is now [float] not [{d,v}])
# ---------------------------------------------------------------------------

def _level_z(values: list[float]) -> float | None:
    if len(values) < _MIN_POINTS:
        return None
    sd = statistics.pstdev(values)
    if sd == 0:
        return None
    return round((values[-1] - statistics.mean(values)) / sd, 2)


def _delta_z(values: list[float]) -> float | None:
    if len(values) < _MIN_POINTS + 1:
        return None
    pct = [(values[i] - values[i - 1]) / values[i - 1]
           for i in range(1, len(values)) if values[i - 1] != 0]
    if len(pct) < _MIN_POINTS:
        return None
    sd = statistics.pstdev(pct)
    if sd == 0:
        return None
    return round((pct[-1] - statistics.mean(pct)) / sd, 2)


def compute_zscores(windows: dict[str, list[float]]) -> dict:
    """windows: {metric_name: [float, ...]} — values in chronological order."""
    def z(metric, kind):
        vals = windows.get(metric, [])[-ZSCORE_WINDOW:]
        return _level_z(vals) if kind == "level" else _delta_z(vals)

    return {
        "price_z":     z("price",        "level"),
        "price_dz":    z("price",        "delta"),
        "spot_vol_dz": z("spot_vol",     "delta"),
        "oi_dz":       z("oi",           "delta"),
        "funding_z":   z("funding_apr",  "level"),
        "perp_vol_dz": z("perp_vol",     "delta"),
        "liq_ratio_z": z("liq_oi_ratio", "level"),
        "tvl_dz":      z("tvl",          "delta"),
        "dex_vol_dz":  z("dex_vol",      "delta"),
    }


# ---------------------------------------------------------------------------
# Seed z-score history from existing metric_series (run once after backfill)
# ---------------------------------------------------------------------------

async def seed_zscore_history(pool: asyncpg.Pool) -> dict:
    """Slide a 30-day window over metric_series and retroactively compute z-score history."""
    async with pool.acquire() as conn:
        token_rows = await conn.fetch("SELECT id, symbol FROM tokens ORDER BY rank NULLS LAST")

    total_dates = 0
    for token in token_rows:
        token_id = token["id"]

        async with pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT metric, date, value FROM metric_series
                WHERE token_id = $1
                ORDER BY metric, date
            """, token_id)

        # Group into {metric: [(date, value), ...]}
        by_metric: dict[str, list[tuple]] = {}
        for r in rows:
            by_metric.setdefault(r["metric"], []).append((r["date"], r["value"]))

        all_dates = sorted({d for pts in by_metric.values() for d, _ in pts})

        for d_str in all_dates:
            windows: dict[str, list[float]] = {}
            for metric, pts in by_metric.items():
                window = [v for d, v in pts if d <= d_str][-ZSCORE_WINDOW:]
                if window:
                    windows[metric] = window

            zs = compute_zscores(windows)
            await db.upsert_zscore_history(pool, token_id, d_str, zs)

        total_dates += len(all_dates)
        print(f"[seed] {token['symbol']} — {len(all_dates)} dates")

    return {"seeded_tokens": len(token_rows), "total_date_points": total_dates}


# ---------------------------------------------------------------------------
# Daily ingest
# ---------------------------------------------------------------------------

async def run_ingest(pool: asyncpg.Pool) -> None:
    today     = date.today()
    today_str = today.isoformat()
    print(f"[ingest] starting for {today_str}")

    async with SourceAPI() as api:
        await api.prep_run()

        universe = await resolve_universe(
            top_n=300,
            exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS,
            api=api,
        )
        print(f"[ingest] universe size: {len(universe)}")

        await api._batch_fetch_prices([t.id for t in universe])

        sem = asyncio.Semaphore(20)

        async def pull(t):
            today_vals: dict[str, float | None] = {m: None for m in METRICS}
            mcap = None
            async with sem:
                px, vol = await api.price_volume(t.id)
                today_vals["price"]    = px
                today_vals["spot_vol"] = vol
                mcap = api.market_cap_usd(t.id)

                if t.has_coinglass:
                    derivs = await api.derivatives(t.symbol)
                    if derivs:
                        today_vals["oi"]          = derivs.oi
                        today_vals["funding_apr"]  = derivs.funding_apr
                        today_vals["perp_vol"]     = derivs.perp_vol
                        today_vals["liq_oi_ratio"] = derivs.liq_oi_ratio

                if t.defillama_slug or t.chain_name:
                    tvl, dexv = await api.protocol(t.defillama_slug, t.chain_name)
                    today_vals["tvl"]     = tvl
                    today_vals["dex_vol"] = dexv

            return t, today_vals, mcap

        results = await asyncio.gather(*(pull(t) for t in universe))

    # Write to DB
    for t, today_vals, mcap in results:
        # 1. Upsert token metadata
        await db.upsert_token(pool, {
            "id":                 t.id,
            "symbol":             t.symbol,
            "rank":               t.rank,
            "market_cap_usd":     mcap,
            "defillama_slug":     t.defillama_slug,
            "chain_name":         t.chain_name,
            "coinglass_coverage": t.has_coinglass,
            "updated_at":         today_str,
        })

        # 2. Write today's metric points
        for metric, value in today_vals.items():
            if value is not None:
                await db.upsert_metric_point(pool, t.id, metric, today_str, value)

        # 3. Read 30-point windows and compute z-scores
        windows: dict[str, list[float]] = {}
        for metric in METRICS:
            vals = await db.get_metric_series(pool, t.id, metric, limit=ZSCORE_WINDOW)
            if vals:
                windows[metric] = vals

        zs = compute_zscores(windows)

        # 4. Write z-scores
        await db.upsert_zscores(pool, t.id, today_str, zs)
        await db.upsert_zscore_history(pool, t.id, today_str, zs)

    # 5. Retention sweep
    await db.apply_retention(pool, today)

    status = await db.get_status(pool)
    print(f"[ingest] done — {status['tokens']} tokens as_of {status['as_of']}")
