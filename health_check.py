"""MVP drift-detection monitor for BTC model sub-signals and hypothesis composites.

Reports per-signal AUCs (in-sample, hold-out, rolling) against y_60 and flags
cases where:
  (a) the hold-out or rolling-window AUC has fallen to the floor convention
      (AUC <= --auc-threshold), OR
  (b) the AUC has degraded meaningfully from in-sample to out-of-sample
      (is_auc - oos_auc > --auc-delta-threshold).

Read-only: this script does not mutate any pinning or strong-prior sets. It
reads canonical artifacts, reports, exits. Promotion from "reported" to
"auto-applied" is a separate future decision.

Coverage:
  - 5 canonical hypothesis composites (macro_equities, cme, crypto_derivatives,
    classic_cycle, etf_flows) read from master_daily_view_wf365.csv
  - ETH hypothesis composite (reference-only since v12) read from
    master_daily_view_wf365.csv's eth_score column. The v12 open item #6
    explicitly asked for ETH monitoring; this MVP subsumes that.
  - All sub-signals from hypothesis_*.csv (post-flip oriented values —
    i.e. the same values the production composite consumes).

AUC convention:
  - Labels come from master_daily_view's y_60 column (60-day forward drawdown
    ≥ 20%).
  - in-sample window:   [2021-06-30, hold-out_start)
  - hold-out window:    [last_date - 365d, last_date]  (matches canonical hold-out)
  - rolling window:     [last_date - ROLLING_WINDOW_DAYS, last_date]
        In the default (ROLLING_WINDOW_DAYS = 365), hold-out and rolling are
        identical. Pass a shorter window to get a faster drift view (with the
        expected loss of statistical power).

Parameters (3 total; matches the MVP budget in NEXT_SESSION_PLAN.md):
  --rolling-window-days   default 365  (= canonical hold-out length)
  --auc-threshold         default 0.50 (= floor convention in auc_excess_weights)
  --auc-delta-threshold   default 0.15 (~2-sigma noise floor at ~110 OOS positives;
                                        calibrated a priori to avoid flagging
                                        known-working signals such as macro_equities
                                        composite while still catching severe drift.)

Run:
    python3 health_check.py --out health_check.csv
    python3 health_check.py --out health_check.csv --rolling-window-days 90
    python3 health_check.py --help
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

# Canonical conventions (these are NOT tunable monitor parameters; they
# mirror the main pipeline).
FIT_START = pd.Timestamp("2021-06-30")
CANONICAL_HOLDOUT_DAYS = 365
LABEL = "y_60"

# The 5 canonical hypotheses + ETH (reference-only since v12).
HYPOTHESES = [
    "macro_equities",
    "cme",
    "crypto_derivatives",
    "classic_cycle",
    "etf_flows",
    "eth",
]


def compute_auc(y: pd.Series, s: pd.Series) -> tuple[float, int]:
    """Return (AUC, n) after dropping NaN rows. NaN AUC if fewer than 50 rows
    or only one class, matching common.compute_auc's behavior."""
    df = pd.DataFrame({"y": y, "s": s}).dropna()
    n = len(df)
    if n < 50 or df["y"].nunique() < 2:
        return float("nan"), n
    try:
        return float(roc_auc_score(df["y"], df["s"])), n
    except Exception:
        return float("nan"), n


def auc_three_windows(
    df: pd.DataFrame,
    value_col: str,
    label_col: str,
    hold_start: pd.Timestamp,
    rolling_start: pd.Timestamp,
    date_col: str = "date",
) -> dict:
    """Compute is/oos/rolling AUCs and sample sizes for one series."""
    d = df[[date_col, label_col, value_col]].copy()
    d = d[d[label_col].notna() & d[value_col].notna()]
    in_sample = d[(d[date_col] >= FIT_START) & (d[date_col] < hold_start)]
    hold_out = d[d[date_col] >= hold_start]
    rolling = d[d[date_col] >= rolling_start]
    is_auc, n_is = compute_auc(in_sample[label_col], in_sample[value_col])
    oos_auc, n_oos = compute_auc(hold_out[label_col], hold_out[value_col])
    roll_auc, n_roll = compute_auc(rolling[label_col], rolling[value_col])
    return {
        "is_auc": is_auc,
        "oos_auc": oos_auc,
        "rolling_auc": roll_auc,
        "n_is": n_is,
        "n_oos": n_oos,
        "n_rolling": n_roll,
    }


