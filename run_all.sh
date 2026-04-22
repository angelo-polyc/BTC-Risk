#!/bin/bash
# run_all.sh — one-command cold-start pipeline.
#
# From a fresh checkout with no data pulled, this runs end-to-end:
#   1. Install Python dependencies
#   2. Pull raw data from all 8 sources (FRED, Velo, Coinglass, CFTC, Yahoo, Artemis)
#   3. Patch known parser bugs
#   4. Regenerate all 4 canonical variants
#   5. Export master_daily_view_*.csv for inspection
#
# Runtime: ~2 minutes wall clock on a warm machine.
# Credentials: baked into pull_all_raw_data.py + pull_artemis_etf.py. See
# credentials.md for key inventory and rotation notes.
#
# Override ARTEMIS_API_KEY via env var if needed; otherwise it reads the baked-in
# key from pull_artemis_etf.py.
#
# Usage:
#   bash run_all.sh                    # full cold-start
#   bash run_all.sh --skip-pull        # skip data pull (assumes data/raw/ populated)
#   bash run_all.sh --skip-deps        # skip pip install
#   bash run_all.sh --skip-pull --skip-deps

set -e
cd "$(dirname "$0")"
export BTC_MODEL_ROOT="${BTC_MODEL_ROOT:-$(pwd)}"

SKIP_PULL=0
SKIP_DEPS=0
for arg in "$@"; do
    case "$arg" in
        --skip-pull) SKIP_PULL=1 ;;
        --skip-deps) SKIP_DEPS=1 ;;
        -h|--help)
            sed -n '2,20p' "$0"
            exit 0
            ;;
        *) echo "Unknown flag: $arg" >&2; exit 1 ;;
    esac
done

echo "========================================================================"
echo "BTC Model — cold-start pipeline"
echo "BTC_MODEL_ROOT=$BTC_MODEL_ROOT"
echo "========================================================================"

mkdir -p data/raw data/derived data/hypotheses data/final

if [ $SKIP_DEPS -eq 0 ]; then
    echo
    echo "─── Step 1: install Python deps ──────────────────────────────────"
    pip install --break-system-packages --quiet \
        pandas numpy scipy scikit-learn pyarrow yfinance fredapi velodata \
        cot_reports artemis requests
    echo "Deps installed."
fi

if [ $SKIP_PULL -eq 0 ]; then
    echo
    echo "─── Step 2: pull raw data (9 sources, including Artemis) ────────"
    echo "(expect ~80 seconds)"
    # v13: Artemis is now source #9 inside pull_all_raw_data.py; 'all' covers it.
    # Parser patches are folded into the per-source handlers — no separate
    # fix_parsers.py step needed. ARTEMIS_API_KEY env var takes priority over
    # the baked-in fallback.
    python3 pull_all_raw_data.py --source all --out-dir data/raw 2>&1 | \
        grep -E "(START|DONE|SUMMARY|WARNING|ERROR)" | head -50
fi

echo
echo "─── Step 3: regenerate canonicals (4 variants) ──────────────────"
bash regenerate_canonicals.sh

echo
echo "─── Step 4: export master_daily_view CSVs ──────────────────────"
python3 export_csvs.py --label y_60 --variant wf365 --out master_daily_view_wf365.csv
python3 export_csvs.py --label y_60 --variant sf730 --out master_daily_view_sf730.csv

echo
echo "========================================================================"
echo "Pipeline complete. Today's call in master_daily_view_wf365.csv (last row)."
echo "========================================================================"
tail -1 master_daily_view_wf365.csv
