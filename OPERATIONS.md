# OPERATIONS.md — BTC Model Ops Layer

Runbook for the operational services that sit on top of the committed
pipeline code. The pipeline itself (`run_all.sh`, `build_*.py`,
`common.py`, `regenerate_canonicals.sh`, `health_check.py`,
`shadow_tracker.py`) is untouched by this layer.

Four services, all optional and independently runnable:

| Service                | File              | Purpose                                              |
| ---------------------- | ----------------- | ---------------------------------------------------- |
| Daily pipeline runner  | `daily_pipeline.py` | Cron-style refresh: `run_all.sh` + monitors + audit  |
| Audit log              | `audit_log.py`      | Git-commits today's model call, one commit per day   |
| REST API + MCP         | `api_server.py`     | HTTP/JSON REST and MCP (at `/mcp`) on one port, bearer-auth |
| MCP server (standalone)| `mcp_server.py`     | Same MCP surface as a standalone app for local dev    |

## Production topology (Replit Reserved VM Deployment)

The production deployment is a single Replit Reserved VM Deployment running
`runner.sh`, which in turn runs:

  * `daily_pipeline.py` in the background (scheduler for the daily refresh)
  * `api_server.py` in the foreground — binds `$PORT`, serves REST at `/`
    and MCP at `/mcp` behind one bearer token

One container, one public HTTPS URL, one TLS cert, one bearer token.
`mcp_server.py` is NOT run as a separate process in production — its
`FastMCP` instance is mounted into `api_server.py` at `/mcp` for a single-
process topology. `mcp_server.py` remains runnable standalone for local
development.

See `.replit` for the deployment config and `runner.sh` for the entrypoint.

## One-time setup

### Install additional dependencies

```bash
pip install --break-system-packages -r requirements-ops.txt
```

The pipeline's own deps (pandas, numpy, scipy, sklearn, fredapi, etc.)
continue to be handled by `run_all.sh`.

### Secrets

Create `.env.ops` in the project root — **not committed to git**:

```bash
# Any long random string. Generate with:
#   python3 -c "import secrets; print(secrets.token_hex(32))"
BTC_API_TOKEN=<paste generated token here>

# Password for the browser dashboard at /dashboard. Pick something
# memorable — browser will prompt for it via Basic Auth. Rotate
# independently of BTC_API_TOKEN. On Railway, add this to the Service
# Variables panel alongside BTC_API_TOKEN (not to .env.ops, which isn't
# read by the Railway deployment).
BTC_DASHBOARD_PASSWORD=<pick something memorable>

# Absolute path to the project checkout
BTC_MODEL_DIR=/home/runner/btc_model

# Optional — leave defaults unless host/port collides
BTC_API_HOST=0.0.0.0
BTC_API_PORT=8787
MCP_HOST=0.0.0.0
MCP_PORT=8788
```

Source before starting any service:

```bash
set -a && source .env.ops && set +a
```

### `.gitignore` additions

Append:

```
.env.ops
logs/
health_check.csv
shadow_state.csv
```

The last two regenerate on every daily run and shouldn't carry state
between deployments. **Do NOT gitignore `paper_trading_log.csv`** — that
file is the audit record; the whole point is that it's committed. See
Phase 2 below.

---

## Phase 1 — Daily pipeline runner

### What it does

On a cron schedule (default 22:00 UTC daily, post-CME close), runs:

1. `bash run_all.sh` — full pipeline refresh (pull → regen → export)
2. `python3 health_check.py --out health_check.csv` — drift monitor
3. `python3 shadow_tracker.py --out shadow_state.csv` — counterfactual log
4. `python3 audit_log.py` — git-commit today's row to the audit trail (Phase 2)

Each step is timeout-bounded; any non-zero exit aborts the chain (next
steps skipped) and is logged at ERROR. The scheduler itself continues
running and will retry on the next tick.

### Config

`runtime_config.yaml` at project root. All knobs are operational (cron
expression, log paths, timeouts, step list); none affect model
behavior. Defaults are in `daily_pipeline.py:DEFAULT_CONFIG`.

### Run

```bash
# Foreground (persistent scheduler)
python3 daily_pipeline.py

# One-shot — run the sequence once and exit. Useful for testing or
# triggering an ad-hoc refresh.
python3 daily_pipeline.py --once

# Show plan without running anything
python3 daily_pipeline.py --dry-run
```

