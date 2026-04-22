# BTC Model — Handover Package

**Last updated:** 2026-04-22
**Status:** Canonical **v14** shipped; operational comms runtime (Phases 1-4 of the six-phase infra plan) shipped on top. Model code and weights are unchanged since 2026-04-18.

Four changes from v13 (still current):
(1) wf365 position taper widened from (0.55, 0.70) to **(0.45, 0.80)** per `experiment_taper_sweep.md` — full Sharpe 1.20 → **1.24**, hold-out 1.19 → **1.29**, full MaxDD −38% → **−32%**. Yearly trade: costs in bull years (2024 −9pp return), wins in bear/choppy (2022 +6.7pp return, +5.8pp MaxDD). **Today's position (2026-04-17) changes: 1.00 → 0.77.** sf730 taper unchanged at (0.55, 0.65).
(2) `health_check.py` shipped — MVP drift-detection monitor, 3 parameters, read-only. Run with `python3 health_check.py --out health_check.csv`.
(3) `shadow_tracker.py` shipped — infrastructure for shadow-tracking monitor decision rules (naive, persist-2, persist-3) alongside baseline. Read-only, doesn't touch pipeline state. Run with `python3 shadow_tracker.py --out shadow_state.csv`.
(4) `fed_funds_stress_rank` unpinned in `build_macro_equities.py` — confirmed no-op today (weights bit-identical), prospective safety for future refits if calib AUC crosses 0.5. `test_strong_prior.py` deleted (dead scaffolding for a never-adopted feature).

v14 numbers (wf365, y_60, gross, 2026-04-17 data): Sharpe **1.242 full / 1.287 hold-out**, total +556% full / +30.5% hold-out, MaxDD **−32% full / −18% hold-out**, AUC 0.74 full / 0.89 hold-out. sf730 reference unchanged from v13: Sharpe 1.21 full / 1.76 hold-out.

Today's call (2026-04-17): regime=bear, percentile=0.529, **position 0.77** (was 1.00 under v13). See `refit_report_v14.md` and `experiment_taper_sweep.md` for full rationale.

> **v13 ensemble-layer note (2026-04-18):** NaN renormalization. On wf365 canonical, this exactly preserves hold-out Sharpe and today's call — the rolling-365 percentile window is past the pre-ETF era. Full-window Sharpe +0.051 and AUC +0.023, at the cost of 9pp worse full-window MaxDD (mostly 2022 Q2). `weight_history` is invariant under the fix. Details in refit_report_v13.md.

> **v13 operational-hygiene note (2026-04-18):** four pipeline cleanups. (1) `fix_parsers.py` folded into per-source handlers in `pull_all_raw_data.py` and deleted as a standalone step. (2) Artemis wired in as source #9 of `pull_all_raw_data.py`; `pull_artemis_etf.py` reduced to a thin back-compat wrapper. (3) Standalone `hypothesis_*.csv` files now regenerate from canonical state on every export — drift vs master's embedded columns is 0.00 (was up to 0.34 per v9 audit). (4) `export_manifest.json` + `python3 export_csvs.py --check-coherence` catches the "regenerated weight_history without re-exporting master" failure mode that refit_report_v13.md §7 forensically traced.

> **v14 monitor note (2026-04-18):** `health_check.py` added as the MVP version of open item #7 (automated roster monitor). Read-only. 3 parameters total (`--rolling-window-days` default 365, `--auc-threshold` default 0.50, `--auc-delta-threshold` default 0.15). Reads `master_daily_view_wf365.csv` + the six `hypothesis_*.csv` files; writes `health_check.csv` with per-signal IS/OOS/rolling AUCs and flag status. On v13 canonical, flags 10 of 42 rows: the known-stressed `etf_flows` composite (OOS 0.479) plus a new finding — `sub_fed_funds_stress_rank` at OOS 0.072 (IS 0.591), a pinned macro sub-signal whose hold-out direction has inverted. Does NOT flag classic_cycle pinned-contrarian sub-signals (correctly), does NOT flag macro_equities composite (correctly — IS-OOS delta 0.122 stays below 0.15 threshold by design). `test_strong_prior.py` deleted — tested a `strong_prior` kwarg of `auc_excess_weights` that was never adopted into production. Full rationale in refit_report_v14.md.

