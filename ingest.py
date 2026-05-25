"""Pull today's metrics, merge into the rolling 30d window, write atomically."""
import os
import json
import asyncio
import statistics
from pathlib import Path
from datetime import date, timedelta

from sources import SourceAPI
from universe import resolve_universe

DATA_FILE          = Path(os.environ.get("DATA_DIR", "/data")) / "divergence.json"
ZSCORE_HISTORY_FILE = Path(os.environ.get("DATA_DIR", "/data")) / "zscore_history.json"
RETENTION_DAYS     = 90   # raw metric rolling window
ZSCORE_WINDOW      = 30   # points used for z-score computation (unchanged)
ZSCORE_HISTORY_RETENTION = 90

EXCLUDE_CATEGORIES = {
    "Stablecoins", "Wrapped-Tokens", "Liquid-Staked-Tokens",
    "Real World Assets",
}
PRESET_TOKENS: set[str] = set()  # no longer excluded; all watchlist tokens tracked in scanner


def load_state() -> dict:
    if DATA_FILE.exists():
        return json.loads(DATA_FILE.read_text())
    return {"as_of": None, "universe": []}


def write_atomic(state: dict) -> None:
    tmp = DATA_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(DATA_FILE)


_MIN_POINTS = 5


def _vals(series: list) -> list[float]:
    """Last ZSCORE_WINDOW non-null values sorted by date. Caps at 30 regardless of
    how many raw points are stored so z-scores stay on a consistent 30-day baseline."""
    all_vals = [r["v"] for r in sorted(series, key=lambda r: r["d"]) if r.get("v") is not None]
    return all_vals[-ZSCORE_WINDOW:]


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


def _load_zscore_history() -> dict:
    if ZSCORE_HISTORY_FILE.exists():
        try:
            return json.loads(ZSCORE_HISTORY_FILE.read_text())
        except (OSError, ValueError):
            pass
    return {"as_of": None, "series": {}}


def _write_zscore_history(state: dict) -> None:
    tmp = ZSCORE_HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(ZSCORE_HISTORY_FILE)


def _append_zscore_history(token_id: str, symbol: str, today: date,
                            zscores: dict, state: dict) -> None:
    today_str = today.isoformat()
    cutoff    = (today - timedelta(days=ZSCORE_HISTORY_RETENTION)).isoformat()
    token_entry = state["series"].setdefault(token_id, {"symbol": symbol})
    for metric, val in zscores.items():
        if val is None:
            continue
        series = [pt for pt in token_entry.get(metric, [])
                  if pt["d"] != today_str and pt["d"] >= cutoff]
        series.append({"d": today_str, "v": val})
        token_entry[metric] = sorted(series, key=lambda x: x["d"])


async def seed_zscore_history() -> dict:
    """One-time retroactive seed: slides a 30-day window over the stored raw
    metrics and computes a daily z-score for each date where ≥5 points exist.
    Call POST /seed_zscore_history after a 90-day backfill completes."""
    if not DATA_FILE.exists():
        return {"error": "no data file — run backfill first"}
    state   = json.loads(DATA_FILE.read_text())
    universe = state.get("universe", [])
    history  = {"as_of": state.get("as_of"), "series": {}}
    total_dates = 0

    for token in universe:
        token_id = token["id"]
        symbol   = token.get("symbol", "")
        metrics  = token.get("metrics", {})

        # Union of all dates present across metrics
        all_dates = sorted({pt["d"] for m in metrics.values() for pt in m})
        history["series"][token_id] = {"symbol": symbol}

        for d_str in all_dates:
            # Build 30-point window ending on d_str for each metric
            windowed: dict[str, list] = {}
            for m_name, series in metrics.items():
                pts = sorted((pt for pt in series if pt["d"] <= d_str),
                             key=lambda x: x["d"])
                windowed[m_name] = pts[-ZSCORE_WINDOW:]

            zs = compute_zscores(windowed)
            for metric, val in zs.items():
                if val is None:
                    continue
                history["series"][token_id].setdefault(metric, []).append(
                    {"d": d_str, "v": val}
                )
        total_dates += len(all_dates)

    _write_zscore_history(history)
    return {"seeded_tokens": len(universe), "total_date_points": total_dates}


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
            out = {
                "price": None, "spot_vol": None, "oi": None, "funding_apr": None,
                "perp_vol": None, "liq_oi_ratio": None, "tvl": None, "dex_vol": None,
            }
            mcap = None
            async with sem:
                px, vol = await api.price_volume(t.id)
                out["price"] = px
                out["spot_vol"] = vol
                mcap = api.market_cap_usd(t.id)

                if t.has_coinglass:
                    derivs = await api.derivatives(t.symbol)
                    if derivs:
                        out["oi"] = derivs.oi
                        out["funding_apr"] = derivs.funding_apr
                        out["perp_vol"] = derivs.perp_vol
                        out["liq_oi_ratio"] = derivs.liq_oi_ratio

                # TVL and DEX vol: DefiLlama for both protocol and chain tokens
                # (consistent with backfill — no CG tickers dex_vol)
                if t.defillama_slug or t.chain_name:
                    tvl, dexv = await api.protocol(t.defillama_slug, t.chain_name)
                    out["tvl"] = tvl
                    out["dex_vol"] = dexv

            return t, out, mcap

        results = await asyncio.gather(*(pull(t) for t in universe))

    new_universe = []
    for t, today_metrics, mcap in results:
        existing = existing_by_id.get(t.id, {"metrics": {m: [] for m in today_metrics}})
        merged = {
            metric: merge_metric_series(
                existing.get("metrics", {}).get(metric, []), today, val
            )
            for metric, val in today_metrics.items()
        }
        entry = {
            "id": t.id,
            "symbol": t.symbol,
            "rank": t.rank,
            "market_cap_usd": mcap,
            "defillama_slug": t.defillama_slug,
            "chain_name": t.chain_name,
            "coinglass_coverage": t.has_coinglass,
            "metrics": merged,
            "zscores": compute_zscores(merged),
        }
        # Preserve existing market_cap_usd if today's pull missed it
        if mcap is None and "market_cap_usd" in existing:
            entry["market_cap_usd"] = existing["market_cap_usd"]
        new_universe.append(entry)

    state = {"as_of": today.isoformat(), "universe": new_universe}
    write_atomic(state)
    size_kb = DATA_FILE.stat().st_size // 1024
    print(f"[ingest] wrote {DATA_FILE} — {len(new_universe)} tokens, ~{size_kb}KB")

    # Append today's z-scores to rolling history
    zs_history = _load_zscore_history()
    for entry in new_universe:
        _append_zscore_history(entry["id"], entry["symbol"], today,
                               entry["zscores"], zs_history)
    zs_history["as_of"] = today.isoformat()
    _write_zscore_history(zs_history)
    print(f"[ingest] updated zscore history — {len(zs_history['series'])} tokens")