On Replit Reserved VM, configure "Always On" to run
`python3 daily_pipeline.py` as the background process. Under systemd,
use a simple `ExecStart=/usr/bin/python3 daily_pipeline.py` unit with
`Restart=on-failure`.

### Logs

`logs/pipeline.log` with daily rotation, 14-day retention. Each step's
stdout and stderr are captured and echoed into the main log with
`[stdout]` / `[stderr]` prefixes. Watch a deploy go live:

```bash
tail -f logs/pipeline.log
```

### Verify it's working

```bash
python3 daily_pipeline.py --once
# Expect final line:  === daily sequence OK ===
# Then check:
ls -lt master_daily_view_wf365.csv health_check.csv shadow_state.csv
# All three mtimes should be within the last couple minutes.
# And audit log:
git log --oneline -- paper_trading_log.csv | head -3
# Expect one new commit with today's date if a new daily row was produced.
```

---

## Phase 2 — Git-based audit log

### What it does

After the daily pipeline refresh, appends today's row from
`master_daily_view_wf365.csv` to `paper_trading_log.csv` and commits
with a structured message:

```
paper trading YYYY-MM-DD: regime=X position=Y.YY pct=Z.ZZZ
```

**The log starts empty on first deploy — no backfill.** This is by
design. The value of the log is that each commit is timestamped at the
moment the model produced that day's call, which makes the record
unfalsifiable. Retroactively populating the log would defeat the
purpose; anyone reviewing later could not distinguish "what the model
said that day" from "what someone wrote after the fact." Each
`git log` entry carries its own authorship timestamp, so the commit
history itself is the evidence.

The log row carries more columns than the commit message (ensemble
score, all five hypothesis scores, ETH reference score, returns) so
the log alone is enough to reconstruct the full call. The commit
message carries just the summary fields a human can read in one line.

### Idempotency

If the script runs twice in the same day (cron overlap, manual rerun,
one-shot during testing), the second run is a no-op — no new row, no
empty commit, exit 0. Safe to run as often as you like.

### Git setup (one-time)

The audit log needs a git repository to write into. **Two options:**

**Option A (simpler): same repo as the rest of the project.** The
daily runner's checkout on the VM is the log repo. `paper_trading_log.csv`
lives alongside everything else. This works as long as the VM's
checkout stays on a stable branch and doesn't get rebased from under
the audit commits. Recommend a dedicated branch on the VM (e.g.
`live-log`) that only the audit log writes to — merge or cherry-pick
from the dev branch as needed.

**Option B (cleaner separation): dedicated audit repo.** Create a
separate repo whose only content is `paper_trading_log.csv`. Point
`audit_log.py --log-dir` at it. Advantage: audit commits are never
entangled with pipeline code changes.

Initial setup for Option A:

```bash
cd $BTC_MODEL_DIR
git checkout -b live-log           # optional, but recommended
git config user.email "vm@replit.local"
git config user.name "BTC Model VM"
# If you want automatic push:
git remote -v                      # confirm 'origin' points where you want
```

Initial setup for Option B:

```bash
mkdir -p ~/btc_audit_log && cd ~/btc_audit_log
git init
git remote add origin <your-remote-url>
git commit --allow-empty -m "init audit log"
git config user.email "vm@replit.local"
git config user.name "BTC Model VM"
```

Then in `runtime_config.yaml`, change the step cmd to:

```yaml
  - name: audit_log
    cmd: ["python3", "audit_log.py", "--log-dir", "/home/runner/btc_audit_log"]
    timeout_sec: 60
```

### Push options

The default `cmd: ["python3", "audit_log.py"]` commits locally only.
To also push to a remote:

- `--push` — hard-fail on push error (exit 6). The local commit is
  still made, but the daily sequence is marked FAILED in the log.
  Fine if network is reliable and you want to catch push regressions
  loudly.
- `--push-best-effort` — try to push, exit 0 either way. Local commit
  is always intact; failed pushes are logged but don't flag the
  sequence. **Recommended for most deploys**, since push failures are
  usually transient and the local commit is the core evidence.

Edit `runtime_config.yaml`'s `audit_log` step to pick one:

```yaml
  - name: audit_log
    cmd: ["python3", "audit_log.py", "--push-best-effort"]
    timeout_sec: 60
```

Push credentials must already be configured in the VM's git (SSH key
or personal access token). The first push will need an upstream:
`git push --set-upstream origin <branch>` once by hand.

### Run ad-hoc

```bash
# One-shot, no push, current dir
python3 audit_log.py

# With explicit paths and push
python3 audit_log.py \
  --master-dir $BTC_MODEL_DIR \
  --log-dir ~/btc_audit_log \
  --push-best-effort
```