> **v14 ops-layer note (2026-04-22):** operational comms runtime shipped on top of v14. Four new services (`daily_pipeline.py`, `audit_log.py`, `api_server.py`, `mcp_server.py`) plus shared loader (`data_access.py`), config (`runtime_config.yaml`), deps (`requirements-ops.txt`), and runbook (`OPERATIONS.md`). **No changes to any model code, weights, or canonical CSVs.** The layer runs `run_all.sh` + `health_check.py` + `shadow_tracker.py` + `audit_log.py` on a cron, serves the committed CSV history via bearer-authed REST, and exposes the same surface as MCP tools for Claude Code agents. The audit log (`paper_trading_log.csv`) starts empty on deploy — no backfill — and gets one commit per day with message `paper trading YYYY-MM-DD: regime=X position=Y.YY pct=Z.ZZZ`. See `OPERATIONS.md` for deployment and the runbook.

---

## 30-second orientation for a fresh Claude instance

1. Model predicts BTC drawdowns using **5 hypothesis groups** (Macro & Equities, CME positioning, Crypto Derivatives, Classic Cycle, ETF Flows). ETH was dropped from the ensemble in v12 — still computed for health-check monitoring but doesn't feed `ensemble_score`.
2. Each hypothesis produces a `[0, 1]` composite score per day where 1 = high drawdown risk.
3. A regime classifier (bull/neutral/bear) determines which hypothesis weights apply on a given day. **v10+: D2h drawdown-from-365d-peak classifier** (hysteresis thresholds −5/−15/−30/−20).
4. Weighted sum → rolling-365 percentile → linear-hybrid position function → daily BTC long position from 0% to 100%.
5. **Canonical fit uses `y_60`** (60-day forward drawdown ≥ 20% as the training label) with per-hypothesis calibration windows, pinned priors where direction is known from prior work, and ensemble-layer NaN renormalization (v13+).
6. Under **v14 canonical** (2026-04-17 data, gross, 5-hypothesis, wider taper): wf365 hold-out AUC 0.886, Sharpe 1.24 full / 1.29 hold-out, total +556% vs B&H +116%, MaxDD −32% full / −18% hold-out. sf730 reference: Sharpe 1.21 full / 1.76 hold-out.

For the full story of how the model got here and why each design decision was made, read `execution_playbook_v4.md` then `refit_report_v14.md` (most recent) and work backward through earlier refit reports for context. For the operational layer, read `OPERATIONS.md`.

---

## Pipeline quickstart (fresh checkout)

One command, top to bottom, under 2 minutes wall-clock:

```bash
bash run_all.sh
```

This installs deps, pulls all 9 data sources (including Artemis), regenerates all 4 canonical variants, and exports the master CSVs + standalone hypothesis CSVs + manifest. Credentials are baked into `pull_all_raw_data.py` (see `credentials.md`); `ARTEMIS_API_KEY` env var takes priority over the baked-in fallback.

Skip flags for reruns:

```bash
bash run_all.sh --skip-pull             # assumes data/raw/ populated
bash run_all.sh --skip-pull --skip-deps # full rerun, code-only changes
```

### Manual step-by-step (if you need to run steps individually)

```bash
# Step 1: deps
pip install --break-system-packages pandas numpy scipy scikit-learn pyarrow \
    yfinance fredapi velodata cot_reports artemis requests

# Step 2: full data pull (~80 seconds, 9 sources including Artemis)
python3 pull_all_raw_data.py --source all --out-dir data/raw

# Step 3: regenerate canonicals (4 variants: wf365/sf730 × y_60/y_30)
bash regenerate_canonicals.sh

# Step 4: export masters + standalone hypothesis CSVs + weights + manifest
python3 export_csvs.py
```

### Which runner to use

- **`run_all.sh`** — cold-start, top-level. Deps → pull → regen → export. Use this by default.
- **`regenerate_canonicals.sh`** — `run_all.sh` minus deps/pull. Regenerates all 4 variants; used when data is already local and only code changed.
- **`run_full_pipeline.py`** — single label, single default variant (foundation + 6 hypotheses + default ensemble run, tagged by `CALIB_LABEL`). Useful when iterating on a single hypothesis builder and you want one quick end-to-end check without the 4-variant overhead. Not called by the other runners.
- **`python3 health_check.py --out health_check.csv`** — post-pipeline drift-detection monitor. Read-only, 3 params (rolling window / AUC threshold / IS-OOS delta threshold). Runs against the committed CSVs in the current directory. Does NOT regenerate anything. Use ad-hoc after a refit, or on a monthly cadence. Flags on v14 canonical: etf_flows composite + sub_fed_funds_stress_rank + 8 others. See refit_report_v14.md §2.
- **`python3 daily_pipeline.py`** — persistent scheduler wrapping all four above (plus `shadow_tracker.py` and `audit_log.py`). For VM deployment, not interactive use. See `OPERATIONS.md`.

### Known pipeline gotchas

