"""Daily pipeline runner for the BTC drawdown-probability model.

Runs the full refresh sequence on a configurable cron schedule:

    bash run_all.sh
    python3 health_check.py --out health_check.csv
    python3 shadow_tracker.py --out shadow_state.csv

Persistent foreground process. Intended for a Replit Reserved VM or any
long-running host. Reads schedule and paths from runtime_config.yaml.

Behavior:
    * Each scheduled run is one subprocess chain. A non-zero exit from any
      step stops the chain and logs the failure; subsequent scheduled runs
      are still attempted at the next tick.
    * The process itself exits non-zero only on unrecoverable config /
      scheduler errors (never on pipeline step failures — those are
      recoverable by the next run).
    * Logs: rotating, default 14-day retention, written to
      logs/pipeline.log. Each step's stdout/stderr is captured and echoed
      into the main log with a [stdout]/[stderr] prefix.
    * Runs immediately on startup if `run_on_startup: true` in config.
      Useful for a fresh deploy so the system has data before the first
      cron tick.

Run:
    python3 daily_pipeline.py
    python3 daily_pipeline.py --config runtime_config.yaml
    python3 daily_pipeline.py --once     # run the sequence once and exit
    python3 daily_pipeline.py --dry-run  # show what would run and exit

No behavior-affecting model knobs. Operational knobs only (cron, paths,
log retention). All model code is untouched; this layer sits on top.
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

# ─── Defaults (overridden by runtime_config.yaml) ─────────────────────────────

DEFAULT_CONFIG: dict[str, Any] = {
    "project_dir": ".",
    "cron": "0 22 * * *",            # 22:00 UTC daily, post-CME close
    "timezone": "UTC",
    "log_dir": "logs",
    "log_filename": "pipeline.log",
    "log_retention_days": 14,
    "run_on_startup": False,
    "steps": [
        {"name": "run_all",        "cmd": ["bash", "run_all.sh"], "timeout_sec": 1800},
        {"name": "health_check",   "cmd": ["python3", "health_check.py", "--out", "health_check.csv"], "timeout_sec": 300},
        {"name": "shadow_tracker", "cmd": ["python3", "shadow_tracker.py", "--out", "shadow_state.csv"], "timeout_sec": 300},
        {"name": "audit_log",      "cmd": ["python3", "audit_log.py"], "timeout_sec": 60},
    ],
}


def load_config(path: Path) -> dict[str, Any]:
    """Merge user config onto defaults. Missing file => defaults only."""
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        with path.open() as f:
            user = yaml.safe_load(f) or {}
        # Shallow merge; steps list fully replaces if provided.
        for k, v in user.items():
            cfg[k] = v
    return cfg


# ─── Logging ──────────────────────────────────────────────────────────────────

def configure_logging(log_dir: Path, filename: str, retention_days: int) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / filename

    fmt = logging.Formatter(
        fmt="%(asctime)s UTC [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    fmt.converter = time.gmtime

    handler = logging.handlers.TimedRotatingFileHandler(
        log_path, when="midnight", interval=1, backupCount=retention_days, utc=True
    )
    handler.setFormatter(fmt)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(fmt)

    logger = logging.getLogger("daily_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.addHandler(stream)
    logger.propagate = False
    return logger


# ─── Step execution ───────────────────────────────────────────────────────────

def run_step(step: dict[str, Any], cwd: Path, logger: logging.Logger) -> bool:
    """Run one subprocess step. Returns True on success, False on failure."""
    name = step["name"]
    cmd = step["cmd"]
    timeout = step.get("timeout_sec", 1800)
    logger.info("step=%s starting cmd=%s cwd=%s", name, " ".join(cmd), cwd)

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.error("step=%s TIMEOUT after %ds", name, timeout)
        return False
    except FileNotFoundError as e:
        logger.error("step=%s command not found: %s", name, e)
        return False

    elapsed = time.time() - t0
    if proc.stdout:
        for line in proc.stdout.splitlines():
            logger.info("step=%s [stdout] %s", name, line)
    if proc.stderr:
        for line in proc.stderr.splitlines():
            logger.info("step=%s [stderr] %s", name, line)

    if proc.returncode != 0:
        logger.error("step=%s FAILED rc=%d elapsed=%.1fs", name, proc.returncode, elapsed)
        return False

    logger.info("step=%s OK elapsed=%.1fs", name, elapsed)
    return True


def run_sequence(cfg: dict[str, Any], logger: logging.Logger) -> bool:
    """Run all steps in order. Stops on first failure. Returns overall success."""
    project_dir = Path(cfg["project_dir"]).expanduser().resolve()
    if not project_dir.exists():
        logger.error("project_dir does not exist: %s", project_dir)
        return False

    start_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    logger.info("=== daily sequence start %s project=%s ===", start_utc, project_dir)

    for step in cfg["steps"]:
        if not run_step(step, project_dir, logger):
            logger.error("=== daily sequence ABORTED at step=%s ===", step["name"])
            return False

    logger.info("=== daily sequence OK ===")
    return True


# ─── Scheduler ────────────────────────────────────────────────────────────────

def build_trigger(cron_expr: str, tz: str) -> CronTrigger:
    parts = cron_expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron must be 5 fields (m h dom mon dow), got: {cron_expr!r}")
    m, h, dom, mon, dow = parts
    return CronTrigger(minute=m, hour=h, day=dom, month=mon, day_of_week=dow, timezone=tz)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="runtime_config.yaml",
                    help="path to runtime_config.yaml (default: ./runtime_config.yaml)")
    ap.add_argument("--once", action="store_true",
                    help="run the sequence once and exit (no scheduler)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without running anything")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    logger = configure_logging(
        Path(cfg["log_dir"]),
        cfg["log_filename"],
        int(cfg["log_retention_days"]),
    )

    logger.info("daily_pipeline starting config=%s", cfg_path if cfg_path.exists() else "<defaults>")
    logger.info("project_dir=%s cron=%s tz=%s steps=%s",
                cfg["project_dir"], cfg["cron"], cfg["timezone"],
                [s["name"] for s in cfg["steps"]])

    if args.dry_run:
        logger.info("dry-run: not executing anything")
        return 0

    if args.once:
        ok = run_sequence(cfg, logger)
        return 0 if ok else 1

    # Persistent scheduler
    try:
        trigger = build_trigger(cfg["cron"], cfg["timezone"])
    except Exception as e:
        logger.error("invalid cron/timezone: %s", e)
        return 2

    scheduler = BlockingScheduler(timezone=cfg["timezone"])
    scheduler.add_job(
        run_sequence, trigger=trigger, args=[cfg, logger],
        id="daily_sequence", name="BTC model daily refresh",
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )

    # Graceful shutdown on SIGINT / SIGTERM
    def _shutdown(signum, frame):  # noqa: ARG001
        logger.info("received signal %d, shutting down scheduler", signum)
        scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    if cfg.get("run_on_startup"):
        logger.info("run_on_startup=true; executing sequence before entering scheduler loop")
        run_sequence(cfg, logger)

    logger.info("scheduler started; next run at %s", trigger.get_next_fire_time(None, datetime.now(timezone.utc)))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
