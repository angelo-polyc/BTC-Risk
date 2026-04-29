"""Pull today's metrics, merge into the rolling 30d window, write atomically."""
import os
import json
import asyncio
import statistics
from pathlib import Path
from datetime import date, timedelta

from sources import SourceAPI
from universe import resolve_universe

DATA_FILE = Path(os.environ.get("DATA_DIR", "/data")) / "divergence.json"
RETENTION_DAYS = 30

EXCLUDE_CATEGORIES = {
    "Stablecoins", "Wrapped-Tokens", "Liquid-Staked-Tokens",
    "Real World Assets",   # tokenized treasuries, RWA funds (BUIDL, USYC, JAAA, etc.)
}
PRESET_TOKENS = {
    "bitcoin", "ethereum", "solana", "hyperliquid", "syrup", "ether-fi",
    "ethena", "grass", "bittensor", "creator-chain", "berachain-bera", "aleo",
}


def load_state() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"as_of": None, "universe": []}


def write_atomic(state: dict) -> None:
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(DATA_FILE)


_MIN_POINTS = 5  # minimum series length for a meaningful z-score


def _vals(series: list) -> list[float]:
    return [r["v"] for r in sorted(series, key=lambda r: r["d"]) if r.get("v") is not None]


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


def compute_zscores(metrics: dict) -> dict:
    """Compute z-scores over the 30d series for each metric.

    LEVEL z: how anomalous is today's absolute value.
    DELTA z: how anomalous is today's 24h % change.
    Returns None for any metric with insufficient history (< 5 points).
    """
    def z(metric, kind):
        v = _vals(metrics.get(metric, []))
        return _level_z(v) if kind == "level" else _delta_z(v)

    return {
        "price_z":      z("price",        "level"),
        "price_dz":     z("price",        "delta"),
        "spot_vol_dz":  z("spot_vol",     "delta"),
        "oi_dz":        z("oi",           "delta"),
        "funding_z":    z("funding_apr",  "level"),
        "perp_vol_dz":  z("perp_vol",     "delta"),
        "liq_ratio_z":  z("liq_oi_ratio", "level"),
        "tvl_dz":       z("tvl",          "delta"),
        "dex_vol_dz":   z("dex_vol",      "delta"),
    }


def merge_metric_series(existing: list, today: date, value: float | None) -> list:
    today_str = today.isoformat()
    cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
    series = [r for r in existing if r["d"] != today_str and r["d"] >= cutoff]
    if value is not None:
        series.append({"d": today_str, "v": value})
    series.sort(key=lambda r: r["d"])
    return series


async def run_ingest() -> None:
    today = date.today()
    print(f"[ingest] starting for {today}")
    state = load_state()
    existing_by_id = {t["id"]: t for t in state.get("universe", [])}

    async with SourceAPI() as api:
        # Resolve universe first (needs coinglass/llama caches from prep_run)
        # We do a lightweight prep_run without batch prices, then re-run with ids
        await api.prep_run()

        universe = await resolve_universe(
            top_n=300,
            exclude_categories=EXCLUDE_CATEGORIES,
            exclude_tokens=PRESET_TOKENS,
            api=api,
        )
        print(f"[ingest] universe size: {len(universe)}")

        # Batch-fetch all prices in 2 CG calls instead of 291 individual ones
        await api.prep_run(token_ids=[t.id for t in universe])

        sem = asyncio.Semaphore(8)

        async def pull(t):
            out = {
                "price": None, "spot_vol": None, "oi": None, "funding_apr": None,
                "perp_vol": None, "liq_oi_ratio": None, "tvl": None, "dex_vol": None,
            }
            async with sem:
                px, vol, dex_vol_snap = await api.price_volume(t.id)
                out["price"] = px
                out["spot_vol"] = vol
                out["dex_vol"] = dex_vol_snap   # per-token DEX trading vol from CG tickers
                if t.has_coinglass:
                    derivs = await api.derivatives(t.symbol)
                    if derivs:
                        out["oi"] = derivs.oi
                        out["funding_apr"] = derivs.funding_apr
                        out["perp_vol"] = derivs.perp_vol
                        out["liq_oi_ratio"] = derivs.liq_oi_ratio
                if t.defillama_slug or t.dex_chain:
                    tvl, _ = await api.protocol(t.defillama_slug, t.dex_chain)
                    out["tvl"] = tvl            # DefiLlama TVL only; dex_vol comes from CG
            return t, out

        results = await asyncio.gather(*(pull(t) for t in universe))

    # Merge into rolling state (outside the SourceAPI context — client already closed)
    new_universe = []
    for t, today_metrics in results:
        existing = existing_by_id.get(t.id, {"metrics": {m: [] for m in today_metrics}})
        merged = {
            metric: merge_metric_series(
                existing.get("metrics", {}).get(metric, []), today, val
            )
            for metric, val in today_metrics.items()
        }
        new_universe.append({
            "id": t.id,
            "symbol": t.symbol,
            "rank": t.rank,
            "defillama_slug": t.defillama_slug,
            "coinglass_coverage": t.has_coinglass,
            "metrics": merged,
            "zscores": compute_zscores(merged),
        })

    state = {"as_of": today.isoformat(), "universe": new_universe}
    write_atomic(state)
    size_kb = DATA_FILE.stat().st_size // 1024
    print(f"[ingest] wrote {DATA_FILE} — {len(new_universe)} tokens, ~{size_kb}KB")