- **`data/` subdirs auto-create** in `run_all.sh` and `regenerate_canonicals.sh`. If running a builder script directly, `mkdir -p data/raw data/derived data/hypotheses data/final` first.
- **Hypothesis parquets are label-specific.** `regenerate_canonicals.sh` loops `y_30` then `y_60` (v13 change — previously `y_60` then `y_30`) so the final state of `data/hypotheses/*.parquet` is the canonical y_60 build. If you manually run a hypothesis builder with `CALIB_LABEL=y_30`, the canonical state is corrupted — re-run with `CALIB_LABEL=y_60` or rerun `regenerate_canonicals.sh` to restore.
- **`pull_all_raw_data.py` time estimate was 30–40 min in pre-v13 docs.** Actual is ~80 seconds for all 9 sources. If it takes longer than a few minutes, something's wrong; check `pull_all.log` or re-run with `--verbose`.
- **`export_csvs.py` requires explicit `--label` and `--variant` when used in targeted mode** (v13 change). With no args, it exports both canonical y_60 variants + standalone hypothesis CSVs + weights.csv + manifest in one go.
- **The standalone `hypothesis_*.csv` files are now coherent with master** (v13 change). Prior versions drifted up to 0.34 in score values because they were produced by a different pipeline run than the committed master. This is now enforced by `export_csvs.py` co-generating them with each master export.

### Coherence verification

Two different verification tools for two different situations:

**(a) `python3 export_csvs.py --check-coherence`** — verifies that master, weights, weight_history, and standalone hypothesis CSVs in a pipeline working directory all came from the same run. Reads `export_manifest.json`, re-hashes every source parquet in `data/`, compares. Only meaningful where `data/` exists.

**(b) `sha256sum`** — paste verification for a committed-code directory like `/mnt/project/` where `data/` is intentionally not present. Compare the first 16 hex chars of each file's hash against the checksum list in "Package integrity" below. This is the right tool after promoting v13 artifacts.

Running `--check-coherence` in a directory without `data/` produces a friendly error pointing at `sha256sum` (v13 change; prior version false-alarmed with cascading "source missing" messages).

### Sanity-check targets (v14, wf365 canonical, y_60, data through ≥ 2026-04-17)

| Metric | wf365 target | sf730 target |
|---|---|---|
| Full Sharpe (gross) | 1.24 ± 0.05 | 1.21 ± 0.05 |
| Hold-out Sharpe | 1.29 ± 0.05 | 1.76 ± 0.10 |
| Full total | +556% ± 40pp | +597% ± 40pp |
| Hold-out total | +30.5% ± 5pp | +40% ± 5pp |
| Full MaxDD | −32% ± 3pp | −30% ± 3pp |
| Hold-out MaxDD | −18% ± 3pp | −12% ± 3pp |
| Full AUC | 0.74 ± 0.02 | 0.75 ± 0.02 |
| Hold-out AUC | 0.89 ± 0.02 | 0.89 ± 0.02 |

Note: wf365 changed from v13 canonical due to wider taper (0.45, 0.80) vs v13's (0.55, 0.70). Ensemble score, percentile, weights all identical; position and strategy_return change. sf730 reference is unchanged.

Hypothesis hold-out AUCs (unchanged since v12):

| Hypothesis | Hold-out AUC |
|---|---:|
| macro_equities | 0.631 |
| cme | 0.696 |
| crypto_derivatives | 0.671 |
| classic_cycle | 0.748 |
| etf_flows | 0.479 |

---

## What does the model say today

Open `master_daily_view_wf365.csv`, scroll to the last row:

- `regime` — which weight set applies (bull / neutral / bear)
- `percentile` — rolling-365 percentile of the ensemble score (this is what the position function reads)
- `position` — target long BTC weight (0 to 1)

**Today's call (2026-04-17):** regime = **bear**, percentile = **0.529**, position = **0.77** under v14 wider taper (was 1.00 under v13 narrow taper). sf730 reference unchanged: position 0.49 at percentile 0.601.

Once the ops layer is deployed, the same answer is available via:

```bash
curl -H "Authorization: Bearer $BTC_API_TOKEN" https://<your-api-host>/today
```

or as an MCP tool call (`get_today`) from Claude Code.

**Position function** (wf365 widened in v14; sf730 unchanged):

```
wf365 canonical (v14 deployed):
position(percentile) = 1.0                            if percentile ≤ 0.45
                     = 1.0 − (percentile−0.45)/0.35   if 0.45 < percentile < 0.80
                     = 0.0                            if percentile ≥ 0.80

sf730 reference:
position(percentile) = 1.0                            if percentile ≤ 0.55
                     = 1.0 − (percentile−0.55)/0.10   if 0.55 < percentile < 0.65
                     = 0.0                            if percentile ≥ 0.65
```

