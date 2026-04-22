# v10 Deployment Package

**Session date:** 2026-04-17
**Contents:** Finished files to move the BTC model from v8/v9 canonical → v10 canonical (D2h regime classifier + ETF Flows V4 hybrid + fresh Coinglass premium).

All files are drop-in replacements or adds — no patches to apply. Raw data parquets are not included (project knowledge can't store them); re-pull via the scripts in Step 4.

---

## Package contents

**Code (4 files)**

| File | Action | Purpose |
|---|---|---|
| `build_foundation.py` | **REPLACE** | D2h drawdown-from-365d-peak regime classifier with hysteresis (−5/−15/−30/−20). Replaces 200d smoothed momentum. |
| `build_etf_flows.py` | **REPLACE** | V4 hybrid builder: Artemis flows + volume, Coinglass premium, real volume share replacing `\|flow\|/btc_close` proxy. |
| `fix_parsers.py` | **REPLACE** | ETF premium parser updated to compute premium from `(market_price_usd − nav_usd) / nav_usd` (Coinglass response schema changed when the endpoint was fixed). |
| `pull_artemis_etf.py` | **ADD** (new) | Artemis SDK-based pull for ETF_FLOWS and ETF_SPOT_VOLUME. Uses `ARTEMIS_API_KEY` env var. |

**Canonical master (1 file)**

| File | Action | Purpose |
|---|---|---|
| `master_daily_view_wf365_v10.csv` | **REPLACE** `master_daily_view_wf365.csv` | New canonical master, 4,228 rows ending 2026-04-14. v10 state reconstructed from the raw_data_export snapshot. After Step 5 (pipeline rebuild) this will naturally extend to the live edge. |

**Documentation (3 files)**

| File | Action | Purpose |
|---|---|---|
| `HANDOVER.md` | **REPLACE** | Finished merged HANDOVER reflecting v10 canonical state. |
| `credentials.md` | **REPLACE** | Finished merged credentials doc with Artemis entry, updated ETF premium note. |
| `refit_report_v10.md` | **ADD** (new) | This session's full write-up with corrected attribution across the three changes. |

**Not included (and why)**

- **Parquets** (`artemis_etf_btc.parquet`, `coinglass_etf_premium_discount.parquet`) — project knowledge can't store binary files. Re-pull via the scripts in Step 4.
- **`build_robust.py`** — unchanged in v10. The existing file in your project stays. (A sigmoid/multi-window infrastructure branch was built during the amplification investigation, but every candidate was rejected; shipping that instrumentation would add dead code for rejected fixes.)
- **KPI CSVs, grid artifacts** — numbers are all in `refit_report_v10.md` prose.

---

## Upload instructions

### Step 1 — Install the Artemis SDK

The V4 ETF Flows builder requires the `artemis` Python package (official Artemis SDK). Without it, `pull_artemis_etf.py` fails on import and `build_etf_flows.py` fails when it tries to read the Artemis parquet.

```bash
pip install artemis
```

One-time install. If you have a dedicated pipeline venv, install there.

### Step 2 — Back up what you're replacing

```bash
cd /mnt/project
mkdir -p backups/pre_v10
cp build_foundation.py build_etf_flows.py fix_parsers.py \
   master_daily_view_wf365.csv HANDOVER.md credentials.md \
   backups/pre_v10/
cp data/raw/coinglass_h3/etf_premium_discount.parquet \
   backups/pre_v10/etf_premium_discount_pre_v10.parquet
```

### Step 3 — Drop in the finished files

```bash
cd /mnt/project
# Code
cp <package>/build_foundation.py .
cp <package>/build_etf_flows.py .
cp <package>/fix_parsers.py .
cp <package>/pull_artemis_etf.py .

# Docs (REPLACE existing HANDOVER.md and credentials.md; ADD refit report)
cp <package>/HANDOVER.md .
cp <package>/credentials.md .
cp <package>/refit_report_v10.md .

# Canonical master (overwrite existing)
cp <package>/master_daily_view_wf365_v10.csv master_daily_view_wf365.csv
```

Replace `<package>` with wherever you've unpacked this archive.

### Step 4 — Re-pull the data this package couldn't ship

The two data parquets aren't in this package (binary file limit). Re-pull them:

```bash
cd /mnt/project

# Fresh Coinglass premium (overwrites stale 2026-01-06 parquet with current data)
python3 pull_all_raw_data.py --source coinglass_h3 --out-dir data/raw

# Apply the new parser immediately (schema changed upstream)
python3 fix_parsers.py

# Artemis ETF data (new directory, new source)
export ARTEMIS_API_KEY=CXDPqeI6WtowV13pHKKhOm0PFjrUJWSGUJpa-kuSMzY
python3 pull_artemis_etf.py --out-dir data/raw
```

Expected after Step 4:
- `data/raw/coinglass_h3/etf_premium_discount.parquet` — updated with data through the live edge (~550+ rows).
- `data/raw/artemis_etf/btc.parquet` — new file, ~840+ rows from 2024-01-10.

If `pull_all_raw_data.py` needs a full refresh (other endpoints), run it with `--source all` instead. Full pull is ~30–40 min (Velo is the dominant cost).

### Step 5 — Rebuild canonicals from the new data

```bash
cd /mnt/project
bash regenerate_canonicals.sh
```

This runs the v10 code (D2h classifier + V4 ETF Flows) against the freshly-pulled data. Expected output:
- Regime distribution over 2021-06-30 → live edge: bull ~33%, neutral ~25%, bear ~42%.
- Hypothesis-level ETF Flows calibration: in-sample AUC ~0.62, hold-out AUC ~0.38.
- wf365 y_60 full-window Sharpe ~1.09, hold-out Sharpe ~1.15 (gross, 5-hypothesis; ETH removed from ensemble in v12).

Reconstruction-lineage caveat: if you pull completely fresh data (vs the snapshot I used), expect some drift from the v10 numbers above (principally FRED historical revisions on T10Y2Y, DFF, BAMLH0A0HYM2 — documented in v9 refit). The ensemble hold-out AUC should still be ~0.90 and regime distribution approximately as above.

### Step 6 — Verify the install

```bash
cd /mnt/project
python3 -c "
import pandas as pd
df = pd.read_csv('master_daily_view_wf365.csv', parse_dates=['date']).set_index('date')
last = df.dropna(subset=['position']).iloc[-1]
print(f'Date:       {last.name.date()}')
print(f'Regime:     {last.regime}')
print(f'Percentile: {last.percentile:.4f}')
print(f'Position:   {last.position:.4f}')
print()
print('Reference (v10 at 2026-04-14): bear percentile 0.8110 position 0.0000')
print('If your live-edge date is later, regime should still be bear and position 0')
print('(BTC is ~40% below its trailing 365d peak).')
"
```

If you skipped Step 5 and dropped in the v10 master as-is, date = 2026-04-14 and values match the reference line exactly. If you ran Step 5, date is today (or the last available trading day) and regime should still be bear.

### Step 7 — When you're ready: start the 4+ week paper-trading shadow run

This is the gating open item for everything downstream. v10 is a large structural change; four amplification-fix candidates were tested this session and all rejected; paper trading is the missing measurement. Nothing ships to real capital until 4+ weeks of clean paper-trading divergence data are in hand.

---

## Roll-back procedure

If you need to revert to v8 canonical:

```bash
cd /mnt/project
cp backups/pre_v10/*.py .
cp backups/pre_v10/master_daily_view_wf365.csv .
cp backups/pre_v10/HANDOVER.md .
cp backups/pre_v10/credentials.md .
cp backups/pre_v10/etf_premium_discount_pre_v10.parquet \
   data/raw/coinglass_h3/etf_premium_discount.parquet
# pull_artemis_etf.py and data/raw/artemis_etf/ can stay; not referenced by v8
# refit_report_v10.md can stay as historical record
```

Then `bash regenerate_canonicals.sh` to rebuild v8 master from the rolled-back code.

---

## Summary of what v10 changed and why

**D2h regime classifier** does essentially all the work in the v10 adoption. Hold-out AUC moves +0.113 (0.788 → 0.901) — the largest single-component prediction-layer improvement in this model's refit history. Pays a full-window Sharpe cost of −0.19 per `d2h_spec.md`'s expected tradeoff. Regime distribution over the eval window shifts from 40/38/22 bull/neutral/bear to 33/25/42 — bear days nearly double because D2h calls drawdowns faster and stays bear longer.

**Fresh Coinglass premium** is a data-freshness win with essentially no model impact (hold-out Sharpe −0.07 in isolation). The value is removing a known-stale endpoint from production.

**ETF Flows V4 hybrid** is a structural upgrade (real volume share replacing a weak proxy, vendor decoupling for flow data, cleaner degradation path). Hypothesis-level hold-out AUC drops 0.049, ensemble-level impact is small and mixed. Adopted on structural grounds, not KPI grounds.

Net v8 → v10: full Sharpe −0.17, hold-out Sharpe −0.04, **hold-out AUC +0.11**, hold-out MaxDD +1.6 pp, hold-out position call for today (2026-04-14) unchanged at 0.00 (bear regime, 40% below trailing peak).

See `refit_report_v10.md` for full attribution including the four rejected amplification-fix candidates.

---

## Open items (carried forward — see HANDOVER.md for full detail)

1. **Paper trading shadow run (4+ weeks)** — blocking real capital and further amplification-fix work.
2. Cardinal calibration retry.
3. Operational comms runtime.
4. Operational hygiene (some addressed by this package; remainder carries forward).
5. `OPERATIONS.md`.
6. Annual health-check script — add ETF Flows hold-out AUC stability monitoring.
7. **Deferred: structural fix for rolling-percentile amplification** — four candidates tested, zero adoptable. Next phase paper-trading-driven, not backtest-driven.
