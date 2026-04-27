"""Export canonical CSVs (master_daily_view, weights, standalone hypothesis files)
from the current pipeline state, and write a manifest so downstream consumers can
verify that all files came from the same run.

v13 changes (2026-04-18):
- Explicit --label and --variant CLI args (introduced earlier in session).
- Standalone hypothesis_*.csv files are now regenerated from the CURRENT canonical
  state of data/hypotheses/*.parquet. Prior committed versions drifted up to 0.34
  from master's embedded columns (v9 audit). Fixes open-item #4.
- Writes a manifest (export_manifest.json) tying master_daily_view and weight_history
  to the same pipeline run via SHA-256 of their source parquets. Fixes the
  master ↔ weight_history co-export issue from refit_report_v13.md §7.

Canonical usage (from regenerate_canonicals.sh or run_all.sh):
    python export_csvs.py                                # export both canonical y_60
                                                         # variants + hypothesis CSVs +
                                                         # weights.csv + manifest
Targeted usage:
    python export_csvs.py --label y_60 --variant wf365   # specific master only
    python export_csvs.py --skip-weights                 # skip weights.csv regen
    python export_csvs.py --skip-hypotheses              # skip standalone hyp CSVs
    python export_csvs.py --check-coherence              # verify manifest only,
                                                         # no writes

All files land at $BTC_MODEL_ROOT (or the script's parent dir if env var unset).
"""
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

from common import ENSEMBLE_FIT_START

HERE = Path(__file__).parent
ROOT = Path(os.environ.get("BTC_MODEL_ROOT", HERE))

# OUT_DIR is the persistent destination for top-level CSVs and the manifest.
# Phase 2 volume refactor: on Railway, BTC_DATA_DIR=/app/data (the volume
# mount); locally it falls back to BTC_MODEL_ROOT (the project checkout) so
# `bash run_all.sh` keeps writing into the repo dir as before.
# The order is intentional: BTC_DATA_DIR > BTC_MODEL_ROOT > script-parent.
OUT_DIR = Path(
    os.environ.get("BTC_DATA_DIR")
    or os.environ.get("BTC_MODEL_ROOT")
    or HERE
)

DERIVED = ROOT / "data/derived"
HYP = ROOT / "data/hypotheses"
FINAL = ROOT / "data/final"
RAW = ROOT / "data/raw"

VALID_VARIANTS = ("wf365", "sf730")
VALID_LABELS = ("y_60", "y_30")

# Hypothesis groups, in the order master_daily_view's embedded columns use them.
HYPOTHESIS_GROUPS = ["macro_equities", "cme", "crypto_derivatives",
                     "classic_cycle", "etf_flows", "eth"]

# Mapping from hypothesis group -> standalone CSV filename. The historical file
# for etf_flows is `hypothesis_etf_flows 1.csv` (with a space) — we write both
# the spaced legacy name and the conventional name so either can be read.
STANDALONE_CSV_NAMES = {
    "macro_equities":     ["hypothesis_macro_equities.csv"],
    "cme":                ["hypothesis_cme.csv"],
    "crypto_derivatives": ["hypothesis_crypto_derivatives.csv"],
    "classic_cycle":      ["hypothesis_classic_cycle.csv"],
    "etf_flows":          ["hypothesis_etf_flows.csv"],
    "eth":                ["hypothesis_eth.csv"],
}