def flag_and_reason(
    is_auc: float,
    oos_auc: float,
    rolling_auc: float,
    auc_threshold: float,
    auc_delta_threshold: float,
) -> tuple[bool, str]:
    """Apply the two MVP flagging rules and return (flagged, reason)."""
    reasons = []

    if not np.isnan(oos_auc) and oos_auc <= auc_threshold:
        reasons.append(f"OOS AUC {oos_auc:.3f} <= threshold {auc_threshold:.2f}")
    if not np.isnan(rolling_auc) and rolling_auc <= auc_threshold:
        # Only add this if it's not already implied by the OOS clause
        # (to avoid double-listing in the default window=365 case).
        if np.isnan(oos_auc) or abs(rolling_auc - oos_auc) > 1e-6:
            reasons.append(
                f"rolling AUC {rolling_auc:.3f} <= threshold {auc_threshold:.2f}"
            )

    if (
        not np.isnan(is_auc)
        and not np.isnan(oos_auc)
        and (is_auc - oos_auc) > auc_delta_threshold
    ):
        reasons.append(
            f"IS-OOS delta {is_auc - oos_auc:+.3f} > {auc_delta_threshold:.2f}"
        )

    if reasons:
        return True, "; ".join(reasons)
    return False, "ok"


def load_labels(master_path: Path) -> pd.DataFrame:
    """Read y_60 labels and all hypothesis composite scores from master."""
    m = pd.read_csv(master_path)
    m["date"] = pd.to_datetime(m["date"])
    return m


def load_sub_signals(project_dir: Path, hyp: str) -> pd.DataFrame | None:
    """Load hypothesis_<hyp>.csv. Returns None if missing."""
    path = project_dir / f"hypothesis_{hyp}.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df


def build_rows(
    project_dir: Path,
    master: pd.DataFrame,
    hold_start: pd.Timestamp,
    rolling_start: pd.Timestamp,
    auc_threshold: float,
    auc_delta_threshold: float,
) -> list[dict]:
    """Walk hypotheses + sub-signals and produce one row per signal."""
    rows = []
    labels_frame = master[["date", LABEL]].copy()

    for hyp in HYPOTHESES:
        # --- hypothesis composite ---
        composite_col = f"{hyp}_score"
        if composite_col in master.columns:
            stats = auc_three_windows(
                master, composite_col, LABEL, hold_start, rolling_start
            )
            flagged, reason = flag_and_reason(
                stats["is_auc"],
                stats["oos_auc"],
                stats["rolling_auc"],
                auc_threshold,
                auc_delta_threshold,
            )
            pinned_tag = "composite"  # composites are not pinned; pinning is sub-signal-level
            rows.append({
                "level": "composite",
                "hypothesis": hyp,
                "signal": f"{hyp}_score",
                "pinned_at_subsignal_level": pinned_tag,
                **stats,
                "delta_is_oos": stats["is_auc"] - stats["oos_auc"],
                "flagged": flagged,
                "reason": reason,
            })

        # --- sub-signals for this hypothesis ---
        subdf = load_sub_signals(project_dir, hyp)
        if subdf is None:
            # No standalone file (shouldn't happen for production hypotheses, but don't crash).
            continue

        merged = subdf.merge(labels_frame, on="date", how="inner")
        sub_cols = [c for c in subdf.columns if c.startswith("sub_")]

        # Determine which sub-signals are pinned in production code.
        # This is diagnostic metadata only — the monitor treats pinned and
        # unpinned sub-signals identically for flagging. We surface the
        # pinned/unpinned tag so a human reviewing the CSV can tell whether
        # a below-0.5 AUC is the specifically-bad "pinned direction broke"
        # case, vs the auto-flip-logic-regret case for unpinned signals.
        pinned_subs = _pinned_subs_for_hypothesis(hyp)

        for c in sub_cols:
            stats = auc_three_windows(merged, c, LABEL, hold_start, rolling_start)
            flagged, reason = flag_and_reason(
                stats["is_auc"],
                stats["oos_auc"],
                stats["rolling_auc"],
                auc_threshold,
                auc_delta_threshold,
            )
            is_pinned = _is_pinned(c, pinned_subs)
            rows.append({
                "level": "sub_signal",
                "hypothesis": hyp,
                "signal": c,
                "pinned_at_subsignal_level": str(is_pinned),
                **stats,
                "delta_is_oos": stats["is_auc"] - stats["oos_auc"],
                "flagged": flagged,
                "reason": reason,
            })

    return rows


# --- Pinned-signal lookup (diagnostic only) ----------------------------------
#
# These sets are read-only metadata for the monitor. They mirror the
# PINNED_DIRECTION / PINNED_FLIPS / no_flip sets in the hypothesis builders
# as of v13. If the builders change, these lists should be updated — but
# keeping them local here avoids any coupling that would let a monitor change
# affect production.