### Verify

```bash
# After the first daily run or a manual --once:
cat paper_trading_log.csv
git log --oneline -- paper_trading_log.csv | head
# Expect one row in the CSV per day since deploy, and one commit per row.
```

---

## Phase 3 — REST API + MCP (combined in production)

### What it does

Bearer-authenticated read-only HTTP/JSON API over the committed CSV
artifacts, plus the MCP tool surface mounted at `/mcp`. Because it
serves the CSVs directly, the full history (~4,230 rows of model state,
26K rows of raw inputs, all six per-hypothesis series with sub-signals,
walk-forward weight history since 2021-06-30, full drift-monitor
history) is visible from the moment the service starts — no backfill,
no incremental log.

**Single-port topology.** REST and MCP share one uvicorn process, one
public HTTPS URL, and one bearer token. The MCP app from `mcp_server.py`
is mounted into `api_server.py` at `/mcp`; its Streamable-HTTP session
manager runs under FastAPI's lifespan context. This is the production
configuration used by the Replit deployment via `runner.sh`.

### Endpoints

```
GET /today?variant=wf365|sf730             latest model call
GET /history?from=&to=&variant=            master-view range query
GET /hypotheses                            list + which are in-ensemble
GET /hypothesis/{name}?from=&to=           per-hypothesis detail + sub-signals
GET /weights                               regime × hypothesis matrix
GET /weight_history?variant=&label=&from=&to=  walk-forward refit history
GET /health                                latest drift-monitor report
GET /flags                                 only currently-flagged signals
GET /health_history?from=&to=&extended=    replayed monitor history
GET /shadow?from=&to=                      counterfactual positions
GET /raw/columns                           raw-data metadata + column list
GET /raw?columns=&from=&to=                raw slice (columns required)
GET /manifest                              data-freshness fingerprint
GET /thresholds                            position-function thresholds
GET /data_inventory                        input coverage windows
GET /pinning_audit                         pinning findings
GET /status                                smoke + file mtimes
```

Every endpoint requires `Authorization: Bearer $BTC_API_TOKEN`.
OpenAPI docs at `/docs` (also auth-gated).

### Run

```bash
set -a && source .env.ops && set +a
python3 api_server.py
# or (what runner.sh does in production):
python3 -m uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Default port in the production deployment is whatever Replit injects as
`$PORT` (typically 8000, which `.replit` maps to public HTTPS port 80).
Locally, `BTC_API_PORT` (default 8787) controls the bind port when
running `python3 api_server.py` directly.

### Verify it's working

```bash
T="$BTC_API_TOKEN"
PORT=${BTC_API_PORT:-8787}
curl -H "Authorization: Bearer $T" http://localhost:$PORT/status  | jq .latest_date
curl -H "Authorization: Bearer $T" http://localhost:$PORT/today   | jq '{date, regime, position, percentile}'
curl -H "Authorization: Bearer $T" http://localhost:$PORT/flags   | jq '{count, rows: (.rows | map(.signal))}'
# Unauthenticated:
curl -o /dev/null -w "%{http_code}\n" http://localhost:$PORT/today  # expect 401
```

### Browser dashboard (`/dashboard`)

Mounted into the same `api_server.py` process alongside the REST and MCP
routes. Served at `https://btc-risk-model.up.railway.app/dashboard` in
production (on Railway) and at `http://localhost:$PORT/dashboard`
locally. The browser's built-in Basic-Auth prompt gates access behind
`BTC_DASHBOARD_PASSWORD`; the server then reads `dashboard.html` from
disk and substitutes the bearer token into the page so the client-side
JS can call the same REST endpoints the MCP clients and agents use. All
fetches in the page use relative paths, so the Replit→Railway URL change
(or any future host change) is transparent. The bearer token is visible
in page source once authenticated — the trust boundary is "anyone with
the dashboard password also has the bearer token," which matches
single-user personal infra. To sign out, close all tabs for the host;
Basic-Auth credentials are discarded with the process.

Panels rendered: today's call hero with contribution breakdown, BTC
price & position (dual-axis, regime-banded), ensemble percentile with
threshold bands (read live from `/thresholds`), position-function mini
chart, drivers table + flags from `/health`, AUC attribution heatmap,
hypothesis composites over time, sub-signal risk heatmap, track record
(equity curve + perf table scoped to `/manifest.canonical_start`), and
a data-freshness pipeline footer. All ranges have 1Y/3Y/ALL toggles.

