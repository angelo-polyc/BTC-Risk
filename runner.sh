#!/usr/bin/env bash
#
# runner.sh — Replit Reserved VM Deployment entrypoint.
#
# Starts two persistent processes inside the same container:
#
#   1. daily_pipeline.py (APScheduler, runs in background, writes to logs/)
#   2. api_server.py     (uvicorn foreground; mounts MCP at /mcp)
#
# Replit's deployment proxy routes the single public HTTPS port to whichever
# port we bind. We serve REST and MCP from the same port (PORT env var,
# injected by Replit) so both surfaces share one URL and one TLS cert.
#
# The scheduler is backgrounded so that a pipeline-run failure never takes
# down the API; the API is foreground so uvicorn's exit is the container's
# exit, triggering a Replit restart if anything goes wrong with the web
# server itself.

set -u  # unset vars are errors (strict mode minus -e; we want to continue
        # even if the scheduler background job has transient issues)

# ─── Load secrets ─────────────────────────────────────────────────────────────
# Replit injects env vars from the deployment's Secrets panel at runtime,
# but support a .env.ops file as a fallback for non-Replit hosts.
if [[ -f .env.ops ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.ops
  set +a
fi

# ─── Validate required secrets ────────────────────────────────────────────────
: "${BTC_API_TOKEN:?BTC_API_TOKEN must be set}"

# Phase 2 volume refactor: top-level CSVs and the manifest live on the
# persistent volume (/app/data on Railway), not on ephemeral /app. Both env
# vars default to /app/data here so a Railway redeploy serves volume-backed
# state immediately on boot — no cold-pull required for the API to be useful.
# These can still be overridden via Railway's Variables panel.
: "${BTC_DATA_DIR:=/app/data}"
: "${BTC_MODEL_DIR:=$BTC_DATA_DIR}"
: "${BTC_MODEL_ROOT:=$(pwd)}"
export BTC_DATA_DIR BTC_MODEL_DIR BTC_MODEL_ROOT

# Replit injects PORT; fall back to 8000 locally.
: "${PORT:=8000}"

# ─── Bootstrap committed-in-git static reference CSVs onto the volume ─────────
# These files don't change between refits but live with the code at /app/.
# Phase 2 points the API readers at /app/data, so we copy them across on
# boot if missing. Subsequent boots are no-ops.
# Without this step, /thresholds, /weight_history, /pinning_audit, and the
# replayed-monitor history endpoints would 500 on a fresh volume.
mkdir -p "$BTC_DATA_DIR"
for f in thresholds.csv \
         weight_history_wf365_y_60.csv \
         weight_history_wf365_y_30.csv \
         pinning_audit_findings.csv \
         health_check_history.csv \
         health_check_history_extended.csv; do
  if [ ! -f "$BTC_DATA_DIR/$f" ] && [ -f "$BTC_MODEL_ROOT/$f" ]; then
    cp "$BTC_MODEL_ROOT/$f" "$BTC_DATA_DIR/$f"
    echo "[runner] bootstrapped $f to $BTC_DATA_DIR"
  fi
done

# ─── Logs ─────────────────────────────────────────────────────────────────────
mkdir -p logs

# ─── Scheduler (background) ───────────────────────────────────────────────────
echo "[runner] starting daily_pipeline (background)"
python3 daily_pipeline.py >>logs/scheduler.stdout.log 2>&1 &
SCHEDULER_PID=$!
echo "[runner] daily_pipeline pid=$SCHEDULER_PID"

# Forward shutdown signals so both children die cleanly on redeploy.
cleanup() {
  echo "[runner] received shutdown signal, stopping scheduler pid=$SCHEDULER_PID"
  kill -TERM "$SCHEDULER_PID" 2>/dev/null || true
  wait "$SCHEDULER_PID" 2>/dev/null || true
  exit 0
}
trap cleanup INT TERM

# ─── API (foreground) ─────────────────────────────────────────────────────────
echo "[runner] starting api_server on 0.0.0.0:$PORT (REST + MCP at /mcp)"
exec python3 -m uvicorn api_server:app \
  --host 0.0.0.0 \
  --port "$PORT" \
  --log-level info
