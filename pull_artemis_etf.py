#!/usr/bin/env python3
"""
pull_artemis_etf.py — pull daily US spot BTC ETF metrics from Artemis.

Pulls ETF_FLOWS and ETF_SPOT_VOLUME for bitcoin and writes them joined on
date to:
    data/raw/artemis_etf/btc.parquet

Columns:
    date                  — UTC-midnight-normalized
    etf_flow_usd          — Artemis ETF_FLOWS, daily net flow USD
    etf_spot_volume_usd   — Artemis ETF_SPOT_VOLUME, daily ETF spot volume USD

Consumed by: build_etf_flows.py (V4 hybrid).

Usage:
    ARTEMIS_API_KEY=<key> python3 pull_artemis_etf.py
    ARTEMIS_API_KEY=<key> python3 pull_artemis_etf.py --out-dir data/raw
    python3 pull_artemis_etf.py --dry-run

Auth: ARTEMIS_API_KEY env var. Uses the official `artemis` PyPI SDK.

Dependency: `pip install artemis`. The SDK wraps HTTP details, handles retries,
and returns a typed response object.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────
ASSET_SYMBOL = "bitcoin"  # Artemis asset slug for BTC spot ETF series.

# Order matters: these map one-to-one to the parquet column names below.
METRIC_TO_COL = {
    "ETF_FLOWS":       "etf_flow_usd",
    "ETF_SPOT_VOLUME": "etf_spot_volume_usd",
}

# ETF spot BTC launch date — earliest day any of these metrics can be non-null.
DEFAULT_START = "2024-01-01"

log = logging.getLogger("pull_artemis_etf")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_api_key() -> str:
    key = os.environ.get("ARTEMIS_API_KEY")
    if not key:
        sys.stderr.write(
            "\nERROR: ARTEMIS_API_KEY environment variable is not set.\n\n"
            "This script requires an Artemis API key to pull ETF_FLOWS and\n"
            "ETF_SPOT_VOLUME for bitcoin.\n\n"
            "Set it and retry:\n"
            "    export ARTEMIS_API_KEY=<your-key>\n"
            "    python3 pull_artemis_etf.py\n\n"
            "These series feed sub-signals 1, 3, and 4 of the ETF Flows\n"
            "hypothesis (V4 hybrid).\n"
        )
        sys.exit(2)
    return key


def _records_to_frame(records: List[dict], col_name: str) -> pd.DataFrame:
    """Convert a list of {date, val} dicts into a typed, deduplicated frame."""
    if not records:
        return pd.DataFrame(columns=["date", col_name])
    df = pd.DataFrame([(r.get("date"), r.get("val")) for r in records],
                      columns=["date_raw", "val_raw"])
    df["date"] = pd.to_datetime(df["date_raw"], errors="coerce", utc=True).dt.normalize()
    df[col_name] = pd.to_numeric(df["val_raw"], errors="coerce")
    df = df[["date", col_name]].dropna(subset=["date"])
    df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


def _fetch_all_metrics(api_key: str, start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch all configured metrics in a single batched call, join on date.

    Returns a frame with columns ['date'] + list(METRIC_TO_COL.values()).
    """
    # Import here so --dry-run works without the SDK installed.
    import artemis

    client = artemis.Artemis(api_key=api_key)

    metric_names_csv = ",".join(METRIC_TO_COL.keys())
    log.info("Calling Artemis fetch_metrics: metrics=%s symbols=%s range=%s..%s",
             metric_names_csv, ASSET_SYMBOL, start_date, end_date)

    resp = client.fetch_metrics(
        metric_names_csv,
        api_key=api_key,
        start_date=start_date,
        end_date=end_date,
        symbols=ASSET_SYMBOL,
    )
    payload = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)

    # Expected shape (per Artemis SDK sample on 2026-04-17):
    #   {"data": {"symbols": {"bitcoin": {"ETF_FLOWS": [...], "ETF_SPOT_VOLUME": [...]}}}}
    # The SDK occasionally returns an informational string in place of the symbols
    # dict when the requested range has no data — guard against that.
    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected Artemis response: data is not a dict. Payload: {str(payload)[:400]}")
    symbols = data.get("symbols")
    if isinstance(symbols, str):
        # e.g. "Latest data not available for this asset."
        raise RuntimeError(
            f"Artemis returned an informational message rather than data: {symbols!r}. "
            f"Check the asset symbol ({ASSET_SYMBOL!r}) and date range ({start_date}..{end_date})."
        )
    if not isinstance(symbols, dict):
        raise RuntimeError(f"Unexpected Artemis response: data.symbols is not a dict. Payload: {str(payload)[:400]}")
    asset_block = symbols.get(ASSET_SYMBOL)
    if not isinstance(asset_block, dict):
        raise RuntimeError(
            f"Artemis response did not include an asset block for {ASSET_SYMBOL!r}. "
            f"Observed keys under data.symbols: {list(symbols.keys())}. "
            f"Payload: {str(payload)[:400]}"
        )

    frames = []
    for metric, col in METRIC_TO_COL.items():
        records = asset_block.get(metric)
        if not isinstance(records, list):
            log.warning("Artemis response for %s is not a list (type=%s); skipping.",
                        metric, type(records).__name__)
            frames.append(pd.DataFrame(columns=["date", col]))
            continue
        frames.append(_records_to_frame(records, col))
        log.info("  %s: %d rows", metric, len(frames[-1]))

    # Full outer join on date so partial coverage in one series doesn't trim the other.
    merged = frames[0]
    for f in frames[1:]:
        merged = merged.merge(f, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged


def _save_parquet(df: pd.DataFrame, path: Path, source_tag: str) -> None:
    """Write parquet with audit columns in the same style as pull_all_raw_data.py."""
    df = df.copy()
    df["_source"] = source_tag
    df["_pulled_at"] = _now_iso()
    df["_rows"] = len(df)
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.Table.from_pandas(df, preserve_index=False),
        str(path),
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", default="data/raw",
                    help="Output root. Writes <out-dir>/artemis_etf/btc.parquet.")
    ap.add_argument("--start-date", default=DEFAULT_START,
                    help=f"YYYY-MM-DD. Default: {DEFAULT_START} (ETF launch era).")
    ap.add_argument("--end-date", default=None,
                    help="YYYY-MM-DD. Default: today (UTC).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print config and exit without calling the API.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    end_date = args.end_date or date.today().isoformat()
    out_path = Path(args.out_dir) / "artemis_etf" / "btc.parquet"

    if args.dry_run:
        print("DRY RUN — no API calls will be made.")
        print(f"  Asset:        {ASSET_SYMBOL}")
        print(f"  Metrics:      {list(METRIC_TO_COL.keys())}")
        print(f"  Range:        {args.start_date} .. {end_date}")
        print(f"  Output:       {out_path}")
        print(f"  Auth env var: ARTEMIS_API_KEY (set={'yes' if os.environ.get('ARTEMIS_API_KEY') else 'no'})")
        return 0

    api_key = _require_api_key()
    merged = _fetch_all_metrics(api_key, args.start_date, end_date)

    if merged.empty:
        raise RuntimeError("Merged Artemis frame is empty. Check date range and API key.")
    if not merged["date"].is_monotonic_increasing:
        raise RuntimeError("Merged Artemis frame date column is not monotone.")

    _save_parquet(merged, out_path, source_tag="artemis_etf")

    nulls = {c: int(merged[c].isna().sum()) for c in METRIC_TO_COL.values() if c in merged.columns}
    print(f"Wrote {out_path}")
    print(f"  rows:  {len(merged)}")
    print(f"  range: {merged['date'].min().date()} .. {merged['date'].max().date()}")
    print(f"  nulls: {nulls}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