MANIFEST_PATH = OUT_DIR / "export_manifest.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_master(label: str, variant: str, out_path: Path) -> dict:
    """Build master_daily_view_{variant}.csv for the specified label.

    Returns a dict summarizing which source files contributed (for manifest).
    """
    ens_file = FINAL / f"ensemble_{variant}_{label}.parquet"
    if not ens_file.exists():
        sys.stderr.write(
            f"ERROR: {ens_file} does not exist.\n"
            f"Run `bash regenerate_canonicals.sh` first to produce it.\n"
        )
        sys.exit(1)

    sources = {
        "regime":   str(DERIVED / "regime.parquet"),
        "labels":   str(DERIVED / "labels.parquet"),
        "ensemble": str(ens_file),
    }
    for group in HYPOTHESIS_GROUPS:
        sources[f"hypothesis_{group}"] = str(HYP / f"{group}.parquet")

    regime = pd.read_parquet(DERIVED / "regime.parquet")[["regime"]]
    labels = pd.read_parquet(DERIVED / "labels.parquet")[
        ["y_30", "y_60", "fwd_30d_max_dd", "fwd_60d_max_dd"]
    ]
    wide = regime.join(labels)

    for group in HYPOTHESIS_GROUPS:
        h = pd.read_parquet(HYP / f"{group}.parquet")[["score"]]
        h.columns = [f"{group}_score"]
        wide = wide.join(h)

    ens = pd.read_parquet(ens_file)[
        ["ensemble_score", "percentile", "position", "btc_return", "strategy_return"]
    ]
    wide = wide.join(ens)

    wide = wide.reset_index()
    if "date" not in wide.columns and "index" in wide.columns:
        wide = wide.rename(columns={"index": "date"})
    wide["date"] = pd.to_datetime(wide["date"]).dt.strftime("%Y-%m-%d")
    wide = wide.drop(columns=[c for c in wide.columns if c.startswith("_")], errors="ignore")
    wide.to_csv(out_path, index=False, float_format="%.6f")

    # Hash the source parquets at the moment of export for the manifest.
    source_hashes = {k: _sha256(Path(v)) for k, v in sources.items() if Path(v).exists()}

    print(f"wrote {out_path}  ({len(wide)} rows, {len(wide.columns)} cols, "
          f"source={ens_file.name})")
    return {
        "master_csv":   str(out_path),
        "master_sha":   _sha256(out_path),
        "source_files": sources,
        "source_shas":  source_hashes,
    }


def build_weights_csv(out_path: Path) -> dict:
    """Combine per-variant-per-label weight CSVs into unified weights.csv.

    Returns a manifest entry naming the source files and their hashes.
    """
    frames = []
    sources = {}
    for variant in VALID_VARIANTS:
        for label in VALID_LABELS:
            f = FINAL / f"ensemble_weights_{variant}_{label}.csv"
            if f.exists():
                w = pd.read_csv(f)
                w.insert(0, "variant", f"{variant}_{label}")
                frames.append(w)
                sources[f"{variant}_{label}"] = str(f)
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined.to_csv(out_path, index=False, float_format="%.6f")
        print(f"wrote {out_path}  ({len(combined)} rows)")
        return {
            "weights_csv":  str(out_path),
            "weights_sha":  _sha256(out_path),
            "source_files": sources,
            "source_shas":  {k: _sha256(Path(v)) for k, v in sources.items()},
        }
    else:
        print(f"WARNING: no ensemble_weights_{{variant}}_{{label}}.csv files found; "
              f"skipping {out_path}")
        return {}


def build_weight_history_manifest() -> dict:
    """Manifest entry for the weight_history CSVs produced by walk-forward runs."""
    entries = {}
    for label in VALID_LABELS:
        wh_path = FINAL / f"weight_history_wf365_{label}.csv"
        if wh_path.exists():
            entries[f"wf365_{label}"] = {
                "path": str(wh_path),
                "sha":  _sha256(wh_path),
            }
    return entries


