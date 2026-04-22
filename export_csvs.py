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

HERE = Path(__file__).parent
ROOT = Path(os.environ.get("BTC_MODEL_ROOT", HERE))

DERIVED = ROOT / "data/derived"
HYP = ROOT / "data/hypotheses"
FINAL = ROOT / "data/final"

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

MANIFEST_PATH = HERE / "export_manifest.json"


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
    ap.add_argument("--check-coherence", action="store_true",
                    help="Verify files on disk match the manifest; exit 0 if OK, "
                         "1 if not. No writes.")
    args = ap.parse_args()

    if args.check_coherence:
        sys.exit(check_coherence())

    master_entries = []

    if args.label is None and args.variant is None:
        for variant in VALID_VARIANTS:
            master_entries.append(build_master(
                "y_60", variant, HERE / f"master_daily_view_{variant}.csv"))
    elif args.label is None or args.variant is None:
        sys.stderr.write("ERROR: must specify both --label and --variant, or neither.\n")
        sys.exit(1)
    else:
        out = args.out or (HERE / f"master_daily_view_{args.variant}.csv")
        master_entries.append(build_master(args.label, args.variant, out))

    weights_entry = {}
    if not args.skip_weights:
        weights_entry = build_weights_csv(HERE / "weights.csv")

    hypothesis_entries = []
    if not args.skip_hypotheses:
        hypothesis_entries = build_standalone_hypothesis_csvs(HERE)

    if not args.skip_manifest:
        weight_history_entries = build_weight_history_manifest()
        write_manifest(master_entries, weights_entry,
                       weight_history_entries, hypothesis_entries)


if __name__ == "__main__":
    main()
