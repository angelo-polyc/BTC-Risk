"""Build entire pipeline for a given calibration label.

Usage:
  CALIB_LABEL=y_60 python run_full_pipeline.py    # canonical
  CALIB_LABEL=y_30 python run_full_pipeline.py    # comparison

The pipeline runs from the directory containing this script. Outputs go to
./data/{raw,derived,hypotheses,final}/ relative to this script's location,
unless BTC_MODEL_ROOT env var overrides that.
"""
import os
import subprocess
import sys
import shutil
from pathlib import Path

LABEL = os.environ.get("CALIB_LABEL", "y_60")
HERE = Path(__file__).parent.resolve()
ROOT = Path(os.environ.get("BTC_MODEL_ROOT", HERE))

# Ensure data subdirs exist
for d in ["raw", "derived", "hypotheses", "final"]:
    (ROOT / "data" / d).mkdir(parents=True, exist_ok=True)

def run(script):
    cmd = f"python {script}"
    print(f"\n>>> {cmd}")
    r = subprocess.run(
        cmd, shell=True, cwd=HERE, capture_output=True, text=True,
        env={**os.environ, "CALIB_LABEL": LABEL, "BTC_MODEL_ROOT": str(ROOT),
             "PYTHONWARNINGS": "ignore"},
    )
    if r.returncode != 0:
        print("STDERR:", r.stderr[-2000:])
        sys.exit(1)
    return r.stdout

# Foundation (label-independent)
print(run("build_foundation.py"))

# 6 hypotheses
for name in ["macro_equities", "cme", "crypto_derivatives", "classic_cycle", "etf_flows", "eth"]:
    print(run(f"build_{name}.py"))

# Robust ensemble + backtest
print(run("build_robust.py"))

# Tag outputs with the label
for src_rel, dst_rel in [
    ("data/final/ensemble_robust.parquet",      f"data/final/ensemble_robust_{LABEL}.parquet"),
    ("data/final/ensemble_weights_robust.csv",  f"data/final/ensemble_weights_robust_{LABEL}.csv"),
]:
    s = ROOT / src_rel
    if s.exists():
        shutil.copy(s, ROOT / dst_rel)
        print(f"tagged → {dst_rel}")