Thresholds are set via env vars `POSITION_LONG_THR` and `POSITION_DEF_THR` in `regenerate_canonicals.sh`; see `thresholds.csv`.

### What's driving the current call

On the same row, look at the **5 hypothesis score columns** that feed the ensemble (`macro_equities_score`, `cme_score`, `crypto_derivatives_score`, `classic_cycle_score`, `etf_flows_score`). The `eth_score` column is embedded but reference-only — it does not feed `ensemble_score`.

As of v13, the standalone `hypothesis_*.csv` files are coherent with master's embedded columns (max drift 0.00), so either source is valid. The standalone files add sub-signal columns useful for attribution.

---

## What's in this folder

**Everything is flat. No subdirectories. No parquet files committed — re-pullable via `run_all.sh`.**

**Documentation (read in order)**
- `HANDOVER.md` — this file, orientation
- `OPERATIONS.md` — **new in v14 ops.** Runbook for the four operational services (scheduler, audit log, REST API, MCP server). Read this before deploying to the VM.
- `execution_playbook_v4.md` — model spec, hypothesis definitions, runbook (still current; v13 updates §8.1 convention reference)
- `refit_report_v14.md` — **most recent** model-session's work (wider taper, health_check MVP, shadow_tracker, fed_funds unpinned)
- `refit_report_v13.md` — NaN renormalization at ensemble layer, operational-hygiene pass, coherence manifest, forensic finding on committed-master provenance
- `refit_report_v12.md` — ETH hypothesis removed from ensemble
- `refit_report_v11.md` — cost model removal + data refresh through 2026-04-17
- `refit_report_v10.md` — D2h regime classifier, fresh Coinglass premium, ETF Flows V4 hybrid
- `refit_report_v9.md` — reproducibility audit, sensitivity characterization
- `refit_report_v8.md` — position threshold recalibration
- Historical refit reports preserved: `refit_report.md`, `refit_report_v3.md` through `refit_report_v7.md`. Read only if context needed.

**Data files**
- `master_daily_view_wf365.csv` — **THE canonical** daily state (4,231 rows). One row per day: regime, labels, 6 hypothesis scores, ensemble_score, percentile, position, btc_return, strategy_return. The one file to open to inspect the model. Embedded `<hyp>_score` columns are the authoritative hypothesis-layer inputs.
- `master_daily_view_sf730.csv` — reference single-fit variant with rolling-730d percentile. Same schema.
- `weights.csv` — regime×hypothesis weight matrix per variant×label (12 rows).
- `weight_history_wf365_y_{60,30}.csv` — monthly walk-forward refit history.
- `thresholds.csv` — per-variant position thresholds. wf365: (0.45, 0.80); sf730: (0.55, 0.65).
- `hypothesis_*.csv` — per-hypothesis score + sub-signal ranks. **v13: coherent with master's embedded columns** (regenerated with each master export; drift max 0.00 — prior versions drifted up to 0.34). Safe for sub-signal attribution, pinning audits, historical per-hypothesis analysis.
- `export_manifest.json` — **new in v13.** SHA-256 fingerprint tying master + weights + weight_history + standalone hypothesis CSVs to the exact pipeline run that produced them. Checked by `python3 export_csvs.py --check-coherence`.
- `pinning_audit_findings.csv`, `data_inventory.csv`, `raw_data_export.csv` — reference data.
- `grid_sensitivity.csv` — v9 artifact, 24-point (long_thr × def_thr) grid for future threshold analysis.
- `health_check.csv` — **new in v14.** Per-signal IS / OOS / rolling AUC report produced by `python3 health_check.py`. 42 rows (6 composites + 36 sub-signals). Regenerate any time; not used by the pipeline. Current-canonical flags: 10 of 42. Regenerates on every daily run under the ops layer.
- `shadow_state.csv` — **new in v14.** Counterfactual positions under alt decision rules. Regenerates on every daily run under the ops layer.
- `paper_trading_log.csv` — **new in v14 ops.** Git-committed daily record of model calls. Generated on first VM deploy, **never backfilled**. Grows by one row + one commit per day. Format: date, regime, ensemble_score, percentile, position, five hypothesis scores, eth_score, btc_return, strategy_return.