Railway setup: add `BTC_DASHBOARD_PASSWORD` to the Service → Variables
panel alongside `BTC_API_TOKEN`. Redeploy (Railway doesn't always
auto-redeploy on variable changes).

Verify:

```bash
curl -u any:$BTC_DASHBOARD_PASSWORD https://btc-risk-model.up.railway.app/dashboard | head -c 200
# expect HTML starting with <!DOCTYPE html>
```

---

## Phase 4 — MCP (mounted into the REST API in production)

### What it does

Exposes the same read surface as the REST API but as MCP tools,
consumable by Claude Code (or any MCP-speaking agent) for
"ask-the-model-directly" workflows.

**Production:** the `FastMCP` instance from `mcp_server.py` is mounted
into `api_server.py` at `/mcp`. One uvicorn process, one port, one
bearer token protects both surfaces. This is what `runner.sh` runs.

**Local development:** `mcp_server.py` remains runnable standalone as a
separate process on its own port (default 8788). This is useful when
iterating on tool definitions without restarting the REST server, but
the production topology does not use it.

Transport: Streamable HTTP at `<base>/mcp`.
Auth: same bearer token as the REST API (`BTC_API_TOKEN`), enforced by
the same `BearerAuthMiddleware` in both mounted and standalone modes.

17 tools, one-to-one with the REST endpoints. See docstrings in
`mcp_server.py` for the full set; the ones an agent typically calls
first are `get_status`, `get_today`, `get_flags`, `get_history`.

### Run standalone (local dev only)

```bash
set -a && source .env.ops && set +a
python3 mcp_server.py
# or
python3 -m uvicorn mcp_server:app --host 0.0.0.0 --port 8788
```

### Wire up Claude Code (production, mounted topology)

In Claude Code's MCP config (`~/.claude/settings.json` or equivalent):

```json
{
  "mcpServers": {
    "btc-drawdown-model": {
      "transport": "streamable-http",
      "url": "https://<your-replit-deployment-host>/mcp",
      "headers": {
        "Authorization": "Bearer <paste BTC_API_TOKEN here>"
      }
    }
  }
}
```

On Replit, `<your-replit-host>` is the Reserved VM's public
`repl.co` or custom domain pointed at port 8788. Locally (agent and
MCP co-hosted), use `http://127.0.0.1:8788/mcp`.

### Verify from the CLI

```python
# smoke.py
import asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async def main():
    async with streamablehttp_client(
        "http://127.0.0.1:8788/mcp",
        headers={"Authorization": "Bearer $BTC_API_TOKEN"},
    ) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            r = await s.call_tool("get_today", {})
            print(r.structuredContent)

asyncio.run(main())
```

Expect 17 tool names and a `get_today` dict with `date`, `regime`,
`percentile`, `position`, five hypothesis scores.

---

## Common troubleshooting

**Service starts but `/today` returns 500.**  Check `BTC_MODEL_DIR`
points at the project root with `master_daily_view_wf365.csv` in it.
`curl /status` will list all expected files and whether they're
present.

**Daily run fails on `run_all`.**  The first thing the pipeline runner
captures is each step's stderr. `grep FAILED logs/pipeline.log` then
look a few lines up for the stderr from `run_all.sh`. Most common
causes: Velo rate-limit on a cold pull (retry it manually), Artemis
key not set, or `data/` permissions.

**MCP agent gets 401.**  The token the agent sends doesn't match
`BTC_API_TOKEN`. Confirm with `env | grep BTC_API_TOKEN` on the
server, and decode the `Authorization` header on the client.

**`generated_at` in `/manifest` is hours old.**  Pipeline hasn't run
today. Check `logs/pipeline.log` for the most recent sequence, or
trigger a refresh with `python3 daily_pipeline.py --once`.

**Step timeout fires on `run_all` after a cold data reset.**  The
Velo pull dominates cold-start time (~25 min). The default 1800 s
(30 min) timeout covers it with margin but not always; raise
`timeout_sec` for the `run_all` step in `runtime_config.yaml` if a
full cold rebuild is expected.

---

## What's not in this layer (deferred to later phases)

- Telegram / email alerting. Phase 5 in the six-phase plan; the
  trigger evaluator in `refit_report_v7.md` §4 is the model.
- Event-alert charts.
- Dedup state file for alerts.
- `make_daily_chart.py` integration (chart is generated but not yet
  automatically distributed).

None of these are blockers for using the shipped stack today.
