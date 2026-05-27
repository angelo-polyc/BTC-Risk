"""Database layer — all reads and writes go through here, nothing else touches Postgres."""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any

import asyncpg

# Railway provides postgres://, asyncpg requires postgresql://
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

RETENTION_DAYS       = 90
ZSCORE_WINDOW        = 30
ZSCORE_METRICS = [
    "price_z", "price_dz", "spot_vol_dz", "oi_dz", "funding_z",
    "perp_vol_dz", "liq_ratio_z", "tvl_dz", "dex_vol_dz",
]

# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

async def create_pool() -> asyncpg.Pool:
    try:
        return await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    except Exception:
        # Retry with SSL — needed when using Railway's external URL
        return await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, ssl="require")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

async def init_db(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id                  TEXT PRIMARY KEY,
                symbol              TEXT NOT NULL,
                rank                INTEGER,
                market_cap_usd      REAL,
                defillama_slug      TEXT,
                chain_name          TEXT,
                coinglass_coverage  BOOLEAN DEFAULT FALSE,
                updated_at          TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS metric_series (
                token_id  TEXT    NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
                metric    TEXT    NOT NULL,
                date      TEXT    NOT NULL,
                value     REAL    NOT NULL,
                PRIMARY KEY (token_id, metric, date)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metric_series_token_metric
                ON metric_series (token_id, metric, date DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zscores (
                token_id     TEXT PRIMARY KEY REFERENCES tokens(id) ON DELETE CASCADE,
                as_of        TEXT NOT NULL,
                price_z      REAL,
                price_dz     REAL,
                spot_vol_dz  REAL,
                oi_dz        REAL,
                funding_z    REAL,
                perp_vol_dz  REAL,
                liq_ratio_z  REAL,
                tvl_dz       REAL,
                dex_vol_dz   REAL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS zscore_history (
                token_id  TEXT  NOT NULL REFERENCES tokens(id) ON DELETE CASCADE,
                date      TEXT  NOT NULL,
                metric    TEXT  NOT NULL,
                value     REAL  NOT NULL,
                PRIMARY KEY (token_id, date, metric)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_zscore_history_token_date
                ON zscore_history (token_id, date DESC)
        """)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

async def upsert_token(pool: asyncpg.Pool, token: dict) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO tokens (id, symbol, rank, market_cap_usd, defillama_slug,
                                chain_name, coinglass_coverage, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            ON CONFLICT (id) DO UPDATE SET
                symbol             = EXCLUDED.symbol,
                rank               = EXCLUDED.rank,
                market_cap_usd     = EXCLUDED.market_cap_usd,
                defillama_slug     = EXCLUDED.defillama_slug,
                chain_name         = EXCLUDED.chain_name,
                coinglass_coverage = EXCLUDED.coinglass_coverage,
                updated_at         = EXCLUDED.updated_at
        """,
        token["id"], token["symbol"], token.get("rank"), token.get("market_cap_usd"),
        token.get("defillama_slug"), token.get("chain_name"),
        bool(token.get("coinglass_coverage")), token["updated_at"])


async def upsert_tokens_batch(pool: asyncpg.Pool, tokens: list[dict]) -> None:
    """Upsert many tokens in a single transaction."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            for token in tokens:
                await conn.execute("""
                    INSERT INTO tokens (id, symbol, rank, market_cap_usd, defillama_slug,
                                        chain_name, coinglass_coverage, updated_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (id) DO UPDATE SET
                        symbol             = EXCLUDED.symbol,
                        rank               = EXCLUDED.rank,
                        market_cap_usd     = EXCLUDED.market_cap_usd,
                        defillama_slug     = EXCLUDED.defillama_slug,
                        chain_name         = EXCLUDED.chain_name,
                        coinglass_coverage = EXCLUDED.coinglass_coverage,
                        updated_at         = EXCLUDED.updated_at
                """,
                token["id"], token["symbol"], token.get("rank"), token.get("market_cap_usd"),
                token.get("defillama_slug"), token.get("chain_name"),
                bool(token.get("coinglass_coverage")), token["updated_at"])


# ---------------------------------------------------------------------------
# Metric series
# ---------------------------------------------------------------------------

async def upsert_metric_series(
    pool: asyncpg.Pool,
    token_id: str,
    metric: str,
    points: list[dict],      # [{d: "YYYY-MM-DD", v: float}, ...]
) -> None:
    """Upsert a list of (date, value) points for one token/metric."""
    if not points:
        return
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO metric_series (token_id, metric, date, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (token_id, metric, date) DO UPDATE SET value = EXCLUDED.value
        """, [(token_id, metric, p["d"], float(p["v"])) for p in points])


async def upsert_metric_point(
    pool: asyncpg.Pool,
    token_id: str,
    metric: str,
    date_str: str,
    value: float,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO metric_series (token_id, metric, date, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (token_id, metric, date) DO UPDATE SET value = EXCLUDED.value
        """, token_id, metric, date_str, value)


async def get_metric_series(
    pool: asyncpg.Pool,
    token_id: str,
    metric: str,
    limit: int = ZSCORE_WINDOW,
) -> list[float]:
    """Return last `limit` values sorted oldest→newest — ready for z-score computation."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT value FROM metric_series
            WHERE token_id = $1 AND metric = $2
            ORDER BY date DESC
            LIMIT $3
        """, token_id, metric, limit)
    return [r["value"] for r in reversed(rows)]


async def get_metric_series_all(
    pool: asyncpg.Pool,
    token_id: str,
) -> dict[str, list[dict]]:
    """Return all metrics for a token as {metric: [{d, v}, ...]} — for backfill merge checks."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT metric, date, value FROM metric_series
            WHERE token_id = $1
            ORDER BY metric, date
        """, token_id)
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["metric"], []).append({"d": r["date"], "v": r["value"]})
    return out


async def prune_metric_series(pool: asyncpg.Pool, cutoff: str) -> int:
    """Delete metric_series rows older than cutoff (YYYY-MM-DD). Returns rows deleted."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM metric_series WHERE date < $1", cutoff
        )
    return int(result.split()[-1])


# ---------------------------------------------------------------------------
# Z-scores
# ---------------------------------------------------------------------------

async def upsert_zscores(
    pool: asyncpg.Pool,
    token_id: str,
    as_of: str,
    zs: dict[str, float | None],
) -> None:
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO zscores
                (token_id, as_of, price_z, price_dz, spot_vol_dz, oi_dz,
                 funding_z, perp_vol_dz, liq_ratio_z, tvl_dz, dex_vol_dz)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (token_id) DO UPDATE SET
                as_of        = EXCLUDED.as_of,
                price_z      = EXCLUDED.price_z,
                price_dz     = EXCLUDED.price_dz,
                spot_vol_dz  = EXCLUDED.spot_vol_dz,
                oi_dz        = EXCLUDED.oi_dz,
                funding_z    = EXCLUDED.funding_z,
                perp_vol_dz  = EXCLUDED.perp_vol_dz,
                liq_ratio_z  = EXCLUDED.liq_ratio_z,
                tvl_dz       = EXCLUDED.tvl_dz,
                dex_vol_dz   = EXCLUDED.dex_vol_dz
        """,
        token_id, as_of,
        zs.get("price_z"),    zs.get("price_dz"),   zs.get("spot_vol_dz"),
        zs.get("oi_dz"),      zs.get("funding_z"),  zs.get("perp_vol_dz"),
        zs.get("liq_ratio_z"),zs.get("tvl_dz"),     zs.get("dex_vol_dz"))


async def get_all_zscores(pool: asyncpg.Pool) -> list[dict]:
    """Full universe z-scores joined with token metadata — the dashboard /zscores payload."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT
                t.id, t.symbol, t.rank, t.market_cap_usd, t.coinglass_coverage,
                z.as_of,
                z.price_z, z.price_dz, z.spot_vol_dz, z.oi_dz, z.funding_z,
                z.perp_vol_dz, z.liq_ratio_z, z.tvl_dz, z.dex_vol_dz
            FROM tokens t
            JOIN zscores z ON z.token_id = t.id
            ORDER BY t.rank ASC NULLS LAST
        """)
    out = []
    for r in rows:
        d = dict(r)
        d["zscores"] = {m: d.pop(m) for m in ZSCORE_METRICS}
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Z-score history
# ---------------------------------------------------------------------------

async def upsert_zscore_history(
    pool: asyncpg.Pool,
    token_id: str,
    date_str: str,
    zs: dict[str, float | None],
) -> None:
    points = [(token_id, date_str, metric, zs[metric])
              for metric in ZSCORE_METRICS if zs.get(metric) is not None]
    if not points:
        return
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO zscore_history (token_id, date, metric, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (token_id, date, metric) DO UPDATE SET value = EXCLUDED.value
        """, points)


async def get_zscore_history(
    pool: asyncpg.Pool,
    token_id: str | None = None,
    since: str | None = None,
) -> dict:
    """Return zscore history shaped as {as_of, series: {token_id: {symbol, metric: [{d,v}]}}}."""
    conditions = []
    params: list[Any] = []

    if token_id:
        params.append(token_id)
        conditions.append(f"h.token_id = ${len(params)}")
    if since:
        params.append(since)
        conditions.append(f"h.date >= ${len(params)}")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    async with pool.acquire() as conn:
        rows = await conn.fetch(f"""
            SELECT h.token_id, t.symbol, h.date, h.metric, h.value
            FROM zscore_history h
            JOIN tokens t ON t.id = h.token_id
            {where}
            ORDER BY h.token_id, h.date
        """, *params)

        latest = await conn.fetchval("SELECT MAX(date) FROM zscore_history")

    series: dict = {}
    for r in rows:
        tid = r["token_id"]
        if tid not in series:
            series[tid] = {"symbol": r["symbol"]}
        series[tid].setdefault(r["metric"], []).append({"d": r["date"], "v": r["value"]})

    return {"as_of": latest, "series": series}


async def prune_zscore_history(pool: asyncpg.Pool, cutoff: str) -> int:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM zscore_history WHERE date < $1", cutoff
        )
    return int(result.split()[-1])


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

async def get_status(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        token_count = await conn.fetchval("SELECT COUNT(*) FROM tokens")
        as_of       = await conn.fetchval("SELECT MAX(as_of) FROM zscores")
        oi_count    = await conn.fetchval("""
            SELECT COUNT(DISTINCT token_id) FROM metric_series WHERE metric = 'oi'
        """)
        tvl_count   = await conn.fetchval("""
            SELECT COUNT(DISTINCT token_id) FROM metric_series WHERE metric = 'tvl'
        """)
        funding_count = await conn.fetchval("""
            SELECT COUNT(DISTINCT token_id) FROM metric_series WHERE metric = 'funding_apr'
        """)
    return {
        "as_of":   as_of,
        "tokens":  token_count,
        "oi":      oi_count,
        "tvl":     tvl_count,
        "funding": funding_count,
    }


# ---------------------------------------------------------------------------
# Retention sweep (call from ingest after each run)
# ---------------------------------------------------------------------------

async def apply_retention(pool: asyncpg.Pool, today: date) -> None:
    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
    await prune_metric_series(pool, cutoff)
    await prune_zscore_history(pool, cutoff)
