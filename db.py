"""Database layer — asyncpg. All reads/writes go through here."""
from __future__ import annotations

import os
from datetime import date, timedelta

import asyncpg

# Railway provides postgres://, asyncpg requires postgresql://
DATABASE_URL = os.environ.get("DATABASE_URL", "").replace("postgres://", "postgresql://", 1)

PRICE_RETENTION   = 430   # days kept in mom_raw_series for price panel
HISTORY_RETENTION = 365   # days kept in mom_scores_history
RAW_PANELS = ["price", "taker_buy", "taker_sell", "funding", "ls_global"]


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
            CREATE TABLE IF NOT EXISTS mom_tokens (
                symbol          TEXT PRIMARY KEY,
                cg_id           TEXT,
                rank            INTEGER,
                market_cap_usd  REAL,
                updated_at      TEXT NOT NULL
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mom_raw_series (
                symbol  TEXT NOT NULL,
                panel   TEXT NOT NULL,
                date    TEXT NOT NULL,
                value   REAL NOT NULL,
                PRIMARY KEY (symbol, panel, date)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mom_raw_series_lookup
                ON mom_raw_series (symbol, panel, date DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mom_scores (
                symbol        TEXT PRIMARY KEY,
                as_of         TEXT NOT NULL,
                score         REAL,
                rank_pct      REAL,
                res14_z       REAL,
                raw14_z       REAL,
                raw7_z        REAL,
                cvd_pct       REAL,
                ls_ext_short  BOOLEAN
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mom_regime (
                id         INTEGER PRIMARY KEY DEFAULT 1,
                as_of      TEXT NOT NULL,
                regime     TEXT NOT NULL,
                gate_on    BOOLEAN NOT NULL,
                btc_price  REAL,
                btc_ma200  REAL,
                n_tokens   INTEGER
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS mom_scores_history (
                symbol    TEXT NOT NULL,
                date      TEXT NOT NULL,
                rank_pct  REAL NOT NULL,
                PRIMARY KEY (symbol, date)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mom_scores_history_date
                ON mom_scores_history (date DESC)
        """)


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------

async def upsert_tokens_batch(pool: asyncpg.Pool, tokens: list[dict]) -> None:
    """Upsert many tokens in a single transaction.

    tokens: [{"symbol": "BTC", "cg_id": "bitcoin", "rank": 1, "market_cap_usd": 1.5e12}]
    """
    if not tokens:
        return
    from datetime import datetime, timezone
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO mom_tokens (symbol, cg_id, rank, market_cap_usd, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (symbol) DO UPDATE SET
                    cg_id          = EXCLUDED.cg_id,
                    rank           = EXCLUDED.rank,
                    market_cap_usd = EXCLUDED.market_cap_usd,
                    updated_at     = EXCLUDED.updated_at
            """, [
                (
                    t["symbol"],
                    t.get("cg_id"),
                    t.get("rank"),
                    t.get("market_cap_usd"),
                    now,
                )
                for t in tokens
            ])


# ---------------------------------------------------------------------------
# Raw series
# ---------------------------------------------------------------------------

async def upsert_raw_series(
    pool: asyncpg.Pool,
    symbol: str,
    panel: str,
    points: list[dict],
) -> None:
    """Upsert a list of {d, v} points for one symbol/panel.

    points: [{"d": "2026-05-27", "v": 109432.0}, ...]
    """
    if not points:
        return
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO mom_raw_series (symbol, panel, date, value)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (symbol, panel, date) DO UPDATE SET value = EXCLUDED.value
        """, [(symbol, panel, p["d"], float(p["v"])) for p in points])


async def get_raw_panel(pool: asyncpg.Pool, panel: str, days: int) -> dict[str, list[dict]]:
    """Return {symbol: [{d, v}, ...]} for all tokens in the last `days` days."""
    from datetime import datetime, timezone
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT symbol, date, value
            FROM mom_raw_series
            WHERE panel = $1 AND date >= $2
            ORDER BY symbol, date
        """, panel, cutoff)
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["symbol"], []).append({"d": r["date"], "v": r["value"]})
    return out


async def get_raw_panel_for_scoring(pool: asyncpg.Pool, panel: str, days: int) -> dict:
    """Same as get_raw_panel but returns all data needed for scoring."""
    return await get_raw_panel(pool, panel, days)


# ---------------------------------------------------------------------------
# Scores
# ---------------------------------------------------------------------------

async def upsert_scores_batch(pool: asyncpg.Pool, scores: list[dict]) -> None:
    """Upsert one row per token into mom_scores.

    scores: [{"symbol": "BTC", "as_of": "2026-05-27", "score": 1.23, "rank_pct": 0.95, ...}]
    """
    if not scores:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""
                INSERT INTO mom_scores
                    (symbol, as_of, score, rank_pct, res14_z, raw14_z, raw7_z, cvd_pct, ls_ext_short)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (symbol) DO UPDATE SET
                    as_of        = EXCLUDED.as_of,
                    score        = EXCLUDED.score,
                    rank_pct     = EXCLUDED.rank_pct,
                    res14_z      = EXCLUDED.res14_z,
                    raw14_z      = EXCLUDED.raw14_z,
                    raw7_z       = EXCLUDED.raw7_z,
                    cvd_pct      = EXCLUDED.cvd_pct,
                    ls_ext_short = EXCLUDED.ls_ext_short
            """, [
                (
                    s["symbol"],
                    s["as_of"],
                    s.get("score"),
                    s.get("rank_pct"),
                    s.get("res14_z"),
                    s.get("raw14_z"),
                    s.get("raw7_z"),
                    s.get("cvd_pct"),
                    s.get("ls_ext_short"),
                )
                for s in scores
            ])


async def get_all_scores(pool: asyncpg.Pool) -> tuple[list[dict], dict]:
    """Returns (scores_list, regime_dict) — the full /scores response."""
    async with pool.acquire() as conn:
        score_rows = await conn.fetch("""
            SELECT symbol, as_of, score, rank_pct, res14_z, raw14_z, raw7_z,
                   cvd_pct, ls_ext_short
            FROM mom_scores
            ORDER BY rank_pct DESC NULLS LAST
        """)
        regime_row = await conn.fetchrow("""
            SELECT as_of, regime, gate_on, btc_price, btc_ma200, n_tokens
            FROM mom_regime
            WHERE id = 1
        """)

    scores_list = [dict(r) for r in score_rows]
    regime = dict(regime_row) if regime_row else {}
    return scores_list, regime


# ---------------------------------------------------------------------------
# Regime
# ---------------------------------------------------------------------------

async def upsert_regime(pool: asyncpg.Pool, regime: dict) -> None:
    """Upsert the singleton regime row (id always = 1).

    regime: {"as_of": "...", "regime": "bear", "gate_on": False, "btc_price": 75000,
             "btc_ma200": 80000, "n_tokens": 291}
    """
    async with pool.acquire() as conn:
        await conn.execute("""
            INSERT INTO mom_regime (id, as_of, regime, gate_on, btc_price, btc_ma200, n_tokens)
            VALUES (1, $1, $2, $3, $4, $5, $6)
            ON CONFLICT (id) DO UPDATE SET
                as_of     = EXCLUDED.as_of,
                regime    = EXCLUDED.regime,
                gate_on   = EXCLUDED.gate_on,
                btc_price = EXCLUDED.btc_price,
                btc_ma200 = EXCLUDED.btc_ma200,
                n_tokens  = EXCLUDED.n_tokens
        """,
        regime["as_of"], regime["regime"], bool(regime["gate_on"]),
        regime.get("btc_price"), regime.get("btc_ma200"), regime.get("n_tokens"))


async def get_regime(pool: asyncpg.Pool) -> dict:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT as_of, regime, gate_on, btc_price, btc_ma200, n_tokens
            FROM mom_regime
            WHERE id = 1
        """)
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Scores history
# ---------------------------------------------------------------------------

async def upsert_scores_history_batch(pool: asyncpg.Pool, rows: list[dict]) -> None:
    """Upsert daily rank_pct snapshots.

    rows: [{"symbol": "BTC", "date": "2026-05-27", "rank_pct": 0.95}, ...]
    """
    if not rows:
        return
    async with pool.acquire() as conn:
        await conn.executemany("""
            INSERT INTO mom_scores_history (symbol, date, rank_pct)
            VALUES ($1, $2, $3)
            ON CONFLICT (symbol, date) DO UPDATE SET rank_pct = EXCLUDED.rank_pct
        """, [(r["symbol"], r["date"], float(r["rank_pct"])) for r in rows])


async def get_scores_history(pool: asyncpg.Pool, days: int = 365) -> dict:
    """Returns {"dates": [...], "tokens": [...], "rank_pcts": [[...]]} — same shape as current endpoint."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT date, symbol, rank_pct
            FROM mom_scores_history
            WHERE date >= $1
            ORDER BY date, symbol
        """, cutoff)

    if not rows:
        return {"dates": [], "tokens": [], "rank_pcts": []}

    # Collect sorted unique dates and tokens
    dates_set:   set[str] = set()
    tokens_set:  set[str] = set()
    data: dict[tuple[str, str], float] = {}

    for r in rows:
        dates_set.add(r["date"])
        tokens_set.add(r["symbol"])
        data[(r["date"], r["symbol"])] = r["rank_pct"]

    dates  = sorted(dates_set)
    tokens = sorted(tokens_set)

    rank_pcts = [
        [round(data.get((d, t), None) or 0, 4) if (d, t) in data else None
         for t in tokens]
        for d in dates
    ]

    return {"dates": dates, "tokens": tokens, "rank_pcts": rank_pcts}


# ---------------------------------------------------------------------------
# Retention sweep
# ---------------------------------------------------------------------------

async def apply_retention(pool: asyncpg.Pool, today: date) -> None:
    """Prune mom_raw_series older than PRICE_RETENTION days and
    mom_scores_history older than HISTORY_RETENTION days."""
    price_cutoff   = (today - timedelta(days=PRICE_RETENTION)).isoformat()
    history_cutoff = (today - timedelta(days=HISTORY_RETENTION)).isoformat()
    async with pool.acquire() as conn:
        r1 = await conn.execute(
            "DELETE FROM mom_raw_series WHERE date < $1", price_cutoff
        )
        r2 = await conn.execute(
            "DELETE FROM mom_scores_history WHERE date < $1", history_cutoff
        )
    deleted_raw  = int(r1.split()[-1])
    deleted_hist = int(r2.split()[-1])
    if deleted_raw or deleted_hist:
        print(f"[db] retention: pruned {deleted_raw} raw_series rows, {deleted_hist} history rows")
