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
: "${BTC_MODEL_DIR:=$(pwd)}"
export BTC_MODEL_DIR

# Replit injects PORT; fall back to 8000 locally.
: "${PORT:=8000}"

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