**Model Python source**
- `common.py` — shared utilities. **v13:** `composite_score_renorm` added (v13+ preferred for ensemble-layer code). `composite_score_no_renorm` retained with updated docstring for backward compat with `build_nnls_diagnostic.py`.
- `build_foundation.py` — D2h regime classifier (v10; unchanged since).
- `build_macro_equities.py`, `build_cme.py`, `build_crypto_derivatives.py`, `build_classic_cycle.py` — unchanged since v9 (v14: fed_funds unpinned in build_macro_equities.py).
- `build_etf_flows.py` — V4 hybrid (v10; Artemis + Coinglass). Requires `data/raw/artemis_etf/btc.parquet`, fails loud if missing.
- `build_eth.py` — reference-only since v12. Runs and produces `hypothesis_eth.csv` for per-hypothesis health-check monitoring. NOT included in ensemble.
- `build_robust.py` — canonical ensemble (AUC-excess weighting per regime) with walk-forward monthly refits. **v13:** inlined `row_ensemble` now renormalizes active weights on NaN. **v14:** default taper thresholds (0.45, 0.80). Full walk-forward machinery unchanged.
- `build_nnls_diagnostic.py` — diagnostic NNLS variant; same env-var pattern.
- `pull_all_raw_data.py` — **v13: 9 sources.** Credentials baked in. Parser fixes for bubble_index / bmo / etf_flow_history / etf_premium_discount now happen inside their source handlers (previously a separate `fix_parsers.py` step). Artemis added as source #9.
- `pull_artemis_etf.py` — **v13: thin back-compat wrapper** (~55 lines). Delegates to `pull_all_raw_data.pull_artemis_etf`. Older call sites still work.
- `regenerate_canonicals.sh` — **v14:** wf365 thresholds updated to 0.45/0.80.
- `run_all.sh` — cold-start top-level pipeline. 3 steps after deps: pull, regen, export.
- `export_csvs.py` — required `--label`/`--variant` CLI args (or no args = default both canonical y_60 variants); regenerates standalone hypothesis CSVs; writes `export_manifest.json`; has `--check-coherence` subcommand.
- `run_full_pipeline.py` — single-label narrow runner. Builds foundation + 6 hypotheses + one default ensemble. Not called by `run_all.sh` or `regenerate_canonicals.sh`; used for iterative hypothesis-builder debugging.
- `make_daily_chart.py` — daily comms chart prototype. Not yet wired into the ops layer; integration is Phase 5 scope.
- `health_check.py` — MVP drift-detection monitor. Reads the committed `master_daily_view_wf365.csv` + `hypothesis_*.csv`, computes per-signal IS/OOS/rolling AUCs against `y_60`, writes `health_check.csv`. Read-only. 3 parameters.
- `shadow_tracker.py` — shadow-tracking counterfactual logger. Read-only. Writes `shadow_state.csv`.
- `calibration_test.py` — session-7 failed calibration artifact (retained for v8 retry plan).

**Operational layer (new in v14 ops, 2026-04-22)**
- `daily_pipeline.py` — APScheduler-based persistent runner. Chains `run_all.sh` → `health_check.py` → `shadow_tracker.py` → `audit_log.py` on a configurable cron (default 22:00 UTC daily). Step timeouts, rotating logs with 14-day retention, SIGTERM-clean shutdown. `--once` for a one-shot, `--dry-run` to print the plan. No model knobs — all parameters are operational.
- `runtime_config.yaml` — config for the scheduler. Cron expression, project dir, log paths, step list. **Will diverge from repo version once deployed** — treat the committed copy as a template.
- `audit_log.py` — appends today's row from `master_daily_view_wf365.csv` to `paper_trading_log.csv` and commits with the structured message `paper trading YYYY-MM-DD: regime=X position=Y.YY pct=Z.ZZZ`. Idempotent (no-op on same-day rerun). Optional `--push` / `--push-best-effort` flags for remote replication.
- `data_access.py` — shared pure-function loader over the committed CSV artifacts. Mtime-keyed cache picks up fresh pipeline outputs without restart. Used by both API and MCP servers.
- `api_server.py` — FastAPI REST server with bearer auth. 17 read-only endpoints over the full CSV history: `/today`, `/history`, `/hypothesis/{name}`, `/hypotheses`, `/weights`, `/weight_history`, `/health`, `/flags`, `/health_history`, `/shadow`, `/raw`, `/raw/columns`, `/manifest`, `/thresholds`, `/data_inventory`, `/pinning_audit`, `/status`. Full history visible on deploy — no backfill.
- `mcp_server.py` — MCP Streamable HTTP server wrapping the same 17-endpoint surface as tools for Claude Code agents. Same bearer via Starlette middleware. Default port 8788, mount `/mcp`.
- `requirements-ops.txt` — additive deps (apscheduler, fastapi, uvicorn, pydantic, pyyaml, mcp[cli]). Project's own deps are handled by `run_all.sh`.
- `.env.ops` — **gitignored.** Holds `BTC_API_TOKEN`, `BTC_MODEL_DIR`, optional host/port overrides. See `OPERATIONS.md` for the template.

