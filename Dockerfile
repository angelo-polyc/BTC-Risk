# Dockerfile — BTC drawdown-probability model ops layer.
#
# One image builds the whole thing: model pipeline + ops layer deps, with
# the committed CSVs and code. Used by Railway (railway.toml), Render,
# Fly, or any container host. Replit does NOT need this (it uses .replit).
#
# Build:   docker build -t btc-model .
# Run:     docker run -p 8000:8000 -e BTC_API_TOKEN=xxx btc-model

FROM python:3.12-slim

# ─── System deps ──────────────────────────────────────────────────────────
# git: required at runtime by audit_log.py for committing paper_trading_log.csv
# bash: runner.sh uses bash-specific features
# tini: clean signal forwarding so both processes die on SIGTERM
RUN apt-get update && apt-get install -y --no-install-recommends \
    git bash tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ─── Python deps (cached layer — only rebuilds if requirements.txt changes) ─
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ─── App code ─────────────────────────────────────────────────────────────
COPY . .

# ─── Git identity for audit commits ───────────────────────────────────────
# The deployment writes daily commits to paper_trading_log.csv via
# audit_log.py. Set an identity so commits don't fail on missing config.
# Can be overridden at runtime via env vars or a startup hook.
RUN git config --global user.email "vm@btc-risk.deploy" && \
    git config --global user.name  "BTC Model VM" && \
    git config --global --add safe.directory /app

# ─── Runtime port ─────────────────────────────────────────────────────────
# PORT is conventionally injected by Railway / Render / Fly. runner.sh
# reads it; default 8000 if unset.
ENV PORT=8000
EXPOSE 8000

# ─── Entrypoint ───────────────────────────────────────────────────────────
# tini is PID 1 — reaps zombies and forwards SIGTERM cleanly to runner.sh,
# which forwards to both uvicorn and daily_pipeline.
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["bash", "runner.sh"]