def build_standalone_hypothesis_csvs(out_dir: Path) -> list:
    """Regenerate each standalone hypothesis_*.csv from data/hypotheses/*.parquet.

    The embedded <hyp>_score columns in master_daily_view are the source of truth;
    these standalone files are a diagnostic/attribution convenience. v13 fixes the
    drift-from-master issue found in the v9 audit by co-generating them with each
    master export.

    Returns a list of manifest entries for the written files.
    """
    manifest_entries = []
    for group in HYPOTHESIS_GROUPS:
        src_path = HYP / f"{group}.parquet"
        if not src_path.exists():
            print(f"WARNING: {src_path} not present; skipping standalone CSV for {group}.")
            continue
        df = pd.read_parquet(src_path)
        # Keep score + all sub_* columns (sub-signal attribution is the main
        # non-diagnostic use of these files).
        keep_cols = ["score"] + [c for c in df.columns if c.startswith("sub_")]
        df_out = df[keep_cols].copy().reset_index()
        if "date" not in df_out.columns and "index" in df_out.columns:
            df_out = df_out.rename(columns={"index": "date"})
        df_out["date"] = pd.to_datetime(df_out["date"]).dt.strftime("%Y-%m-%d")
        for name in STANDALONE_CSV_NAMES[group]:
            out_path = out_dir / name
            df_out.to_csv(out_path, index=False, float_format="%.6f")
            manifest_entries.append({
                "group":     group,
                "out_path":  str(out_path),
                "out_sha":   _sha256(out_path),
                "source":    str(src_path),
                "source_sha": _sha256(src_path),
            })
        print(f"wrote hypothesis_{group}.csv  ({len(df_out)} rows, {len(keep_cols)+1} cols, "
              f"source={src_path.name})")
    return manifest_entries