**v9 analysis scripts (reusable):**
- `grid_sensitivity.py`, `full_comparison.py`, `chart_positions_v2.py`.

**Removed in v14**
- `test_strong_prior.py` — deleted. Tested a `strong_prior` kwarg of `auc_excess_weights` that was never adopted into production (crashed with TypeError against current common.py). See refit_report_v14.md §1.

**Removed in v13**
- `fix_parsers.py` — deleted. Logic folded into per-source handlers in `pull_all_raw_data.py`.

---

## Sensitivity to input drift (v9 finding, holds through v13)

Small drift in hypothesis-score inputs produces disproportionately large drift in realized strategy returns. The amplification comes from the rolling-365 percentile transform: small score shifts that preserve global rank (and therefore AUC) can substantially change within-window ranks, pushing percentiles across the (0.55, 0.70) taper-zone boundaries.

**v10 reinforcement.** The fresh-premium step moved hypothesis-level hold-out AUC by only −0.010 but moved wf365 ensemble hold-out Sharpe by −0.07 (on sf730 exactly 0.000 — same data, different percentile window). The V4 step moved hypothesis AUC −0.049 while moving sf730 hold-out Sharpe +0.40. Every refit session reproduces the pattern.

**v13 related finding.** The NaN renormalization produced exactly 0.000 Sharpe change on wf365 hold-out because the rolling-365 window at 2025-04-17 doesn't reach pre-ETF dates — but moved sf730 hold-out by +0.077 because rolling-730 does cross the boundary. Same data, same fix, window-dependent outcome. See refit_report_v13.md §3.

### Resolved — structural fix for rolling-percentile amplification (v14)

v9 finding reproduced through v13. In v10, four structural candidates were tested and rejected because they dampened binary position behavior that the hold-out specifically rewarded at the time. In v14, the taper was revisited with extended data and multiple taper widths: widening from (0.55, 0.70) to (0.45, 0.80) improved full-window Sharpe by +0.04, hold-out Sharpe by +0.10, and full MaxDD by 6.2pp. Trade-off is a modest cost in bull-year upside capture (2024 −9pp return) for meaningful bear/choppy-year protection (2022 +6.7pp return, +5.8pp MaxDD). **Shipped in v14.** See `experiment_taper_sweep.md` for full evidence.

---

## Known open items (in priority order)

1. **Promote v14 + ops layer to the project repo.** v14 model files were shipped on 2026-04-18. Ops-layer files shipped on 2026-04-22: add `daily_pipeline.py`, `runtime_config.yaml`, `audit_log.py`, `data_access.py`, `api_server.py`, `mcp_server.py`, `requirements-ops.txt`, `OPERATIONS.md`. Update `.gitignore` to exclude `.env.ops`, `logs/`, `health_check.csv`, `shadow_state.csv` (but NOT `paper_trading_log.csv`). Verify via `sha256sum <file> | cut -c1-16` against "Package integrity" below.

2. **Cardinal calibration retry.** Session 7's attempt failed OOS. Session 8 plan (`refit_report_v7.md` §3) has three architectural changes: rolling-window calibration, Huber regression for magnitude, and a validation gate. If it passes, the daily message gains `P(DD≥20% in 60d)` and a magnitude band. If it fails, ship the contextualized-percentile fallback.

