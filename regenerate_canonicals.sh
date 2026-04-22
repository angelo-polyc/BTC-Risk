#!/bin/bash
# Regenerate the two canonical models' outputs.
# Runs each canonical twice: once with y_60 (deployed) and once with y_30 (reference).
#
# v13 change (2026-04-18): loop order is y_30 then y_60 so that the final state of
# `data/hypotheses/*.parquet` is the CANONICAL (y_60) state. Prior order (y_60 then
# y_30) left y_30-built parquets on disk after the script finished, which silently
# broke any off-pipeline inspection that assumed canonical state. See
# refit_report_v13.md §Pipeline-ergonomics.
set -e
cd "$(dirname "$0")"
# Respect externally-set BTC_MODEL_ROOT; default to the script dir so the
# pipeline works out-of-the-box on a fresh project checkout.
export BTC_MODEL_ROOT="${BTC_MODEL_ROOT:-$(pwd)}"
export PYTHONWARNINGS=ignore

# Ensure output directories exist (pipeline scripts don't auto-create them).
mkdir -p "$BTC_MODEL_ROOT/data/raw" \
         "$BTC_MODEL_ROOT/data/derived" \
         "$BTC_MODEL_ROOT/data/hypotheses" \
         "$BTC_MODEL_ROOT/data/final"

echo "BTC_MODEL_ROOT=$BTC_MODEL_ROOT"
echo "Loop order: y_30 first, y_60 last (canonical hypothesis state persists)."
echo

# ---- Build hypotheses once per label (they depend on CALIB_LABEL) ----
# y_30 first, y_60 last — so the final data/hypotheses/ state is the deployed one.
for LABEL in y_30 y_60; do
    echo "=== Building foundation + 6 hypotheses (CALIB_LABEL=$LABEL) ==="
    export CALIB_LABEL=$LABEL
    python build_foundation.py > /dev/null
    for h in macro_equities cme crypto_derivatives classic_cycle etf_flows eth; do
        python build_${h}.py > /dev/null
    done
    echo "Hypotheses done for $LABEL."
    echo

    # ---- CANONICAL 1: Single-fit + rolling-730d percentile ----
    echo "=== Canonical 1: single_fit + pct=rolling-730  (LABEL=$LABEL) ==="
    export WALK_FORWARD=0
    export PERCENTILE_WINDOW=730
    # sf730 position thresholds (recalibrated 2026-04-15; see refit_report_v8)
    export POSITION_LONG_THR=0.55
    export POSITION_DEF_THR=0.65
    python build_robust.py 2>&1 | tail -6
    cp "$BTC_MODEL_ROOT/data/final/ensemble_robust.parquet"         "$BTC_MODEL_ROOT/data/final/ensemble_sf730_${LABEL}.parquet"
    cp "$BTC_MODEL_ROOT/data/final/ensemble_weights_robust.csv"     "$BTC_MODEL_ROOT/data/final/ensemble_weights_sf730_${LABEL}.csv"
    echo

    # ---- CANONICAL 2: Walk-forward + rolling-365d percentile ----
    echo "=== Canonical 2: wf_baseline + pct=rolling-365  (LABEL=$LABEL) ==="
    export WALK_FORWARD=1
    export WALK_CADENCE_MONTHS=1
    export WALK_WINDOW_MONTHS=expanding
    export WARMUP_MONTHS=12
    export WALK_SMOOTH_K=1
    export PERCENTILE_WINDOW=365
    # wf365 position thresholds
    # v14 (2026-04-18): widened from (0.55, 0.70) to (0.45, 0.80) per
    # experiment_taper_sweep.md. Full Sharpe +0.04, hold-out Sharpe +0.10,
    # full MaxDD better by 6.2pp. Trade: gives up bull-year upside (2024 -9pp)
    # for bear-year protection (2022 +6.7pp return, +5.8pp MaxDD).
    export POSITION_LONG_THR=0.45
    export POSITION_DEF_THR=0.80
    python build_robust.py 2>&1 | tail -6
    cp "$BTC_MODEL_ROOT/data/final/ensemble_robust.parquet"         "$BTC_MODEL_ROOT/data/final/ensemble_wf365_${LABEL}.parquet"
    cp "$BTC_MODEL_ROOT/data/final/ensemble_weights_robust.csv"     "$BTC_MODEL_ROOT/data/final/ensemble_weights_wf365_${LABEL}.csv"
    if [ -f "$BTC_MODEL_ROOT/data/final/weight_history_robust.csv" ]; then
        cp "$BTC_MODEL_ROOT/data/final/weight_history_robust.csv"   "$BTC_MODEL_ROOT/data/final/weight_history_wf365_${LABEL}.csv"
    fi
    echo
done
echo "All 4 variants regenerated: sf730/wf365 × y_60/y_30."
echo "Final hypothesis state: CALIB_LABEL=y_60 (canonical)."