_PINNED_SUBS_BY_HYP = {
    "macro_equities": {
        # build_macro_equities.py PINNED_DIRECTION
        "sub_spx_overext_rank",
        "sub_vix_z90_rank",
        "sub_fed_funds_stress_rank",
        "sub_hy_spread_roc_rank",
    },
    "crypto_derivatives": {
        # build_crypto_derivatives.py no_flip set — extract from source.
        # Included as comment in case of source drift. Currently pins:
        "sub_funding_zscore_rank",
        "sub_lev_stress_rank",
        "sub_rv21_zscore_rank",
    },
    "classic_cycle": {
        # build_classic_cycle.py PINNED_FLIPS_ALL keys — all four orient-pinned.
        "sub_golden_ratio",
        "sub_bmo",
        "sub_ahr999",
        "sub_fear_greed",
    },
    # cme, etf_flows, eth: no production-level pinning (auc_excess_weights
    # called without no_flip=).
}


def _pinned_subs_for_hypothesis(hyp: str) -> set[str]:
    return _PINNED_SUBS_BY_HYP.get(hyp, set())


def _is_pinned(sub_col: str, pinned_subs: set[str]) -> bool:
    return sub_col in pinned_subs
# -----------------------------------------------------------------------------


def run(
    project_dir: Path,
    out_path: Path,
    rolling_window_days: int,
    auc_threshold: float,
    auc_delta_threshold: float,
) -> int:
    master_path = project_dir / "master_daily_view_wf365.csv"
    if not master_path.exists():
        print(f"ERROR: {master_path} not found.", file=sys.stderr)
        return 2

    master = load_labels(master_path)
    last_date = master["date"].max()
    hold_start = last_date - pd.Timedelta(days=CANONICAL_HOLDOUT_DAYS)
    rolling_start = last_date - pd.Timedelta(days=rolling_window_days)

    print(
        f"health_check: data through {last_date.date()}  "
        f"hold-out start {hold_start.date()}  "
        f"rolling start {rolling_start.date()}  "
        f"(thr={auc_threshold:.2f}, delta={auc_delta_threshold:.2f})"
    )

    rows = build_rows(
        project_dir,
        master,
        hold_start,
        rolling_start,
        auc_threshold,
        auc_delta_threshold,
    )

    df = pd.DataFrame(rows)
    # Order columns for readability
    col_order = [
        "level", "hypothesis", "signal", "pinned_at_subsignal_level",
        "is_auc", "oos_auc", "rolling_auc", "delta_is_oos",
        "n_is", "n_oos", "n_rolling",
        "flagged", "reason",
    ]
    df = df[col_order]
    df.to_csv(out_path, index=False, float_format="%.4f")

    # Console summary
    flagged_rows = df[df["flagged"]]
    print(f"\nWrote {len(df)} rows to {out_path}")
    print(f"Flagged: {len(flagged_rows)} of {len(df)}")
    if len(flagged_rows) > 0:
        print("\nFlagged signals:")
        for _, r in flagged_rows.iterrows():
            print(
                f"  [{r['level']:9s}] {r['hypothesis']:20s} {r['signal']:35s} "
                f"IS {r['is_auc']:.3f}  OOS {r['oos_auc']:.3f}  "
                f"pinned={r['pinned_at_subsignal_level']:5s}  "
                f"reason: {r['reason']}"
            )

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "MVP drift-detection monitor for BTC model sub-signals and "
            "hypothesis composites. Read-only; reports AUC health per signal."
        )
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("health_check.csv"),
        help="Output CSV path (default: health_check.csv)",
    )
    ap.add_argument(
        "--project-dir",
        type=Path,
        default=Path(__file__).parent,
        help=(
            "Directory containing master_daily_view_wf365.csv and "
            "hypothesis_*.csv. Defaults to this script's directory."
        ),
    )
    ap.add_argument(
        "--rolling-window-days",
        type=int,
        default=365,
        help=(
            "Trailing window (days) used for the rolling_auc column. "
            "Default 365 matches the canonical hold-out window."
        ),
    )
    ap.add_argument(
        "--auc-threshold",
        type=float,
        default=0.50,
        help=(
            "Flag if hold-out OR rolling AUC <= this value. Default 0.50 "
            "matches the floor convention in auc_excess_weights."
        ),
    )
    ap.add_argument(
        "--auc-delta-threshold",
        type=float,
        default=0.15,
        help=(
            "Flag if (in-sample AUC - hold-out AUC) > this value. "
            "Default 0.15 was calibrated a priori on v13 canonical to "
            "avoid flagging known-working signals (macro_equities "
            "composite, IS-OOS delta 0.122) while still catching severe "
            "degradation."
        ),
    )
    args = ap.parse_args()

    return run(
        args.project_dir,
        args.out,
        args.rolling_window_days,
        args.auc_threshold,
        args.auc_delta_threshold,
    )


if __name__ == "__main__":
    sys.exit(main())
