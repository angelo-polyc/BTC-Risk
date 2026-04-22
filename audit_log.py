"""Git-based audit log of daily model state.

After each daily pipeline run, append today's row from
master_daily_view_wf365.csv to paper_trading_log.csv and commit with a
structured message. Optionally push to a remote.

Design intent: the commits form an unfalsifiable chronological record
of what the model said on each day and when it said it. The log starts
empty on first deploy — no backfill. The value of this record comes
from each commit being timestamped at the moment the row was produced,
which means retroactively populating the log would defeat the purpose.

Idempotent: if today's date is already in the log (same run fires
twice, manual rerun, cron overlap), the script is a no-op — no new
row, no empty commit, exit 0.

Commit message format (per v14 ops spec):
    paper trading YYYY-MM-DD: regime=X position=Y.YY pct=Z.ZZZ

The logged CSV carries more columns than appear in the commit message
(ensemble score, all five hypothesis scores, returns) so the log
itself is enough to reconstruct the full call without needing the
full master_daily_view back.

Run:
    python3 audit_log.py
    python3 audit_log.py --log-dir /path/to/audit-log-repo --push
    python3 audit_log.py --master-dir /path/to/btc_model --log-dir .

Exit codes:
    0 success (commit written, or no-op because already logged)
    2 master_daily_view not found
    3 log-dir is not a git repository
    4 git add failed
    5 git commit failed
    6 git push failed (only when --push is set)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

# Columns written to paper_trading_log.csv. Order is stable — append only.
LOG_COLUMNS = [
    "date", "regime", "ensemble_score", "percentile", "position",
    "macro_equities_score", "cme_score", "crypto_derivatives_score",
    "classic_cycle_score", "etf_flows_score", "eth_score",
    "btc_return", "strategy_return",
]

LOG_FILENAME = "paper_trading_log.csv"


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd),
        capture_output=True, text=True, check=False,
    )


def is_git_repo(path: Path) -> bool:
    r = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path)
    return r.returncode == 0 and r.stdout.strip() == "true"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--master-dir", default=".",
        help="directory containing master_daily_view_wf365.csv (default: cwd)",
    )
    ap.add_argument(
        "--log-dir", default=".",
        help="git repo directory where paper_trading_log.csv lives (default: cwd)",
    )
    ap.add_argument(
        "--master-filename", default="master_daily_view_wf365.csv",
        help="master view filename (default: master_daily_view_wf365.csv)",
    )
    ap.add_argument(
        "--push", action="store_true",
        help="also push to the configured remote after commit (hard-fails on push error)",
    )
    ap.add_argument(
        "--push-best-effort", action="store_true",
        help="try to push, but exit 0 even if push fails (local commit is still made)",
    )
    ap.add_argument(
        "--remote", default=os.environ.get("AUDIT_GIT_REMOTE", "origin"),
        help="git remote name for push (default: origin or $AUDIT_GIT_REMOTE)",
    )
    ap.add_argument(
        "--branch", default=os.environ.get("AUDIT_GIT_BRANCH"),
        help="branch for push (default: current branch or $AUDIT_GIT_BRANCH)",
    )
    args = ap.parse_args()

    master_dir = Path(args.master_dir).expanduser().resolve()
    log_dir = Path(args.log_dir).expanduser().resolve()
    master_path = master_dir / args.master_filename
    log_path = log_dir / LOG_FILENAME

    # ─── Validation ──────────────────────────────────────────────────────────
    if not master_path.exists():
        print(f"ERROR: master not found: {master_path}", file=sys.stderr)
        return 2

    if not is_git_repo(log_dir):
        print(f"ERROR: not a git repository: {log_dir}", file=sys.stderr)
        print("       run `git init` there and configure a remote, or point "
              "--log-dir at an existing audit repo", file=sys.stderr)
        return 3

    # ─── Read latest row ─────────────────────────────────────────────────────
    master = pd.read_csv(master_path, parse_dates=["date"])
    if master.empty:
        print(f"ERROR: master has no rows: {master_path}", file=sys.stderr)
        return 2
    latest = master.sort_values("date").iloc[-1]
    date_str = latest["date"].strftime("%Y-%m-%d")

    # Project the row into LOG_COLUMNS, preserving NaN as blank on CSV.
    row = {col: latest.get(col, None) for col in LOG_COLUMNS}

    # ─── Idempotency: skip if already logged ────────────────────────────────
    if log_path.exists():
        existing = pd.read_csv(log_path, parse_dates=["date"])
        if not existing.empty:
            last_date = existing["date"].max().strftime("%Y-%m-%d")
            if date_str == last_date:
                print(f"date {date_str} already logged; no-op")
                return 0
            if date_str in existing["date"].dt.strftime("%Y-%m-%d").values:
                # Defensive: out-of-order rerun. Still a no-op.
                print(f"date {date_str} already present in log; no-op")
                return 0
        new_log = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
    else:
        new_log = pd.DataFrame([row], columns=LOG_COLUMNS)

    # Reorder / enforce column set in case prior log was missing a column.
    for col in LOG_COLUMNS:
        if col not in new_log.columns:
            new_log[col] = None
    new_log = new_log[LOG_COLUMNS]

    new_log.to_csv(log_path, index=False)
    print(f"appended row for {date_str} to {log_path}")

    # ─── Build commit message per v14 ops spec ──────────────────────────────
    regime = latest["regime"]
    position = float(latest["position"])
    percentile = float(latest["percentile"])
    msg = (f"paper trading {date_str}: "
           f"regime={regime} position={position:.2f} pct={percentile:.3f}")

    # ─── git add + commit ───────────────────────────────────────────────────
    add = run_git(["add", LOG_FILENAME], cwd=log_dir)
    if add.returncode != 0:
        print(f"ERROR: git add failed: {add.stderr.strip()}", file=sys.stderr)
        return 4

    commit = run_git(["commit", "-m", msg], cwd=log_dir)
    if commit.returncode != 0:
        # Empty commit is the one benign failure path. "nothing to commit"
        # shouldn't happen given the idempotency check above, but belt-and-
        # suspenders: treat it as a no-op if it does.
        if "nothing to commit" in commit.stdout or "nothing to commit" in commit.stderr:
            print("git reports nothing to commit; treating as no-op")
            return 0
        print(f"ERROR: git commit failed: {commit.stderr.strip()}", file=sys.stderr)
        return 5
    print(f"committed: {msg}")

    # ─── Optional push ──────────────────────────────────────────────────────
    if args.push or args.push_best_effort:
        push_args = ["push", args.remote]
        if args.branch:
            push_args.append(args.branch)
        push = run_git(push_args, cwd=log_dir)
        if push.returncode != 0:
            print(f"ERROR: git push failed: {push.stderr.strip()}", file=sys.stderr)
            if args.push_best_effort:
                print("continuing (--push-best-effort); local commit is intact")
                return 0
            return 6
        dest = f"{args.remote}" + (f"/{args.branch}" if args.branch else "")
        print(f"pushed to {dest}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