3. **Operational comms runtime.** Phases 1-4 of the six-phase plan shipped in the 2026-04-22 ops session. `daily_pipeline.py` (APScheduler; chains `run_all.sh`, `health_check.py`, `shadow_tracker.py`, `audit_log.py` on a configurable cron; rotating logs, step timeouts). `audit_log.py` (appends today's row to git-tracked `paper_trading_log.csv`; structured commit messages; idempotent; starts empty on deploy — no backfill; optional `--push-best-effort` for remote replication). `api_server.py` (FastAPI REST with bearer auth, 17 read-only endpoints exposing the full CSV history from day 1). `mcp_server.py` (FastMCP over Streamable HTTP with the same bearer and same 17-tool surface for Claude Code agents). Shared loader in `data_access.py`. Runbook in `OPERATIONS.md`. **Remaining:** Phase 5 (Telegram alerts + trigger evaluator + dedup state, per `refit_report_v7.md` §4), and integration of `make_daily_chart.py` into the daily distribution. Not blockers for using the stack today.

4. **Operational hygiene (remaining).** v13 resolved the four pipeline-coherence items. v14 resolved `test_strong_prior.py` (deleted) and the taper-amplification deferral. Remaining: consider whether `run_full_pipeline.py` overlap with `run_all.sh`+`regenerate_canonicals.sh` is worth consolidating (current verdict: keep both — `run_full_pipeline.py` serves the "single-hypothesis-builder iteration" use case).

5. `OPERATIONS.md` — daily/weekly rhythm, how to interpret outputs, override procedures, escalation criteria. **Ops-layer scaffold shipped 2026-04-22** covers deploy + runbook for the four services. Still pending: the discretionary runbook (daily reading routine, when to override, escalation criteria) — that's a comms-and-process doc, not an infra doc.

6. **Annual health-check script.** **Signal-health piece partially subsumed in v14 by `health_check.py`** — per-hypothesis composite AUC monitoring, sub-signal AUC monitoring, ETF Flows hold-out AUC stability (v10 addition), ETH hypothesis AUC monitoring (v12 addition) all now covered. Still pending inside this item (not in `health_check.py` scope): forward-DD rate conditional on percentile, wf365 weight-change norm, FRED-revision check on macro sub-signals (T10Y2Y, DFF, BAMLH0A0HYM2 are the known-drifting ones), rolling Sharpe. Expect "no change warranted" most years. **Resolved in v13:** standalone-vs-master self-consistency check, by co-generating hypothesis CSVs with each master export; keep the check as belt-and-suspenders. **v10 ETF Flows AUC alert band:** 0.33–0.45 (now covered by `health_check.py`'s 0.50 threshold). **v12 ETH AUC re-add trigger:** if rolling 365-day hold-out AUC stays >0.6 across 3+ future quarters in non-rotation periods, reconsider re-adding ETH (now automatically checked on each `health_check.py` run).

7. **Automated roster monitor.** **MVP shipped in v14** as `health_check.py` — read-only drift-detection reporting with 3 parameters (rolling window / AUC threshold / IS-OOS delta threshold). Flags etf_flows composite (OOS 0.479), sub_fed_funds_stress_rank (OOS 0.072 — surfaced by monitor), and 8 other sub-signals. Does NOT mutate pinning sets. Shadow tracker shipped alongside (`shadow_tracker.py` + `shadow_state.csv`) — logs counterfactual positions for naive, persist-2, and persist-3 decision rules at production + wide tapers. **OOS rule-selection test (40-month window) decisively rejected automation**: no rule beat baseline on train; forced train-winner lost 0.60 Sharpe on test. See `oos_rule_selection_memo.md`. Revisit monitor auto-mutation only after ≥ 2 quarters of forward shadow data.

8. **`sub_fed_funds_stress_rank` hold-out inversion (surfaced by v14 monitor).** IS AUC 0.591, hold-out AUC **0.072** against y_60. Unpinned in v14 as dead-prior cleanup; no-op today (calib AUC 0.5054 is above 0.5, auto-flip doesn't trigger either way) but prospective safety if it crosses below 0.5 in a future refit. Action: track across the next 2–3 monthly refits. If it stays below 0.5 for 3+ consecutive refits in calibration (not just hold-out), candidate for removal.

9. **Shadow-track decision rules for 2+ quarters.** Run `python3 shadow_tracker.py` monthly (or daily under the ops-layer scheduler); logs what `naive`, `oos_persist2`, and `oos_persist3` would have decided. Re-run the OOS rule-selection protocol (`oos_rule_selection_memo.md`) after Q3 2026 or later with the extended data. If persist-2 or persist-3 still shows meaningful edge under honest OOS evaluation at that point, there's evidence to consider promotion. Until then, report-only.

10. **Static sub-signal weights — structural question.** `auc_excess_weights` fits once per calibration window and never refits. Attempted fixes in v14 session: (a) MIN_CALIB tightening on macro — rejected, costs 0.24 hold-out Sharpe due to accidental dilution-insurance. (b) Shrinkage toward uniform — rejected, produces Sharpe gains while composite AUC *decreases*, indicating the gain comes from rolling-percentile interaction rather than better prediction (see `experiment_shrinkage.md`). (c) Walk-forward sub-signal refit — sketched for macro, hold-out AUC slightly worse than static, consistent with v7 failure pattern. Not a free fix. Future options: annual sub-signal refit, calibrated probabilistic model replacing the AUC-excess + percentile + taper stack. Both are multi-session projects. Revisit only after v14 taper shipped and shadow-tracking data accumulates.

### Explicit non-goals

- V2 architecture rebuild (NNLS + new hypotheses).
- More hypotheses (H7, etc.) unless a health-check signal flags a gap.
- Re-tuning position thresholds again after v14's taper change, for ~2–3 years or until a health-check trigger fires.

---

## To rebuild from scratch

```bash
bash run_all.sh
```

That's it. See "Pipeline quickstart" above for flag variants and individual-step usage.

Expected results (wf365 canonical, v14, y_60, gross):
- Full window (2021-06-30 →): Sharpe 1.24
- Hold-out year (2025-04-17 →): Sharpe 1.29

See "Sanity-check targets" above for the full table.

For the operational layer (scheduler, audit log, REST API, MCP server), see `OPERATIONS.md`.

---

## Provenance note

The v14 canonical state was produced by running the v14 pipeline code on a data snapshot pulled 2026-04-18. Raw data is NOT included in this handover — re-pullable via `run_all.sh`. If you re-pull and re-run, expect numbers to drift slightly as more days get added to the backtest and the last N days shift into the hold-out, plus small revisions in FRED and CFTC historical series.

**Important:** the committed `master_daily_view_wf365.csv` from the pre-v13 era did NOT exactly match a fresh v12 pipeline rerun on 2026-04-18 data — ensemble_score max diff 0.049, Sharpe 1.086 vs fresh-v12 1.150. This looks like a combination of data revisions and a probable mixed snapshot where v12 code was applied on an earlier raw-data pull. Treat pre-v13 committed masters as historical, not bit-exact baselines. See refit_report_v13.md §7 for forensics. **The v13 coherence manifest (`export_manifest.json` + `--check-coherence`) is the procedural safeguard against this class of issue in future sessions.**

---

## Package integrity

SHA-256 first-16-hex-char prefixes for paste verification. Run `sha256sum <file> | cut -c1-16` against any file in `/mnt/project/` after promotion:

```
fd9f713fe6e457ae  master_daily_view_wf365.csv       (v14: regenerated under wide taper)
7800768e2f75c5e5  master_daily_view_sf730.csv
63fefeeacb2c3f65  weights.csv
780977eaf0b9cd76  weight_history_wf365_y_60.csv
9b7d8530fe397e55  weight_history_wf365_y_30.csv
ce5a03c748b10d4d  hypothesis_macro_equities.csv
88ae154971c9b0e8  hypothesis_cme.csv
ce022146f77a999b  hypothesis_crypto_derivatives.csv
87fd5b00f6394ff4  hypothesis_classic_cycle.csv
13a16813264a91a8  hypothesis_etf_flows.csv
b39e5bb81bd8087d  hypothesis_eth.csv
7995927f13dd17a6  export_manifest.json
02d1dd321d698ff5  build_robust.py                   (v14: default taper thresholds 0.45/0.80)
6b3ac3bd71183e6d  build_macro_equities.py           (v14: fed_funds unpinned)
ef0c075646299a1d  common.py
deb6a9591a273730  export_csvs.py
abf04ad2fcbfbaa7  pull_all_raw_data.py
bb3517c6c52f3c14  pull_artemis_etf.py
4d92d97256133e57  regenerate_canonicals.sh          (v14: wf365 thresholds 0.45/0.80)
812401f638474cba  thresholds.csv                    (v14: wf365 row updated)
5c9e6213cffa9d91  run_all.sh
20d44d96af47e0ea  health_check.py                   (new in v14)
bab3fc4d7cfcca70  shadow_tracker.py                 (new in v14)
15a0b08f62642df2  refit_report_v13.md
cfdd5d0710ae8a3b  refit_report_v14.md               (new in v14)
0f63d51f87081620  daily_pipeline.py                 (new in v14 ops)
f46f9639401d3454  runtime_config.yaml               (new in v14 ops; will diverge on VM)
ded2f265faf72844  audit_log.py                      (new in v14 ops)
62a473d5276b5f0a  data_access.py                    (new in v14 ops)
38011c72195b91f8  api_server.py                     (new in v14 ops)
405f16bda3e7e634  mcp_server.py                     (new in v14 ops)
fa9fabc8b1c1ca93  requirements-ops.txt              (new in v14 ops)
ec32a5ecb7c17605  OPERATIONS.md                     (new in v14 ops)
```

Note: `export_manifest.json` hash shifts whenever you regenerate (timestamp inside). `health_check.csv`, `shadow_state.csv`, `paper_trading_log.csv`, and `logs/pipeline.log` regenerate on each run and aren't listed here. `HANDOVER.md` re-hash as needed after edits. `runtime_config.yaml` is expected to diverge on the VM if you change the cron or paths — the hash above is the template as shipped. The other files are stable across reruns as long as pipeline inputs are unchanged.

Run this after setup and save the output somewhere. If files change unexpectedly later, you'll know.
