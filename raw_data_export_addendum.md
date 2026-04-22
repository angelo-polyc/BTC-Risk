# raw_data_export.csv — column naming convention

**File:** `raw_data_export.csv`
**Companion report:** `raw_data_export_report.md` (per-source row counts, what was pivoted, what was skipped)
**Size:** 11.5 MB, 162 columns × 26,470 rows
**Date range:** 1954-07-01 → 2026-12-19 (driven by FRED DFF starting in 1954)

This file is the true raw layer — actual OHLCV prices, actual FRED values, actual Velo/Coinglass metrics. It complements the per-hypothesis sub-signal CSVs already in the project folder (`hypothesis_macro_equities.csv`, `hypothesis_cme.csv`, `hypothesis_crypto_derivatives.csv`, `hypothesis_classic_cycle.csv`, `hypothesis_etf_flows.csv`, `hypothesis_eth.csv`), which hold the post-transformation expanding-percentile ranks.

## Column naming — two shapes

When parsing column names, handle **both 3-component and 4-component names**. Splitting on `__` (double underscore) gives you the right number of parts to route on:

- **3 components: `group__series__col`** (99 columns)
  Standard wide-format sources. Example: `fred__SP500__value`, `price__btc_ohlc__close`, `coinglass_cycle__bmo__bmo_value`.

- **4 components: `group__series__exchange__col`** (62 columns)
  Long-format sources pivoted wide on exchange (to preserve per-exchange data instead of aggregating to first-non-null). Example: `velo_btc__funding_rate__binance-futures__value`, `coinglass_h2__liquidations_btc__Binance__long_liquidation_usd`.

Plus one: `date` (the index column, 1 component).

**Parsing recipe in pseudocode:**
```
parts = column_name.split("__")
if len(parts) == 1:    # date
elif len(parts) == 3:  # group, series, col
elif len(parts) == 4:  # group, series, exchange, col
else: error
```

## Which sources were pivoted (so you know where to expect 4-component names)

Per `raw_data_export_report.md`, these 16 sources were pivoted on exchange rather than aggregated:

**Coinglass H2 liquidations (2 sources, 3 exchanges each):**
- `coinglass_h2__liquidations_btc` (Binance, Bybit, OKX)
- `coinglass_h2__liquidations_eth` (Binance, Bybit, OKX)

**Velo BTC (7 sources):**
- `velo_btc__funding_rate`, `__coin_open_interest_close`, `__liquidations_dollar_volume`, `__buy_liquidations_dollar_volume`, `__sell_liquidations_dollar_volume` — all binance-futures / bybit / okex-swap
- `velo_btc__buy_dollar_volume`, `__sell_dollar_volume` — binance / binance-futures / bybit / coinbase / okex-swap (5 exchanges, includes spot venues)

**Velo ETH (7 sources):** same structure as Velo BTC.

All other sources are 3-component.

## Practical implications for the three tasks

**Task 1 (two-tier pinning):** doesn't need this file at all — work off `hypothesis_*.csv` in the project folder, which already contains the sub-signal ranks post-orientation. Those are the data the pinning formula operates on.

**Task 2 (walk-forward refit):** for ensemble-layer walk-forward (phase 1), use `master_daily_view.csv` which has the 6 hypothesis composite scores + regime + labels. You don't need the raw layer. If phase 2 (sub-signal-layer walk-forward) happens later, this file becomes useful for testing alternative rolling-window or z-score lookback choices without re-pulling from APIs — because the ranks in `hypothesis_*.csv` are computed with a fixed 180-day expanding window, and recomputing them with different parameters requires the raw values.

**Task 3 (crisis validation):** this is where the file matters most. Use `price__btc_ohlc__close` for actual BTC price narrative ("on 2020-03-12 BTC dropped from $X to $Y overnight..."), and use `fred__VIXCLS__value`, `fred__SP500__value`, etc. for annotating macro context. The Classic Cycle raw indicators go back to 2010–2012 and are all present here for pre-2021 crisis runs. Note that the `hypothesis_*.csv` rank columns have meaningful pre-2021 values for hypotheses with early-coverage data (Macro from ~2018, Classic Cycle raw indicators from 2010+), so Task 3 can combine both sources: raw values from this file for narrative, ranked sub-signals from the hypothesis CSVs for what the model would have seen.

## Provenance

Exported from the user's `btc_model/data/raw/` directory in a parallel Claude session, per spec:
- No transformations applied (no rank, no z-score, no rolling)
- Normalized to UTC midnight dates
- `fix_parsers.py` logic applied where needed (bubble_index, bmo, ETF endpoints)
- Long-format sources preserved via exchange pivot (zero-lossiness requirement) rather than first-non-null aggregation
- `coinglass_h3/etf_list.parquet` and `etf_detail.parquet` skipped per spec (metadata, not time series)
- One duplicate date row dropped from `coinglass_h2/coin_margin_oi_btc` (deduplication noted in report)

## What's in the project folder for data, all together

| File | Content | Use for |
|---|---|---|
| `master_daily_view.csv` | regime, labels, 6 composites, ensemble, returns | Tasks 1, 2 ensemble layer |
| `hypothesis_macro_equities.csv` | 8 sub-signal ranks + composite | Task 1 Macro audit |
| `hypothesis_cme.csv` | 3 sub-signal ranks + composite | Task 1 CME audit |
| `hypothesis_crypto_derivatives.csv` | 10 sub-signal ranks + composite | Task 1 Crypto Deriv audit |
| `hypothesis_classic_cycle.csv` | **4** sub-signal ranks + composite (v3 restricted set) | Task 1 CC audit |
| `hypothesis_etf_flows.csv` | 4 sub-signal ranks + composite | Task 1 ETF audit |
| `hypothesis_eth.csv` | 7 sub-signal ranks + composite | Task 1 ETH audit |
| **`raw_data_export.csv`** | **true raw values, 162 columns** | **Task 3 crisis validation, raw-value debugging, phase-2 walk-forward** |

**For most Task 1 / Task 2 work, you don't need to load `raw_data_export.csv` at all.** It's specifically for Task 3 and for any sub-signal re-computation that can't be done from the ranks alone.
