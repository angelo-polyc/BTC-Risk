#!/usr/bin/env python3
"""
pull_all_raw_data.py — V2 rebuild Phase 0 raw data pull script.

CLI:
    python code/pull_all_raw_data.py --source all --out-dir raw_data
    python code/pull_all_raw_data.py --source fred,velo_btc --dry-run
    python code/pull_all_raw_data.py --source coinglass_cycle --force
    python code/pull_all_raw_data.py --source all --dry-run --verbose

Valid --source values: all, fred, velo_btc, velo_eth, coinglass_cycle,
    coinglass_h2, coinglass_h3, cftc, price
"""

import argparse
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

# ──────────────────────────────────────────────────────────────────────────────
# Credentials (from BRIEF.md §"Data sources and API credentials")
# ──────────────────────────────────────────────────────────────────────────────
VELO_API_KEY    = "e1d6ee67e6724c0281c02e02b5c131d5"
FRED_API_KEY    = "be25f20e751efeb98e91471521e1cb57"
CG_API_KEY      = "e921deddec3f4cb2b281b49330428d47"
# v13 change (2026-04-18): Artemis folded in as source #9. Env var takes priority;
# baked-in fallback matches what pull_artemis_etf.py always used.
ARTEMIS_API_KEY = "CXDPqeI6WtowV13pHKKhOm0PFjrUJWSGUJpa-kuSMzY"

CG_V3_BASE = "https://open-api-v3.coinglass.com/api"
CG_V4_BASE = "https://open-api-v4.coinglass.com/api"

# ──────────────────────────────────────────────────────────────────────────────
# Logging
# ──────────────────────────────────────────────────────────────────────────────
log = logging.getLogger("pull_raw")

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ms(dt: datetime) -> int:
    """datetime → unix milliseconds"""
    return int(dt.timestamp() * 1000)


def _save_parquet(df: pd.DataFrame, path: Path, source_tag: str) -> None:
    """Add audit columns and write parquet."""
    df = df.copy()
    # Coerce any object columns that look numeric (e.g. Coinglass returns OHLC as strings)
    # Use try/except per column since errors='ignore' was removed in pandas 2.x
    skip_coerce = {"date", "_source", "_pulled_at", "exchange", "symbol", "metric",
                   "velo_type", "coin", "Market_and_Exchange_Names", "CFTC_Contract_Market_Code"}
    for col in df.columns:
        if col in skip_coerce:
            continue
        if df[col].dtype == object:
            coerced = pd.to_numeric(df[col], errors="coerce")
            # Only apply if conversion preserved most non-null values (i.e. column is actually numeric)
            original_nulls = df[col].isna().sum()
            new_nulls = coerced.isna().sum()
            if new_nulls <= original_nulls + max(1, len(df) * 0.1):
                df[col] = coerced
    df["_source"] = source_tag
    df["_pulled_at"] = _now_iso()
    df["_rows"] = len(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), str(path))
    log.info(f"Wrote {len(df):,} rows → {path}")


def _result(source: str, status: str, rows: int, files: List[str],
            warnings: List[str] = None) -> Dict:
    return {
        "source": source,
        "status": status,
        "rows": rows,
        "files": files,
        "warnings": warnings or [],
    }