def regenerate_data_inventory(out_path: Path) -> None:
    """Walk data/raw/**/*.parquet and rewrite data_inventory.csv from live state.

    Schema (5 cols, matches the static checked-in version):
        group,series,start,end,rows

    For time-series parquets (have a `date` column), start/end are the min/max
    date as YYYY-MM-DD. For row-indexed metadata (`etf_list`, `etf_detail` —
    no date column), start/end are 0 and len-1, matching the historical format.

    Replaces a static checked-in CSV that nothing in the pipeline regenerated.
    The previous file was the v14-ship snapshot from 2026-04-22 and lied to
    every API consumer (and to me during diagnosis) about input coverage.

    Defensive: per-parquet errors get logged and skipped; a top-level failure
    logs the traceback and returns cleanly (never aborts the run_all chain).
    """
    if not RAW.exists():
        return
    try:
        rows = []
        for p in sorted(RAW.rglob("*.parquet")):
            rel = p.relative_to(RAW)
            group = rel.parts[0] if len(rel.parts) > 1 else "_root"
            series = p.stem
            try:
                df = pd.read_parquet(p)
                if "date" in df.columns and len(df) > 0:
                    dates = pd.to_datetime(df["date"], errors="coerce").dropna()
                    if len(dates) > 0:
                        start = dates.min().strftime("%Y-%m-%d")
                        end   = dates.max().strftime("%Y-%m-%d")
                    else:
                        start, end = "", ""
                else:
                    start = "0"
                    end   = str(max(0, len(df) - 1))
                rows.append({"group": group, "series": series,
                             "start": start, "end": end, "rows": len(df)})
            except Exception as e:
                print(f"  data_inventory: skipping {rel}: {type(e).__name__}: {e}",
                      file=sys.stderr)
        pd.DataFrame(rows, columns=["group", "series", "start", "end", "rows"]).to_csv(
            out_path, index=False)
        print(f"wrote {out_path}  ({len(rows)} sources)")
    except Exception as e:
        import traceback
        print(f"WARNING: regenerate_data_inventory failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


def regenerate_raw_data_export(out_path: Path) -> None:
    """Walk data/raw/**/*.parquet and rewrite raw_data_export.csv as a wide
    table keyed by date.

    Column convention: `{group}__{series}__{col}` where group is the parent
    directory name and series is the parquet filename stem. For long-format
    parquets with an `exchange` pivot key (Velo, Coinglass H2 liquidations),
    the exchange becomes part of the column name:
        `{group}__{series}__{value_col}__{exchange}`

    Audit columns (`_source`, `_pulled_at`, `_rows`) and constant-within-file
    dimension columns (`symbol`, `metric`, `velo_type`, `coin`) are dropped.

    Like data_inventory.csv, this replaces a static 2026-04-22 snapshot that
    no pipeline step was overwriting.

    Defensive: per-parquet errors get logged and skipped; a top-level failure
    logs the traceback and returns cleanly (never aborts the run_all chain).
    Best-effort by design — losing one parquet's contribution shouldn't deny
    the API the other ~49 sources' worth of raw data.
    """
    if not RAW.exists():
        return

    AUDIT_COLS = {"_source", "_pulled_at", "_rows"}
    # Constant-per-parquet labels (the parquet filename already encodes them).
    DIM_COLS = {"symbol", "metric", "velo_type", "coin"}
    # One-row-per-(date, key) pivot keys.
    PIVOT_KEYS = ["exchange"]

    frames: list[pd.DataFrame] = []
    for p in sorted(RAW.rglob("*.parquet")):
        rel = p.relative_to(RAW)
        group = rel.parts[0] if len(rel.parts) > 1 else "_root"
        series = p.stem
        try:
            df = pd.read_parquet(p)
            if "date" not in df.columns or len(df) == 0:
                continue
            df = df.drop(columns=[c for c in df.columns if c in AUDIT_COLS | DIM_COLS],
                         errors="ignore").copy()
            # Normalize date dtype across parquets: pyarrow round-trips some
            # parquets as tz-aware datetime64[ms, UTC] (e.g. artemis_etf/btc.
            # parquet, which is written via pd.to_datetime(..., utc=True)) and
            # others as tz-naive datetime64[ns]. pd.merge refuses to merge
            # mixed-tz/precision keys. Coerce everything to a single naive ns
            # representation so the outer merges can join cleanly.
            df["date"] = pd.to_datetime(df["date"], errors="coerce", utc=True
                                        ).dt.tz_convert(None)
            df = df.dropna(subset=["date"])
            if len(df) == 0:
                continue

            pivot_keys = [c for c in PIVOT_KEYS if c in df.columns]
            if pivot_keys:
                pivot_key = pivot_keys[0]
                value_cols = [c for c in df.columns if c not in ("date", pivot_key)]
                if not value_cols:
                    continue
                df = df.pivot_table(
                    index="date", columns=pivot_key,
                    values=value_cols, aggfunc="first",
                )
                df.columns = [
                    "__".join(str(x) for x in (col if isinstance(col, tuple) else (col,)))
                    for col in df.columns
                ]
                df = df.reset_index()

            # Last-resort dedup: if any non-pivot path still produced duplicate
            # `date` rows, keep the first — outer merge would otherwise multiply.
            df = df.drop_duplicates(subset=["date"], keep="first")

            df.columns = ["date" if c == "date" else f"{group}__{series}__{c}"
                          for c in df.columns]
            frames.append(df)
            print(f"  raw_data_export: {rel}  +{len(df.columns)-1} cols × {len(df)} rows")
        except Exception as e:
            import traceback
            print(f"  raw_data_export: SKIP {rel}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            print(traceback.format_exc(), file=sys.stderr)

    if not frames:
        print("raw_data_export: no frames produced; skipping write", file=sys.stderr)
        return

    try:
        out = frames[0]
        for f in frames[1:]:
            out = out.merge(f, on="date", how="outer")
        out = out.sort_values("date").reset_index(drop=True)
        out["date"] = out["date"].dt.strftime("%Y-%m-%d")
        out.to_csv(out_path, index=False)
        print(f"wrote {out_path}  ({len(out)} rows × {len(out.columns)} cols)")
    except Exception as e:
        import traceback
        print(f"WARNING: raw_data_export merge/write failed: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        print(traceback.format_exc(), file=sys.stderr)


def compute_data_freshness() -> dict:
    """Walk data/raw/**/*.parquet, read each parquet's `_pulled_at` audit column,
    return newest/oldest pull timestamps + per-source ages.

    The `_pulled_at` column is added to every parquet by pull_all_raw_data.py at
    write time. If the daily cron is healthy and `--force` is set, every parquet
    gets re-written each run, so newest_age_hours stays well under 24. If the
    fetcher silently stops (cache-skip, API auth failure, etc.), newest_age_hours
    is the canonical freshness signal — far more reliable than file mtimes or
    the export_manifest's own generated_at (which only proves the export ran).
    """
    now = datetime.now(timezone.utc)
    per_source: list[dict] = []
    if not RAW.exists():
        return {
            "checked_at_utc":   now.isoformat(),
            "raw_dir":          str(RAW),
            "raw_dir_present":  False,
            "per_source":       [],
        }
    for p in sorted(RAW.rglob("*.parquet")):
        rel = p.relative_to(RAW).as_posix()
        try:
            df = pd.read_parquet(p, columns=["_pulled_at"])
        except Exception as e:
            per_source.append({
                "source": rel, "pulled_at_utc": None,
                "age_hours": None, "error": str(e),
            })
            continue
        if "_pulled_at" not in df.columns or df["_pulled_at"].isna().all():
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
            per_source.append({
                "source": rel,
                "pulled_at_utc": mtime.isoformat(),
                "age_hours": round((now - mtime).total_seconds() / 3600, 2),
                "source_field": "file_mtime",
            })
            continue
        ts = pd.to_datetime(df["_pulled_at"].dropna()).max()
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
        per_source.append({
            "source": rel,
            "pulled_at_utc": ts.isoformat(),
            "age_hours": round((now - ts.to_pydatetime()).total_seconds() / 3600, 2),
            "source_field": "_pulled_at",
        })

    valid_ages = [s["age_hours"] for s in per_source if s.get("age_hours") is not None]
    return {
        "checked_at_utc":     now.isoformat(),
        "raw_dir":            str(RAW),
        "raw_dir_present":    True,
        "parquet_count":      len(per_source),
        "newest_age_hours":   min(valid_ages) if valid_ages else None,
        "oldest_age_hours":   max(valid_ages) if valid_ages else None,
        "per_source":         per_source,
    }


def check_freshness(max_age_hours: float) -> int:
    """Exit 0 if the freshest parquet under data/raw/ is younger than max_age_hours,
    1 otherwise. Designed to be wired into daily_pipeline.py as a cheap post-pull
    sanity step that fails the chain before stale numbers reach the audit log.

    Checks `newest_age_hours` (not oldest) on purpose: some upstream sources are
    weekly (CFTC TFF) or quarterly, so an "every parquet must be < 36h" rule
    would false-alarm. The signal we want is "no parquet is being refreshed at
    all" — that catches the cache-skip / cron-broken / token-expired classes.
    """
    fresh = compute_data_freshness()
    if not fresh.get("raw_dir_present"):
        print(f"freshness check: data/raw not present at {fresh['raw_dir']}",
              file=sys.stderr)
        return 1
    newest = fresh.get("newest_age_hours")
    if newest is None:
        print("freshness check: no parquets found under data/raw", file=sys.stderr)
        return 1
    print(f"freshness check: newest pull {newest:.2f}h ago "
          f"(oldest {fresh['oldest_age_hours']:.2f}h, {fresh['parquet_count']} parquets)")
    if newest > max_age_hours:
        print(f"FRESHNESS FAILED: newest pull is {newest:.2f}h old, "
              f"threshold {max_age_hours:.0f}h. The fetcher is not refreshing "
              f"upstream data — see pull_all.log and check --force / API tokens.",
              file=sys.stderr)
        for s in fresh["per_source"]:
            age = s.get("age_hours")
            if age is not None and age > max_age_hours:
                print(f"  STALE {s['source']}: {age:.2f}h", file=sys.stderr)
        return 1
    return 0


def write_manifest(master_entries: list, weights_entry: dict,
                   weight_history_entries: dict, hypothesis_entries: list) -> None:
    """Write export_manifest.json tying master, weights, weight_history, and
    standalone hypothesis CSVs to the same set of source parquets.

    Downstream consumers can verify coherence by calling --check-coherence, which
    re-hashes the source files and compares to the manifest. A mismatch means
    the artifacts were produced from different pipeline runs.
    """
    manifest = {
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "canonical_start":    ENSEMBLE_FIT_START.strftime("%Y-%m-%d"),
        "data_freshness":     compute_data_freshness(),
        "master_entries":     master_entries,
        "weights_entry":      weights_entry,
        "weight_history":     weight_history_entries,
        "hypothesis_entries": hypothesis_entries,
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"wrote {MANIFEST_PATH}  (coherence manifest)")


def check_coherence() -> int:
    """Verify that files on disk match the manifest. Exit 0 if coherent, 1 if not.

    Specifically: re-hash every source parquet referenced by any master entry and
    the weight_history files. Compare to stored hashes. A mismatch means the
    master_daily_view_*.csv on disk was NOT produced from the same pipeline run
    as the weight_history_*.csv on disk.

    Only meaningful in a pipeline working directory where `data/{derived,hypotheses,
    final}/` exist. In a committed-code-only directory (e.g. /mnt/project/ which
    never has data/ committed), use `sha256sum` against the checksum list in the
    HANDOVER for paste verification instead.
    """
    if not MANIFEST_PATH.exists():
        print(f"ERROR: {MANIFEST_PATH} does not exist. Run `python export_csvs.py` first.",
              file=sys.stderr)
        return 1

    # Environment sanity check: if data/ directory is missing entirely, the user
    # is likely running this in a committed-code dir (like /mnt/project/) where
    # coherence-checking is not the right tool. Bail out with a useful message
    # rather than false-alarming on every "source missing" line.
    data_dir = ROOT / "data"
    if not data_dir.exists():
        print(
            f"ERROR: {data_dir} does not exist.\n\n"
            f"--check-coherence is designed to verify that master_daily_view and\n"
            f"weight_history in a pipeline working directory came from the same\n"
            f"run. It needs access to data/{{derived,hypotheses,final}}/ parquets.\n\n"
            f"For paste verification of a committed-code directory (where data/\n"
            f"is not committed), use sha256sum against the checksum list in\n"
            f"HANDOVER.md's 'Package integrity' section instead:\n\n"
            f"    sha256sum <file> | cut -c1-16\n",
            file=sys.stderr,
        )
        return 1

    manifest = json.loads(MANIFEST_PATH.read_text())
    problems = []

    for entry in manifest.get("master_entries", []):
        for tag, path_str in entry.get("source_files", {}).items():
            expected = entry.get("source_shas", {}).get(tag)
            p = Path(path_str)
            if not p.exists():
                problems.append(f"master source {tag} missing: {p}")
                continue
            actual = _sha256(p)
            if expected and actual != expected:
                problems.append(
                    f"master source {tag} HASH MISMATCH\n"
                    f"    {p}\n    expected {expected[:16]}...  got {actual[:16]}..."
                )

    for tag, entry in manifest.get("weight_history", {}).items():
        p = Path(entry["path"])
        if not p.exists():
            problems.append(f"weight_history {tag} missing: {p}")
            continue
        if _sha256(p) != entry["sha"]:
            problems.append(f"weight_history {tag} HASH MISMATCH: {p}")

    weights_entry = manifest.get("weights_entry") or {}
    if weights_entry:
        for tag, path_str in weights_entry.get("source_files", {}).items():
            expected = weights_entry.get("source_shas", {}).get(tag)
            p = Path(path_str)
            if not p.exists():
                problems.append(f"weights source {tag} missing: {p}")
                continue
            if expected and _sha256(p) != expected:
                problems.append(f"weights source {tag} HASH MISMATCH: {p}")

    if problems:
        print("COHERENCE CHECK FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(f"\nMeaning: the committed artifacts were NOT produced from a single\n"
              f"pipeline run. Re-run `bash regenerate_canonicals.sh && python\n"
              f"export_csvs.py` from a clean state to recover coherence.",
              file=sys.stderr)
        return 1
    print("COHERENCE OK: master, weights, weight_history, and standalone hypothesis\n"
          "CSVs all trace to the same pipeline run (per export_manifest.json).")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Export canonical CSVs + manifest.")
    ap.add_argument("--label", choices=VALID_LABELS, default=None,
                    help="Calibration label. Omitted = export both canonical y_60 variants.")
    ap.add_argument("--variant", choices=VALID_VARIANTS, default=None,
                    help="Model variant. Omitted = export both canonical variants.")
    ap.add_argument("--out", type=Path, default=None,
                    help="Master CSV output path (only when --variant specified).")
    ap.add_argument("--skip-weights", action="store_true",
                    help="Don't regenerate weights.csv.")
    ap.add_argument("--skip-hypotheses", action="store_true",
                    help="Don't regenerate standalone hypothesis_*.csv files.")
    ap.add_argument("--skip-manifest", action="store_true",
                    help="Don't write export_manifest.json.")
    ap.add_argument("--skip-data-inventory", action="store_true",
                    help="Don't regenerate data_inventory.csv from live parquets.")
    ap.add_argument("--skip-raw-export", action="store_true",
                    help="Don't regenerate raw_data_export.csv from live parquets.")
    ap.add_argument("--check-coherence", action="store_true",
                    help="Verify files on disk match the manifest; exit 0 if OK, "
                         "1 if not. No writes.")
    ap.add_argument("--check-freshness", type=float, default=None, metavar="MAX_HOURS",
                    help="Exit 1 if the newest parquet under data/raw/ is older "
                         "than MAX_HOURS. Catches the cache-skip / fetcher-broken "
                         "failure modes that don't surface in /manifest.generated_at. "
                         "No writes.")
    args = ap.parse_args()

    if args.check_coherence:
        sys.exit(check_coherence())

    if args.check_freshness is not None:
        sys.exit(check_freshness(args.check_freshness))

    master_entries = []

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.label is None and args.variant is None:
        for variant in VALID_VARIANTS:
            master_entries.append(build_master(
                "y_60", variant, OUT_DIR / f"master_daily_view_{variant}.csv"))
    elif args.label is None or args.variant is None:
        sys.stderr.write("ERROR: must specify both --label and --variant, or neither.\n")
        sys.exit(1)
    else:
        # Resolve a relative --out against OUT_DIR; absolute --out is honored as-is.
        if args.out is None:
            out = OUT_DIR / f"master_daily_view_{args.variant}.csv"
        elif args.out.is_absolute():
            out = args.out
        else:
            out = OUT_DIR / args.out
        master_entries.append(build_master(args.label, args.variant, out))

    weights_entry = {}
    if not args.skip_weights:
        weights_entry = build_weights_csv(OUT_DIR / "weights.csv")

    hypothesis_entries = []
    if not args.skip_hypotheses:
        hypothesis_entries = build_standalone_hypothesis_csvs(OUT_DIR)

    # Fix #3: regenerate the previously-static reference CSVs from live parquets
    # so /data_inventory and /raw_data_export stop serving stale 2026-04-22 snapshots.
    if not args.skip_data_inventory:
        regenerate_data_inventory(OUT_DIR / "data_inventory.csv")
    if not args.skip_raw_export:
        regenerate_raw_data_export(OUT_DIR / "raw_data_export.csv")

    if not args.skip_manifest:
        weight_history_entries = build_weight_history_manifest()
        write_manifest(master_entries, weights_entry,
                       weight_history_entries, hypothesis_entries)


if __name__ == "__main__":
    main()