def _cg_get(path: str, params: dict, base: str = CG_V4_BASE,
            max_retries: int = 4) -> Optional[dict]:
    """GET from Coinglass with exponential backoff on 409/500."""
    url = base.rstrip("/") + "/" + path.lstrip("/")
    headers = {"CG-API-KEY": CG_API_KEY}
    delay = 2
    for attempt in range(max_retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (409, 500, 429) and attempt < max_retries:
                log.warning(f"CG {r.status_code} on {path}, retry {attempt+1} in {delay}s")
                time.sleep(delay)
                delay *= 2
                continue
            log.error(f"CG HTTP {r.status_code} on {path}: {r.text[:200]}")
            return None
        except Exception as e:
            if attempt < max_retries:
                log.warning(f"CG request error on {path}: {e}, retry {attempt+1} in {delay}s")
                time.sleep(delay)
                delay *= 2
            else:
                log.error(f"CG request failed on {path}: {e}")
                return None
    return None


def _cg_data(resp: Optional[dict], path: str) -> Optional[list]:
    """Extract .data from CG response; log if missing."""
    if resp is None:
        return None
    if resp.get("code") != "0":
        log.error(f"CG non-zero code on {path}: {resp.get('msg')} / {resp.get('code')}")
        return None
    data = resp.get("data")
    if data is None:
        log.warning(f"CG response missing 'data' field on {path}")
        return None
    return data


# ──────────────────────────────────────────────────────────────────────────────
# FRED
# ──────────────────────────────────────────────────────────────────────────────

FRED_SERIES = [
    "SP500",          # H6 SPX overextension (pre-2014 gap — documented below)
    "DFII10",         # H6 real rate (10Y TIPS)
    "BAMLH0A0HYM2",   # H6/H7 HY spread (OAS)
    "T10Y2Y",         # H6 yield curve ROC
    "VIXCLS",         # H7 equity vol
    "DTWEXBGS",       # H7 USD strength (broad trade-weighted)
    "DEXJPUS",        # H7 USD/JPY yen-carry
    "DGS2",           # H7 2Y Treasury yield
    "DGS10",          # H7 10Y Treasury yield
    "DFF",            # H7 effective Fed Funds rate
]

# SP500 note: FRED SP500 series starts 2014-10-01. Pre-2014 proxy = SPASTT01USM661N
# or compute from Yahoo Finance. Downstream feature-build phase must handle this gap.


def pull_fred(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull 10 FRED series. One parquet per series."""
    from fredapi import Fred

    src = "fred"
    subdir = (out_dir / ".dryrun" / "fred") if dry_run else (out_dir / "fred")
    subdir.mkdir(parents=True, exist_ok=True)

    fred = Fred(api_key=FRED_API_KEY)
    files, warnings, total_rows = [], [], 0

    for series_id in FRED_SERIES:
        out_path = subdir / f"{series_id}.parquet"

        if out_path.exists() and not force and not dry_run:
            log.info(f"[fred] SKIP {series_id} (cached)")
            files.append(str(out_path))
            existing = pq.read_table(str(out_path)).to_pandas()
            total_rows += len(existing)
            continue

        try:
            delay = 2
            data = None
            for attempt in range(4):
                try:
                    if dry_run:
                        start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
                        data = fred.get_series(series_id, observation_start=start)
                    else:
                        data = fred.get_series(series_id)
                    break
                except Exception as retry_err:
                    if attempt < 3:
                        log.warning(f"[fred] {series_id} error (attempt {attempt+1}): {retry_err}, retry in {delay}s")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        raise

            if data is None or len(data) == 0:
                warnings.append(f"{series_id}: empty response")
                log.warning(f"[fred] {series_id} returned no data")
                continue

            df = data.reset_index()
            df.columns = ["date", "value"]
            df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
            df = df.dropna(subset=["value"])

            _save_parquet(df, out_path, source_tag=f"fred:{series_id}")
            files.append(str(out_path))
            total_rows += len(df)
            log.info(f"[fred] {series_id}: {len(df)} rows")

            # SP500 coverage warning
            if series_id == "SP500":
                min_date = pd.to_datetime(df["date"]).min()
                if min_date > pd.Timestamp("2000-01-01"):
                    warnings.append(
                        f"SP500: FRED series starts {min_date.date()} — pre-2014 gap exists. "
                        "Use alternate series or Yahoo Finance for pre-2014 SPX data in feature build."
                    )

        except Exception as e:
            warnings.append(f"{series_id}: {e}")
            log.error(f"[fred] {series_id} failed: {e}\n{traceback.format_exc()}")

    status = "ok" if len(warnings) == 0 else "partial"
    if total_rows == 0 and len(FRED_SERIES) > 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# Velo helpers
# ──────────────────────────────────────────────────────────────────────────────

VELO_BEGIN = "2021-01-01"  # full history begin

# Exchange → symbol mappings
VELO_BTC_PRODUCTS = {
    "binance-futures": "BTCUSDT",
    "bybit":           "BTCUSDT",
    "okex-swap":       "BTC-USDT-SWAP",
    "binance":         "BTCUSDT",
    "coinbase":        "BTC-USD",
}
VELO_ETH_PRODUCTS = {
    "binance-futures": "ETHUSDT",
    "bybit":           "ETHUSDT",
    "okex-swap":       "ETH-USDT-SWAP",
    "binance":         "ETHUSDT",
    "coinbase":        "ETH-USD",
}

VELO_FUTURES_EXCHANGES = ["binance-futures", "bybit", "okex-swap"]
VELO_SPOT_EXCHANGES    = ["binance", "coinbase"]

# Metrics per exchange type
# Correct Velo column names (verified via get_futures_columns() / get_spot_columns())
# Mapping from logical name → actual Velo column:
#   coin_oi             → coin_open_interest_close
#   buy_volume          → buy_dollar_volume
#   sell_volume         → sell_dollar_volume
#   long_liquidations   → buy_liquidations_dollar_volume  (Velo convention: buyer = long position liquidated)
#   short_liquidations  → sell_liquidations_dollar_volume
#   total_liquidations  → liquidations_dollar_volume
#   funding_rate        → funding_rate
VELO_FUTURES_METRICS = [
    "coin_open_interest_close",
    "buy_dollar_volume",
    "sell_dollar_volume",
    "buy_liquidations_dollar_volume",
    "sell_liquidations_dollar_volume",
    "liquidations_dollar_volume",
    "funding_rate",
]
VELO_SPOT_METRICS    = ["buy_dollar_volume", "sell_dollar_volume"]


def _pull_velo_asset(coin: str, products_map: dict, out_dir: Path,
                     dry_run: bool, force: bool) -> Dict:
    """Generic Velo pull for BTC or ETH."""
    import velodata.lib as vl

    src = f"velo_{coin.lower()}"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    vc = vl.client(VELO_API_KEY, retry=4)

    if dry_run:
        begin_dt = datetime.now(timezone.utc) - timedelta(days=7)
    else:
        begin_dt = datetime.fromisoformat(VELO_BEGIN).replace(tzinfo=timezone.utc)

    begin_ms = _ms(begin_dt)
    end_ms   = _ms(datetime.now(timezone.utc))

    files, warnings, total_rows = [], [], 0

    # Determine exchange groups, their metrics, and type (futures vs spot)
    exchange_metrics = {}   # exc → (metrics_list, velo_type)
    for exc in VELO_FUTURES_EXCHANGES:
        if exc in products_map:
            exchange_metrics[exc] = (VELO_FUTURES_METRICS, "futures")
    for exc in VELO_SPOT_EXCHANGES:
        if exc in products_map:
            exchange_metrics[exc] = (VELO_SPOT_METRICS, "spot")

    # Pull per metric, consolidating across exchanges
    all_metrics = set(m for (ms, _) in exchange_metrics.values() for m in ms)

    for metric in sorted(all_metrics):
        out_path = subdir / f"{metric}.parquet"

        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP {metric} (cached)")
            files.append(str(out_path))
            existing = pq.read_table(str(out_path)).to_pandas()
            total_rows += len(existing)
            continue

        metric_frames = []

        for exc, (metrics, velo_type) in exchange_metrics.items():
            if metric not in metrics:
                continue

            product = products_map[exc]
            delay = 2

            for attempt in range(5):
                try:
                    params = {
                        "type":       velo_type,
                        "exchanges":  [exc],
                        "products":   [product],
                        "columns":    [metric],
                        "begin":      begin_ms,
                        "end":        end_ms,
                        "resolution": "1d",
                    }
                    df = vc.get_rows(params)
                    if df is None or len(df) == 0:
                        log.warning(f"[{src}] {exc}/{metric}: empty response")
                        break

                    # Normalize columns
                    df = df.reset_index(drop=True)
                    # Velo returns 'time' column in ms
                    if "time" in df.columns:
                        df["date"] = pd.to_datetime(df["time"], unit="ms", utc=True).dt.date.astype(str)
                    elif "date" in df.columns:
                        df["date"] = df["date"].astype(str)

                    # Rename metric column to 'value'
                    if metric in df.columns:
                        df = df.rename(columns={metric: "value"})

                    df["exchange"]    = exc
                    df["symbol"]      = product
                    df["metric"]      = metric
                    df["velo_type"]   = velo_type
                    keep_cols = [c for c in ["date", "exchange", "symbol", "metric", "velo_type", "value"] if c in df.columns]
                    df = df[keep_cols]
                    metric_frames.append(df)
                    log.info(f"[{src}] {exc}/{metric}: {len(df)} rows")
                    break

                except Exception as e:
                    err_str = str(e)
                    # Velo misreports 409 as 500 — treat both as retriable
                    if ("500" in err_str or "409" in err_str) and attempt < 4:
                        log.warning(f"[{src}] {exc}/{metric} error (attempt {attempt+1}): {e}, retry in {delay}s")
                        time.sleep(delay)
                        delay *= 2
                    else:
                        warnings.append(f"{exc}/{metric}: {e}")
                        log.error(f"[{src}] {exc}/{metric} failed: {e}")
                        break

        if metric_frames:
            combined = pd.concat(metric_frames, ignore_index=True)
            _save_parquet(combined, out_path, source_tag=f"velo:{coin.lower()}:all_exchanges:{metric}")
            files.append(str(out_path))
            total_rows += len(combined)
        else:
            warnings.append(f"{metric}: no data from any exchange")

    status = "ok" if not warnings else "partial"
    if total_rows == 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


def pull_velo_btc(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull Velo BTC microstructure data."""
    return _pull_velo_asset("BTC", VELO_BTC_PRODUCTS, out_dir, dry_run, force)


def pull_velo_eth(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull Velo ETH microstructure data."""
    return _pull_velo_asset("ETH", VELO_ETH_PRODUCTS, out_dir, dry_run, force)


# ──────────────────────────────────────────────────────────────────────────────
# Coinglass cycle (H4v2) — 9 indicators
# NOTE: The BRIEF specifies v3 endpoint paths WITH typos (tow-year, tow-hundred-week).
# Those are the actual v3 API paths per BRIEF §"Known gotchas". Preserved verbatim.
# The v4 MCP uses corrected paths; this script hits v3 REST directly for cycle
# indicators except BMO which is v4.
# ──────────────────────────────────────────────────────────────────────────────

def _cg_v3_get(path: str, params: dict = None) -> Optional[dict]:
    return _cg_get(path, params or {}, base=CG_V3_BASE)


def _cg_v4_get(path: str, params: dict = None) -> Optional[dict]:
    return _cg_get(path, params or {}, base=CG_V4_BASE)


def pull_coinglass_cycle(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull 9 H4v2 cycle indicators from Coinglass v3/v4."""
    src = "coinglass_cycle"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    files, warnings, total_rows = [], [], 0

    # ── 1. 2-Year MA Multiplier (v3, typo endpoint preserved)
    def pull_2yr_ma():
        path = "/index/tow-year-ma-multiplier"
        out  = subdir / "2yr_ma_multiplier.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"2yr_ma_multiplier: no data from {path}"

        rows = []
        for item in data:
            rows.append({
                "date":       pd.to_datetime(item.get("createTime", item.get("time", 0)), unit="ms", utc=True).date().isoformat(),
                "price":      item.get("price"),
                "ma2y":       item.get("mA730"),
                "ma2y_x5":    item.get("mA730X5"),
            })
        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 2. Golden Ratio Multiplier (v3)
    def pull_golden_ratio():
        path = "/index/golden-ratio-multiplier"
        out  = subdir / "golden_ratio_multiplier.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"golden_ratio_multiplier: no data from {path}"

        rows = []
        for item in data:
            ts = item.get("createTime", item.get("time", 0))
            rows.append({
                "date":  pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                "price": item.get("price"),
                **{k: v for k, v in item.items() if k not in ("createTime", "time", "price")},
            })
        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 3 & 4. 200W MA Heatmap + ROC (v3, typo endpoint preserved)
    def pull_200w_heatmap():
        path = "/index/tow-hundred-week-moving-avg-heatmap"
        out  = subdir / "200w_ma_heatmap.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"200w_heatmap: no data from {path}"

        rows = []
        for item in data:
            ts = item.get("createTime", item.get("time", 0))
            rows.append({
                "date":       pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                "price":      item.get("price"),
                "mA1440":     item.get("mA1440"),
                # Omit mA1440IP per BRIEF (mostly zero)
            })
        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 5. BMO — Bitcoin Macro Oscillator (v4)
    # v13 change (2026-04-18): absorb fix_parsers.py bmo patch inline. The API
    # returns a `timestamp` (ms) field; prior handler looked for `createTime`/`time`,
    # silently produced date='1970-01-01' (epoch from default 0), and required a
    # separate fix_parsers.py pass to correct.
    def pull_bmo():
        path = "/index/bitcoin-macro-oscillator"
        out  = subdir / "bmo.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v4_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"bmo: no data from v4{path}"

        rows = []
        for item in data:
            ts = item.get("timestamp", item.get("createTime", item.get("time")))
            if ts is None:
                continue
            rows.append({
                "date":      pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                "price":     item.get("price"),
                "bmo_value": item.get("bmo_value"),
            })
        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 6. AHR999 (v3) — string dates "2026/04/01"
    def pull_ahr999():
        path = "/index/ahr999"
        out  = subdir / "ahr999.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"ahr999: no data from {path}"

        rows = []
        for item in data:
            raw_date = item.get("createTime", item.get("date", ""))
            # AHR999 returns string dates like "2026/04/01" per BRIEF §Known gotchas
            if isinstance(raw_date, str) and "/" in raw_date:
                date_str = raw_date.replace("/", "-")
            elif isinstance(raw_date, (int, float)):
                date_str = pd.to_datetime(raw_date, unit="ms", utc=True).date().isoformat()
            else:
                date_str = str(raw_date)

            ahr_val = item.get("ahr999")
            rows.append({
                "date":    date_str,
                "ahr999":  ahr_val,
                "price":   item.get("price"),
            })
        df = pd.DataFrame(rows).dropna(subset=["date", "ahr999"])
        # Raw transform: 1/ahr999 per BRIEF (orientation: keep after transform)
        df["ahr999_inv"] = 1.0 / df["ahr999"].astype(float)
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 7. Rainbow Chart (v3) — list-of-lists format
    def pull_rainbow():
        path = "/index/bitcoin-rainbow-chart"
        out  = subdir / "rainbow_chart.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"rainbow_chart: no data from {path}"

        # Format per BRIEF: list-of-lists [[price, b1, b2, ..., b10, ts], ...]
        rows = []
        if isinstance(data, list) and data and isinstance(data[0], list):
            for row in data:
                # Last element is timestamp; first is price; middle are band values
                try:
                    ts   = row[-1]
                    price = row[0]
                    bands = row[1:-1]
                    date_str = pd.to_datetime(ts, unit="ms", utc=True).date().isoformat()
                    entry = {"date": date_str, "price": price}
                    for i, b in enumerate(bands):
                        entry[f"band_{i+1}"] = b
                    rows.append(entry)
                except Exception:
                    pass
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            # If v3 returns dicts, handle gracefully
            for item in data:
                ts = item.get("createTime", item.get("time", 0))
                rows.append({
                    "date": pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                    **{k: v for k, v in item.items() if k not in ("createTime", "time")},
                })

        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 8. Fear & Greed (v3) — parallel arrays format
    def pull_fear_greed():
        path = "/index/fear-greed-history"
        out  = subdir / "fear_greed.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"fear_greed: no data from {path}"

        # Format per BRIEF: {values: [...], prices: [...], dates: [...]}
        rows = []
        if isinstance(data, dict):
            values = data.get("values", [])
            prices = data.get("prices", [])
            dates  = data.get("dates",  [])
            for i, v in enumerate(values):
                date_str = dates[i] if i < len(dates) else None
                price    = prices[i] if i < len(prices) else None
                if isinstance(date_str, (int, float)):
                    date_str = pd.to_datetime(date_str, unit="ms", utc=True).date().isoformat()
                rows.append({"date": date_str, "fear_greed": v, "price": price})
        elif isinstance(data, list):
            for item in data:
                ts = item.get("createTime", item.get("time", 0))
                rows.append({
                    "date":       pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                    "fear_greed": item.get("value"),
                    "price":      item.get("price"),
                })

        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── 9. Bubble Index (v3)
    # v13 change (2026-04-18): absorb fix_parsers.py bubble_index patch inline.
    # The v3 payload keys the value under `index` (not `value`/`bubbleIndex`), and
    # returns `date` as a string (not `createTime` ms). Prior handler produced
    # bubble_index=NaN and date='1970-01-01' across all rows, requiring a
    # separate fix_parsers.py re-fetch to correct.
    def pull_bubble_index():
        path = "/index/bitcoin-bubble-index"
        out  = subdir / "bubble_index.parquet"
        if out.exists() and not force and not dry_run:
            return out, None

        resp = _cg_v3_get(path)
        data = _cg_data(resp, path)
        if data is None:
            return None, f"bubble_index: no data from {path}"

        rows = []
        for item in data:
            # Real key is `index`; keep fallbacks for robustness if payload changes.
            val = item.get("index", item.get("value", item.get("bubbleIndex")))
            # Real date field is a string like "2026-04-15"; fallback to createTime ms.
            d = item.get("date")
            if d is None:
                ts = item.get("createTime", item.get("time"))
                if ts is None:
                    continue
                d = pd.to_datetime(ts, unit="ms", utc=True).date().isoformat()
            else:
                d = pd.to_datetime(d).date().isoformat()
            rows.append({"date": d, "bubble_index": val, "price": item.get("price")})
        df = pd.DataFrame(rows).dropna(subset=["date"])
        if dry_run:
            df = df.tail(7)
        return out, df

    # ── Run all 9 pullers
    indicators = [
        ("2yr_ma_multiplier",    pull_2yr_ma,       "coinglass_v3:/index/tow-year-ma-multiplier"),
        ("golden_ratio",         pull_golden_ratio,  "coinglass_v3:/index/golden-ratio-multiplier"),
        ("200w_heatmap",         pull_200w_heatmap,  "coinglass_v3:/index/tow-hundred-week-moving-avg-heatmap"),
        ("bmo",                  pull_bmo,           "coinglass_v4:/index/bitcoin-macro-oscillator"),
        ("ahr999",               pull_ahr999,        "coinglass_v3:/index/ahr999"),
        ("rainbow_chart",        pull_rainbow,       "coinglass_v3:/index/bitcoin-rainbow-chart"),
        ("fear_greed",           pull_fear_greed,    "coinglass_v3:/index/fear-greed-history"),
        ("bubble_index",         pull_bubble_index,  "coinglass_v3:/index/bitcoin-bubble-index"),
    ]

    for name, puller, source_tag in indicators:
        out_path = subdir / f"{name}.parquet"

        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP {name} (cached)")
            files.append(str(out_path))
            existing = pq.read_table(str(out_path)).to_pandas()
            total_rows += len(existing)
            continue

        try:
            result_path, df_or_err = puller()

            if isinstance(df_or_err, str):
                warnings.append(df_or_err)
                log.error(f"[{src}] {name} failed: {df_or_err}")
                continue

            if df_or_err is None:
                # Cached — no-op (shouldn't reach here in normal flow)
                if out_path.exists():
                    files.append(str(out_path))
                continue

            df = df_or_err
            if len(df) == 0:
                warnings.append(f"{name}: 0 rows returned")
                continue

            _save_parquet(df, out_path, source_tag=source_tag)
            files.append(str(out_path))
            total_rows += len(df)

        except Exception as e:
            warnings.append(f"{name}: {e}")
            log.error(f"[{src}] {name} exception: {e}\n{traceback.format_exc()}")

    # Note: 200W MA ROC (#4) is derived from heatmap file in feature-build phase — not a separate pull
    warnings_note = ["200W MA ROC (#4 in H4v2) is derived from heatmap data in feature-build phase, not a separate endpoint pull."]
    warnings = warnings_note + warnings

    status = "ok" if len([w for w in warnings if w != warnings_note[0]]) == 0 else "partial"
    if total_rows == 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# Coinglass H2 — derivatives stress raw inputs
# Verified endpoints (via MCP schema lookup, 2026-04-13):
#   - OI-weighted funding rate:  v4 /futures/funding-rate/oi-weight-history
#   - Aggregated OI:             v4 /futures/open-interest/aggregated-history
#   - Liquidations per exchange: v4 /futures/liquidation/history
#   - Aggregated liquidations:   v4 /futures/liquidation/aggregated-history
#   - Basis:                     v4 /futures/basis/history
#   - Coin-margin OI:            v4 /futures/open-interest/aggregated-coin-margin-history
# All use start_time/end_time in milliseconds.
# ──────────────────────────────────────────────────────────────────────────────

def pull_coinglass_h2(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull raw derivatives stress inputs for BTC and ETH."""
    src = "coinglass_h2"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    files, warnings, total_rows = [], [], 0

    if dry_run:
        start_ms = _ms(datetime.now(timezone.utc) - timedelta(days=7))
    else:
        start_ms = _ms(datetime.fromisoformat("2021-01-01").replace(tzinfo=timezone.utc))
    end_ms = _ms(datetime.now(timezone.utc))

    # ── OI-weighted funding rate (BTC and ETH aggregated)
    for coin in ["BTC", "ETH"]:
        out_path = subdir / f"funding_rate_oi_weighted_{coin.lower()}.parquet"
        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP funding_rate_{coin} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue

        resp = _cg_v4_get("/futures/funding-rate/oi-weight-history", {
            "symbol":     coin,
            "interval":   "1d",
            "limit":      1000,
            "start_time": start_ms,
            "end_time":   end_ms,
        })
        data = _cg_data(resp, f"/futures/funding-rate/oi-weight-history [{coin}]")
        if data:
            rows = []
            for item in data:
                rows.append({
                    "date":           pd.to_datetime(item["time"], unit="ms", utc=True).date().isoformat(),
                    "open":           item.get("open"),
                    "high":           item.get("high"),
                    "low":            item.get("low"),
                    "close":          item.get("close"),
                    "coin":           coin,
                })
            df = pd.DataFrame(rows)
            _save_parquet(df, out_path, f"coinglass_v4:/futures/funding-rate/oi-weight-history:{coin}")
            files.append(str(out_path))
            total_rows += len(df)
        else:
            warnings.append(f"funding_rate_oi_weighted_{coin}: no data")

    # ── Aggregated OI (BTC and ETH)
    for coin in ["BTC", "ETH"]:
        out_path = subdir / f"oi_aggregated_{coin.lower()}.parquet"
        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP oi_aggregated_{coin} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue

        resp = _cg_v4_get("/futures/open-interest/aggregated-history", {
            "symbol":     coin,
            "interval":   "1d",
            "limit":      1000,
            "start_time": start_ms,
            "end_time":   end_ms,
            "unit":       "usd",
        })
        data = _cg_data(resp, f"/futures/open-interest/aggregated-history [{coin}]")
        if data:
            rows = []
            for item in data:
                rows.append({
                    "date":  pd.to_datetime(item["time"], unit="ms", utc=True).date().isoformat(),
                    "open":  item.get("open"),
                    "high":  item.get("high"),
                    "low":   item.get("low"),
                    "close": item.get("close"),
                    "coin":  coin,
                })
            df = pd.DataFrame(rows)
            _save_parquet(df, out_path, f"coinglass_v4:/futures/open-interest/aggregated-history:{coin}")
            files.append(str(out_path))
            total_rows += len(df)
        else:
            warnings.append(f"oi_aggregated_{coin}: no data")

    # ── Liquidations per major exchange (BTC and ETH)
    # OKX requires its native perp symbol format: BTC-USDT-SWAP (verified 2026-04-13)
    liq_exchanges = {
        "BTC": [("Binance", "BTCUSDT"), ("OKX", "BTC-USDT-SWAP"), ("Bybit", "BTCUSDT")],
        "ETH": [("Binance", "ETHUSDT"), ("OKX", "ETH-USDT-SWAP"), ("Bybit", "ETHUSDT")],
    }
    for coin, exc_pairs in liq_exchanges.items():
        liq_frames = []
        for exc, sym in exc_pairs:
            resp = _cg_v4_get("/futures/liquidation/history", {
                "exchange":   exc,
                "symbol":     sym,
                "interval":   "1d",
                "limit":      1000,
                "start_time": start_ms,
                "end_time":   end_ms,
            })
            data = _cg_data(resp, f"/futures/liquidation/history [{exc}/{sym}]")
            if data:
                rows = []
                for item in data:
                    rows.append({
                        "date":                 pd.to_datetime(item["time"], unit="ms", utc=True).date().isoformat(),
                        "long_liquidation_usd": item.get("long_liquidation_usd"),
                        "short_liquidation_usd": item.get("short_liquidation_usd"),
                        "exchange":             exc,
                        "symbol":               sym,
                        "coin":                 coin,
                    })
                liq_frames.append(pd.DataFrame(rows))
            else:
                warnings.append(f"liquidations_{coin}_{exc}: no data")

        if liq_frames:
            out_path = subdir / f"liquidations_{coin.lower()}.parquet"
            if out_path.exists() and not force and not dry_run:
                log.info(f"[{src}] SKIP liquidations_{coin} (cached)")
                files.append(str(out_path))
                total_rows += len(pq.read_table(str(out_path)).to_pandas())
            else:
                df = pd.concat(liq_frames, ignore_index=True)
                _save_parquet(df, out_path, f"coinglass_v4:/futures/liquidation/history:{coin}")
                files.append(str(out_path))
                total_rows += len(df)

    # ── Basis (BTC: Binance BTCUSDT; ETH: Binance ETHUSDT)
    basis_pairs = {"BTC": ("Binance", "BTCUSDT"), "ETH": ("Binance", "ETHUSDT")}
    for coin, (exc, sym) in basis_pairs.items():
        out_path = subdir / f"basis_{coin.lower()}.parquet"
        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP basis_{coin} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue

        resp = _cg_v4_get("/futures/basis/history", {
            "exchange":   exc,
            "symbol":     sym,
            "interval":   "1d",
            "limit":      1000,
            "start_time": start_ms,
            "end_time":   end_ms,
        })
        data = _cg_data(resp, f"/futures/basis/history [{coin}]")
        if data:
            rows = []
            for item in data:
                rows.append({
                    "date":         pd.to_datetime(item["time"], unit="ms", utc=True).date().isoformat(),
                    "open_basis":   item.get("open_basis"),
                    "close_basis":  item.get("close_basis"),
                    "open_change":  item.get("open_change"),
                    "close_change": item.get("close_change"),
                    "coin":         coin,
                    "exchange":     exc,
                })
            df = pd.DataFrame(rows)
            _save_parquet(df, out_path, f"coinglass_v4:/futures/basis/history:{coin}")
            files.append(str(out_path))
            total_rows += len(df)
        else:
            warnings.append(f"basis_{coin}: no data from {exc}/{sym}")

    # ── Coin-margin OI ratio (BTC and ETH)
    cm_exchanges = "Binance,OKX,Bybit"
    for coin in ["BTC", "ETH"]:
        out_path = subdir / f"coin_margin_oi_{coin.lower()}.parquet"
        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP coin_margin_oi_{coin} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue

        resp = _cg_v4_get("/futures/open-interest/aggregated-coin-margin-history", {
            "exchange_list": cm_exchanges,
            "symbol":        coin,
            "interval":      "1d",
            "limit":         1000,
            "start_time":    start_ms,
            "end_time":      end_ms,
        })
        data = _cg_data(resp, f"/futures/open-interest/aggregated-coin-margin-history [{coin}]")
        if data:
            rows = []
            for item in data:
                if isinstance(item, dict):
                    ts = item.get("time", 0)
                    rows.append({
                        "date":  pd.to_datetime(ts, unit="ms", utc=True).date().isoformat(),
                        "open":  item.get("open"),
                        "high":  item.get("high"),
                        "low":   item.get("low"),
                        "close": item.get("close"),
                        "coin":  coin,
                    })
            df = pd.DataFrame(rows)
            if len(df) > 0:
                _save_parquet(df, out_path, f"coinglass_v4:/futures/open-interest/aggregated-coin-margin-history:{coin}")
                files.append(str(out_path))
                total_rows += len(df)
            else:
                warnings.append(f"coin_margin_oi_{coin}: 0 parseable rows")
        else:
            warnings.append(f"coin_margin_oi_{coin}: no data")

    status = "ok" if not warnings else "partial"
    if total_rows == 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# Coinglass H3 — ETF flow (BTC only)
# Verified endpoints (MCP schema lookup, 2026-04-13):
#   v4 /etf/bitcoin/flow-history     ← maps to BRIEF's /index/etf-bitcoin-flow-history
#   v4 /etf/bitcoin/list             ← maps to BRIEF's /index/etf-bitcoin-list
#   v4 /etf/bitcoin/premium-discount/history  ← maps to BRIEF's /index/etf-bitcoin-premium-discount-history
#   v4 /etf/bitcoin/detail           ← maps to BRIEF's /index/etf-bitcoin-detail
# Coverage valid from 2024-01-11 (US spot BTC ETF launch); pre-2024 = NaN by design.
# ──────────────────────────────────────────────────────────────────────────────

def _cg_normalize_df(data, fname: str) -> Optional[pd.DataFrame]:
    """Normalize a Coinglass data payload to a DataFrame with a 'date' column."""
    if isinstance(data, list):
        if len(data) == 0:
            return None
        if isinstance(data[0], dict):
            df = pd.DataFrame(data)
        else:
            return None
    elif isinstance(data, dict):
        df = pd.DataFrame([data])
    else:
        return None

    # Normalize time column
    for tcol in ("time", "createTime", "date"):
        if tcol in df.columns:
            if pd.api.types.is_integer_dtype(df[tcol]) or df[tcol].dtype == object:
                try:
                    df["date"] = pd.to_datetime(df[tcol].astype("int64"), unit="ms", utc=True).dt.date.astype(str)
                    break
                except (ValueError, TypeError):
                    df["date"] = df[tcol].astype(str)
                    break
    return df


def pull_coinglass_h3(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull H3 ETF flow data (BTC only).

    Endpoints (all v4, verified 2026-04-13):
      /etf/bitcoin/flow-history             — daily aggregate net flows
      /etf/bitcoin/list                     — constituent ETF metadata
      /etf/bitcoin/premium-discount/history — cross-ETF premium/NAV history
      /etf/bitcoin/detail?ticker=IBIT       — per-ticker snapshot (requires ticker param,
                                              pulled for top ETFs from list endpoint)
    """
    src = "coinglass_h3"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    files, warnings, total_rows = [], [], 0

    # ── 1. Flow history + premium/discount
    #
    # v13 change (2026-04-18): absorb fix_parsers.py ETF patches inline. Both
    # endpoints return ms `timestamp` fields (need date normalization), and
    # premium/discount's `list` field contains per-ETF objects (need to compute
    # avg_premium_pct as (market-nav)/nav averaged across ETFs; the legacy
    # `premium_discount_percent` field is no longer populated post-2026-04-17
    # Coinglass schema change).
    def _fix_etf_flow_history(df: pd.DataFrame) -> pd.DataFrame:
        if "timestamp" in df.columns:
            df = df.copy()
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date.astype(str)
        keep = [c for c in ["date", "flow_usd", "price_usd"] if c in df.columns]
        return df[keep].copy()

    def _fix_etf_premium_discount(df: pd.DataFrame) -> pd.DataFrame:
        if "list" not in df.columns:
            return df  # already post-processed (idempotent)
        df = df.copy()
        if "timestamp" in df.columns:
            df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date.astype(str)

        def _avg_premium(lst):
            if lst is None:
                return None
            try:
                items = list(lst)
            except TypeError:
                return None
            if not items:
                return None
            vals = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                # Prefer legacy field if Coinglass repopulates; else compute (mkt-nav)/nav.
                p = item.get("premium_discount_percent") or item.get("premiumDiscountPercent")
                if p is not None:
                    try:
                        vals.append(float(p))
                        continue
                    except (ValueError, TypeError):
                        pass
                nav = item.get("nav_usd")
                mkt = item.get("market_price_usd")
                if nav is None or mkt is None:
                    continue
                try:
                    nav_f, mkt_f = float(nav), float(mkt)
                except (TypeError, ValueError):
                    continue
                if nav_f == 0:
                    continue
                vals.append((mkt_f - nav_f) / nav_f)
            return sum(vals) / len(vals) if vals else None

        df["avg_premium_pct"] = df["list"].apply(_avg_premium)
        return df[["date", "avg_premium_pct"]].copy()

    _ETF_POST_FIX = {
        "etf_flow_history":     _fix_etf_flow_history,
        "etf_premium_discount": _fix_etf_premium_discount,
    }

    for fname, path, source_tag in [
        ("etf_flow_history",     "/etf/bitcoin/flow-history",             "coinglass_v4:/etf/bitcoin/flow-history"),
        ("etf_premium_discount", "/etf/bitcoin/premium-discount/history", "coinglass_v4:/etf/bitcoin/premium-discount/history"),
    ]:
        out_path = subdir / f"{fname}.parquet"
        if out_path.exists() and not force and not dry_run:
            log.info(f"[{src}] SKIP {fname} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue
        try:
            resp = _cg_v4_get(path)
            data = _cg_data(resp, path)
            if data is None:
                warnings.append(f"{fname}: no data from {path}")
                continue
            df = _cg_normalize_df(data, fname)
            if df is None or len(df) == 0:
                warnings.append(f"{fname}: empty or unparseable response from {path}")
                continue
            df = _ETF_POST_FIX[fname](df)  # v13: fix_parsers logic inline
            if dry_run:
                df = df.tail(7)
            _save_parquet(df, out_path, source_tag)
            files.append(str(out_path))
            total_rows += len(df)
        except Exception as e:
            warnings.append(f"{fname}: {e}")
            log.error(f"[{src}] {fname} failed: {e}\n{traceback.format_exc()}")

    # ── 2. ETF list (metadata, no pagination)
    etf_list_path = subdir / "etf_list.parquet"
    etf_tickers = []
    if etf_list_path.exists() and not force and not dry_run:
        log.info(f"[{src}] SKIP etf_list (cached)")
        files.append(str(etf_list_path))
        etf_df = pq.read_table(str(etf_list_path)).to_pandas()
        total_rows += len(etf_df)
        if "ticker" in etf_df.columns:
            etf_tickers = etf_df["ticker"].dropna().tolist()
    else:
        try:
            resp = _cg_v4_get("/etf/bitcoin/list")
            data = _cg_data(resp, "/etf/bitcoin/list")
            if data is not None:
                df = _cg_normalize_df(data, "etf_list")
                if df is not None and len(df) > 0:
                    _save_parquet(df, etf_list_path, "coinglass_v4:/etf/bitcoin/list")
                    files.append(str(etf_list_path))
                    total_rows += len(df)
                    if "ticker" in df.columns:
                        etf_tickers = df["ticker"].dropna().tolist()
                else:
                    warnings.append("etf_list: empty/unparseable response")
            else:
                warnings.append("etf_list: no data from /etf/bitcoin/list")
        except Exception as e:
            warnings.append(f"etf_list: {e}")
            log.error(f"[{src}] etf_list failed: {e}\n{traceback.format_exc()}")

    # ── 3. ETF detail per ticker (snapshot per ETF, requires ticker param)
    etf_detail_path = subdir / "etf_detail.parquet"
    if etf_detail_path.exists() and not force and not dry_run:
        log.info(f"[{src}] SKIP etf_detail (cached)")
        files.append(str(etf_detail_path))
        total_rows += len(pq.read_table(str(etf_detail_path)).to_pandas())
    elif etf_tickers:
        detail_frames = []
        # In dry-run, only fetch first 2 tickers to save time
        fetch_tickers = etf_tickers[:2] if dry_run else etf_tickers
        for ticker in fetch_tickers:
            try:
                resp = _cg_v4_get("/etf/bitcoin/detail", params={"ticker": ticker})
                data = _cg_data(resp, f"/etf/bitcoin/detail?ticker={ticker}")
                if data is not None:
                    # data is a dict with nested fields; flatten top-level
                    if isinstance(data, dict):
                        flat = {}
                        for k, v in data.items():
                            if isinstance(v, dict):
                                for kk, vv in v.items():
                                    flat[f"{k}_{kk}"] = vv
                            else:
                                flat[k] = v
                        flat["ticker"] = ticker
                        detail_frames.append(pd.DataFrame([flat]))
            except Exception as e:
                warnings.append(f"etf_detail[{ticker}]: {e}")

        if detail_frames:
            df = pd.concat(detail_frames, ignore_index=True)
            _save_parquet(df, etf_detail_path, "coinglass_v4:/etf/bitcoin/detail")
            files.append(str(etf_detail_path))
            total_rows += len(df)
        else:
            warnings.append("etf_detail: no data returned for any ticker")
    else:
        warnings.append("etf_detail: skipped (no tickers available from etf_list)")

    status = "ok" if not warnings else "partial"
    if total_rows == 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# CFTC — TFF contract 133741
# ──────────────────────────────────────────────────────────────────────────────

CFTC_COLUMNS = [
    # Title_Case as returned by cot_reports (verified 2026-04-13)
    "Dealer_Positions_Long_All",
    "Dealer_Positions_Short_All",
    "Asset_Mgr_Positions_Long_All",
    "Asset_Mgr_Positions_Short_All",
    "Lev_Money_Positions_Long_All",
    "Lev_Money_Positions_Short_All",
    "Open_Interest_All",
    "Report_Date_as_YYYY-MM-DD",
]


def pull_cftc(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull CFTC TFF contract 133741 (leveraged funds, CME BTC futures)."""
    import cot_reports as cot

    src = "cftc"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    out_path = subdir / "cftc_133741_futopt.parquet"

    if out_path.exists() and not force and not dry_run:
        log.info("[cftc] SKIP (cached)")
        existing = pq.read_table(str(out_path)).to_pandas()
        return _result(src, "skipped", len(existing), [str(out_path)])

    try:
        log.info("[cftc] Fetching traders_in_financial_futures_futopt ...")
        df = cot.cot_all(cot_report_type="traders_in_financial_futures_futopt")

        if df is None or len(df) == 0:
            return _result(src, "failed", 0, [], ["cot_all returned empty DataFrame"])

        # Filter contract 133741 ONLY — NOT 133742 (Micro BTC) per BRIEF
        # Column is Title_Case in cot_reports output (verified 2026-04-13)
        df = df[df["CFTC_Contract_Market_Code"].astype(str) == "133741"].copy()

        if len(df) == 0:
            return _result(src, "failed", 0, [], [
                "No rows for contract 133741 after filter. "
                "Check that cot_all returned TFF futopt data and that 133741 exists."
            ])

        # Keep requested columns — gracefully handle missing ones
        available = [c for c in CFTC_COLUMNS if c in df.columns]
        missing_cols = [c for c in CFTC_COLUMNS if c not in df.columns]

        if missing_cols:
            log.warning(f"[cftc] Missing columns (coverage warning): {missing_cols}")

        # Also keep market name for audit
        extra_keep = [c for c in ["Market_and_Exchange_Names", "CFTC_Contract_Market_Code"] if c in df.columns]
        df = df[available + extra_keep].copy()
        df = df.rename(columns={"Report_Date_as_YYYY-MM-DD": "date"}, errors="ignore")
        df = df.sort_values("date") if "date" in df.columns else df

        if dry_run:
            df = df.tail(4)  # ~4 weeks for dry-run

        _save_parquet(df, out_path, "cftc:133741:traders_in_financial_futures_futopt")
        warnings = [f"Missing columns: {missing_cols}"] if missing_cols else []
        return _result(src, "ok" if not warnings else "partial", len(df), [str(out_path)], warnings)

    except Exception as e:
        return _result(src, "failed", 0, [], [str(e) + "\n" + traceback.format_exc()])


# ──────────────────────────────────────────────────────────────────────────────
# Price — BTC and ETH daily OHLC
# Source choice: Yahoo Finance via yfinance (free, no auth, covers BTC from 2010,
# ETH from 2015). Documented in methodology/pull_all_raw_data.md.
# ──────────────────────────────────────────────────────────────────────────────

def pull_price(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull BTC and ETH daily OHLC from Yahoo Finance (yfinance)."""
    try:
        import yfinance as yf
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "yfinance", "-q"], check=True)
        import yfinance as yf

    src = "price"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    files, warnings, total_rows = [], [], 0

    assets = {
        "BTC": ("BTC-USD", "2010-07-17"),
        "ETH": ("ETH-USD", "2015-08-07"),
    }

    for coin, (ticker, full_start) in assets.items():
        out_path = subdir / f"{coin.lower()}_ohlc.parquet"

        if out_path.exists() and not force and not dry_run:
            log.info(f"[price] SKIP {coin} (cached)")
            files.append(str(out_path))
            total_rows += len(pq.read_table(str(out_path)).to_pandas())
            continue

        try:
            if dry_run:
                start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
            else:
                start = full_start

            log.info(f"[price] Fetching {coin} OHLC from Yahoo Finance ({ticker}) ...")
            t = yf.Ticker(ticker)
            hist = t.history(start=start, interval="1d", auto_adjust=True)

            if hist is None or len(hist) == 0:
                warnings.append(f"{coin}: Yahoo Finance returned no data for {ticker}")
                continue

            df = hist.reset_index()
            df.columns = [c.lower().replace(" ", "_") for c in df.columns]

            # Normalize date column
            for dcol in ("date", "datetime", "index"):
                if dcol in df.columns:
                    df["date"] = pd.to_datetime(df[dcol]).dt.date.astype(str)
                    break

            keep = [c for c in ["date", "open", "high", "low", "close", "volume"] if c in df.columns]
            df = df[keep].copy()
            df["coin"] = coin

            _save_parquet(df, out_path, f"yahoo:{ticker}")
            files.append(str(out_path))
            total_rows += len(df)
            log.info(f"[price] {coin}: {len(df)} rows")

        except Exception as e:
            warnings.append(f"{coin} OHLC: {e}")
            log.error(f"[price] {coin} failed: {e}\n{traceback.format_exc()}")

    status = "ok" if not warnings else "partial"
    if total_rows == 0:
        status = "failed"
    return _result(src, status, total_rows, files, warnings)


# ──────────────────────────────────────────────────────────────────────────────
# Main dispatcher
# ──────────────────────────────────────────────────────────────────────────────

def pull_artemis_etf(out_dir: Path, dry_run: bool = False, force: bool = False) -> Dict:
    """Pull daily US spot BTC ETF flows + spot volume from Artemis.

    v13 change (2026-04-18): folded in from the standalone `pull_artemis_etf.py`.
    That script still exists as a thin back-compat wrapper, but the canonical pull
    path is now through this source handler.

    Writes a single parquet: <out_dir>/artemis_etf/btc.parquet with columns:
        date                  — UTC-midnight-normalized
        etf_flow_usd          — Artemis ETF_FLOWS, daily net flow USD
        etf_spot_volume_usd   — Artemis ETF_SPOT_VOLUME, daily ETF spot volume USD

    Consumed by build_etf_flows.py (V4 hybrid). BTC spot ETFs launched 2024-01-10,
    so first ~10 days of the [2024-01-01, today] range are empty.

    Auth: ARTEMIS_API_KEY env var takes priority; falls back to the baked-in key.
    """
    src = "artemis_etf"
    subdir = (out_dir / ".dryrun" / src) if dry_run else (out_dir / src)
    subdir.mkdir(parents=True, exist_ok=True)

    out_path = subdir / "btc.parquet"
    if out_path.exists() and not force and not dry_run:
        log.info(f"[{src}] SKIP btc (cached)")
        existing = pq.read_table(str(out_path)).to_pandas()
        return _result(src, "ok", len(existing), [str(out_path)], [])

    api_key = os.environ.get("ARTEMIS_API_KEY") or ARTEMIS_API_KEY
    if not api_key:
        return _result(src, "failed", 0, [],
                       ["ARTEMIS_API_KEY unset and no baked-in fallback."])

    try:
        import artemis as _artemis_sdk
    except ImportError:
        return _result(src, "failed", 0, [],
                       ["artemis SDK not installed. `pip install artemis`."])

    # Date range: 2024-01-01 to today (UTC). Dry-run uses a tight 10-day tail.
    if dry_run:
        start_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
    else:
        start_date = "2024-01-01"
    end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    metric_to_col = {
        "ETF_FLOWS":       "etf_flow_usd",
        "ETF_SPOT_VOLUME": "etf_spot_volume_usd",
    }
    asset_symbol = "bitcoin"

    try:
        client = _artemis_sdk.Artemis(api_key=api_key)
        metric_csv = ",".join(metric_to_col.keys())
        log.info(f"[{src}] Calling Artemis fetch_metrics: metrics={metric_csv} "
                 f"symbols={asset_symbol} range={start_date}..{end_date}")
        resp = client.fetch_metrics(
            metric_csv,
            api_key=api_key,
            start_date=start_date,
            end_date=end_date,
            symbols=asset_symbol,
        )
        payload = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

        data = payload.get("data")
        if not isinstance(data, dict):
            return _result(src, "failed", 0, [],
                           [f"Unexpected Artemis response: data not a dict. Payload: {str(payload)[:400]}"])
        symbols = data.get("symbols")
        if isinstance(symbols, str):
            return _result(src, "failed", 0, [],
                           [f"Artemis returned informational string: {symbols!r}"])
        if not isinstance(symbols, dict):
            return _result(src, "failed", 0, [],
                           [f"Unexpected: data.symbols not a dict. Payload: {str(payload)[:400]}"])
        asset_block = symbols.get(asset_symbol)
        if not isinstance(asset_block, dict):
            return _result(src, "failed", 0, [],
                           [f"No asset block for {asset_symbol!r}. "
                            f"Got keys: {list(symbols.keys())}"])

        frames = []
        for metric, col in metric_to_col.items():
            records = asset_block.get(metric)
            if not isinstance(records, list):
                log.warning(f"[{src}] {metric} response not a list; skipping.")
                frames.append(pd.DataFrame(columns=["date", col]))
                continue
            df_m = pd.DataFrame(
                [(r.get("date"), r.get("val")) for r in records],
                columns=["date_raw", "val_raw"],
            )
            df_m["date"] = pd.to_datetime(df_m["date_raw"], errors="coerce", utc=True).dt.normalize()
            df_m[col]   = pd.to_numeric(df_m["val_raw"], errors="coerce")
            df_m = df_m[["date", col]].dropna(subset=["date"]).drop_duplicates("date")
            df_m = df_m.sort_values("date").reset_index(drop=True)
            frames.append(df_m)
            log.info(f"[{src}]   {metric}: {len(df_m)} rows")

        merged = frames[0]
        for f in frames[1:]:
            merged = merged.merge(f, on="date", how="outer")
        merged = merged.sort_values("date").reset_index(drop=True)

        if dry_run:
            merged = merged.tail(7)

        _save_parquet(merged, out_path, "artemis:ETF_FLOWS+ETF_SPOT_VOLUME:bitcoin")
        return _result(src, "ok", len(merged), [str(out_path)], [])
    except Exception as e:
        return _result(src, "failed", 0, [], [f"{e}\n{traceback.format_exc()}"])


# ──────────────────────────────────────────────────────────────────────────────
# Source dispatch
# ──────────────────────────────────────────────────────────────────────────────

SOURCE_MAP = {
    "fred":              pull_fred,
    "velo_btc":          pull_velo_btc,
    "velo_eth":          pull_velo_eth,
    "coinglass_cycle":   pull_coinglass_cycle,
    "coinglass_h2":      pull_coinglass_h2,
    "coinglass_h3":      pull_coinglass_h3,
    "cftc":              pull_cftc,
    "price":             pull_price,
    "artemis_etf":       pull_artemis_etf,  # v13: folded in from pull_artemis_etf.py
}


def main():
    parser = argparse.ArgumentParser(description="V2 raw data pull script")
    parser.add_argument("--source", default="all",
                        help="Comma-separated sources or 'all'. Valid: "
                             + ", ".join(SOURCE_MAP.keys()))
    parser.add_argument("--out-dir", default="raw_data",
                        help="Output directory (default: raw_data)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Minimal pull to verify credentials/connectivity. "
                             "Writes to .dryrun/ subdirectory.")
    parser.add_argument("--force", action="store_true",
                        help="Re-pull even if cached files exist.")
    parser.add_argument("--verbose", action="store_true",
                        help="Enable DEBUG logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Resolve sources
    if args.source.strip().lower() == "all":
        sources = list(SOURCE_MAP.keys())
    else:
        sources = [s.strip() for s in args.source.split(",")]
        invalid = [s for s in sources if s not in SOURCE_MAP]
        if invalid:
            log.error(f"Unknown source(s): {invalid}. Valid: {list(SOURCE_MAP.keys())}")
            sys.exit(1)

    log.info(f"Pull starting — sources={sources}, dry_run={args.dry_run}, force={args.force}")
    log.info(f"Output dir: {out_dir.resolve()}")

    results = []
    for src in sources:
        fn = SOURCE_MAP[src]
        log.info(f"{'─'*60}")
        log.info(f"START {src}")
        try:
            result = fn(out_dir, dry_run=args.dry_run, force=args.force)
        except Exception as e:
            result = _result(src, "failed", 0, [], [str(e) + "\n" + traceback.format_exc()])
        results.append(result)
        log.info(f"DONE  {src} → status={result['status']}, rows={result['rows']}, "
                 f"files={len(result['files'])}, warnings={len(result['warnings'])}")
        for w in result["warnings"]:
            log.warning(f"  [{src}] {w}")

    log.info(f"{'═'*60}")
    log.info("SUMMARY")
    log.info(f"{'═'*60}")

    all_ok = True
    for r in results:
        status_str = r["status"].upper()
        log.info(f"  {r['source']:25s} {status_str:8s}  rows={r['rows']:6d}  files={len(r['files'])}")
        if r["status"] == "failed":
            all_ok = False

    # Print structured JSON to stdout for programmatic consumption
    print(json.dumps({"dry_run": args.dry_run, "results": results}, indent=2))

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
