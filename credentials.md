# API Keys & Credentials

Consolidated reference for all external data sources used by the BTC model.
These values are also hardcoded in `pull_all_raw_data.py` (and `pull_artemis_etf.py` for Artemis) — this file is a human-readable consolidation for handover convenience, not the authoritative source. Rotation requires updating both this file and the pull script(s).

---

## Authenticated sources

### FRED (Federal Reserve Economic Data)

- **Used by:** Macro & Equities hypothesis
- **API key:** `be25f20e751efeb98e91471521e1cb57`
- **Client:** `pip install fredapi`
- **Usage:** `from fredapi import Fred; Fred(api_key=<key>).get_series("SP500")`
- **Series used:** `SP500`, `DFII10`, `BAMLH0A0HYM2`, `T10Y2Y`, `DGS2`, `DGS10`, `DFF`, `VIXCLS`, `DTWEXBGS`, `DEXJPUS`
- **Rate limits:** Generous, not a bottleneck in practice
- **Notes:** Free-tier key, no billing attached. `SP500` is limited to the most recent 10 years on free tier — pre-2016 SPX would need a proxy series, not currently blocking.

### Velo

- **Used by:** Crypto Derivatives hypothesis, ETH hypothesis
- **API key:** `e1d6ee67e6724c0281c02e02b5c131d5`
- **Client:** `pip install velodata`
- **Usage:** Via `velodata.client()` with key in environment or config
- **Series used:**
  - **BTC (`velo_btc`):** `funding_rate`, `coin_open_interest_close`, `liquidations_dollar_volume`, `buy_liquidations_dollar_volume`, `sell_liquidations_dollar_volume`, `buy_dollar_volume`, `sell_dollar_volume`
  - **ETH (`velo_eth`):** same 7 metrics
- **Coverage:** All series from 2021-01-01
- **Rate limits:** Strict. Full BTC pull takes ~10–15 min; same for ETH. Combined cold pull is ~25 min and is the dominant cost of cold-starting the pipeline. The pull script handles 409/500 retries via exponential backoff.
- **Notes:** `sell_liquidations` = LONG positions force-sold per Velo convention. Easy convention trap.

### Coinglass (v3 and v4 endpoints)

- **Used by:** Classic Cycle Indicators, Crypto Derivatives (basis + coin-margin), ETF Flows
- **API key:** `e921deddec3f4cb2b281b49330428d47`
- **Auth:** HTTP header `CG-API-KEY: <key>`
- **Base URLs:**
  - v3: `https://open-api-v3.coinglass.com`
  - v4: `https://open-api-v4.coinglass.com`

#### Endpoints

| Purpose | Version | Path |
|---|---|---|
| 2yr MA Multiplier (DROPPED in v3 model) | v3 | `/index/tow-year-ma-multiplier` |
| Golden Ratio Multiplier | v3 | `/index/golden-ratio-multiplier` |
| 200W MA Heatmap (DROPPED in v3 model) | v3 | `/index/tow-hundred-week-moving-avg-heatmap` |
| AHR999 | v3 | `/index/ahr999` |
| Rainbow Chart (DROPPED in v3 model) | v3 | `/index/bitcoin-rainbow-chart` |
| Fear & Greed | v3 | `/index/fear-greed-history` |
| Bubble Index (DROPPED in v3 model) | v3 | `/index/bitcoin-bubble-index` |
| BMO (Bitcoin Macro Oscillator) | v4 | `/index/bitcoin-macro-oscillator` |
| Futures basis (BTC + ETH) | v4 | basis endpoints |
| Coin-margined OI ratio | v4 | OI endpoints |
| ETF flow history | v4 | `/etf/bitcoin/flow-history` |
| ETF premium/discount history | v4 | `/etf/bitcoin/premium-discount/history` |

#### Known issues

- **ETF premium/discount endpoint was fixed upstream 2026-04-17.** Previously stale at 2026-01-06; now current through the live edge. **Schema changed when fixed**: the per-ETF `premium_discount_percent` field is no longer populated; premium is now computed by `fix_parsers.py` as `(market_price_usd − nav_usd) / nav_usd` averaged across ETFs (the same recipe this file always documented as authoritative).
- **Several endpoints have parser bugs** patched post-pull in `fix_parsers.py`:
  - `bubble_index` — API key is `index`, not `value`/`bubbleIndex`; date arrives as string
  - `bmo` — date column not normalized from `timestamp` (ms) field
  - `etf_flow_history` + `etf_premium_discount` — same date normalization issue
  - `etf_premium_discount` — no `premium_discount_percent` field exists in API response; must be computed as `(market_price_usd − nav_usd) / nav_usd` averaged across ETFs
- **DROPPED indicators** (2yr MA, heatmap, rainbow, bubble_index) still need their endpoints pulled in v3 of the model because the raw data is preserved even though they're excluded from the Classic Cycle composite. The `KEEP_SET` filter in `build_classic_cycle.py` operates at the composite layer, not the raw-pull layer.

### Artemis (v10, new)

- **Used by:** ETF Flows hypothesis (V4 hybrid builder — net flows, flow divergence, real spot-volume share)
- **API key:** `CXDPqeI6WtowV13pHKKhOm0PFjrUJWSGUJpa-kuSMzY`
- **Client:** `pip install artemis` (official Artemis SDK)
- **Usage:** Via `pull_artemis_etf.py`, which takes `ARTEMIS_API_KEY` env var. Underlying endpoint pattern: `/data/api/{METRIC}/?APIKey=<key>&startDate=<YYYY-MM-DD>&endDate=<YYYY-MM-DD>&symbols=bitcoin`
- **Series used:** `ETF_FLOWS` (daily net USD flows into spot-BTC ETFs), `ETF_SPOT_VOLUME` (daily USD trading volume of spot-BTC ETFs)
- **Response shape:** `{"data": {"symbols": {"bitcoin": {"METRIC": [{"date": ..., "val": ...}]}}}}`. Single nested container per metric; parse both `date` (string) and `val` (number).
- **Coverage:** BTC spot ETFs launched 2024-01-10. Pull script asks for 2024-01-01 onward; first 10 days are empty.
- **Rate limits:** Generous. Full pull ~5s. Not a bottleneck.
- **Provenance note:** On the 484-day overlap with Coinglass's flow series, Artemis ETF_FLOWS correlates 0.9989 with Coinglass (essentially identical). V4 hybrid uses Artemis for flows (vendor decoupling) and Coinglass for premium (no Artemis equivalent). If Artemis ever stops working, the v4 builder can fall back to v3 behavior with a minor code change — the flow signal is near-interchangeable.

---

## No-auth sources

### Yahoo Finance

- **Used by:** Foundation (BTC + ETH OHLC), Crypto Derivatives, ETH
- **Client:** `pip install yfinance`
- **Symbols:** `BTC-USD`, `ETH-USD`
- **Coverage:** BTC from 2014-09-17, ETH from 2017-11-09
- **Notes:** Free, no key needed. Occasional downtime but generally reliable.

### CFTC Traders in Financial Futures (TFF)

- **Used by:** CME hypothesis
- **Client:** `pip install cot_reports`
- **Report:** `TFF_133741` (financial futures, Bitcoin aggregate)
- **Coverage:** From 2018-04-10 (earliest available)
- **Notes:** Free, weekly cadence (reports released each Friday for the prior Tuesday position). Not a bottleneck.

---

## Full dependency install

```bash
pip install pandas numpy scipy scikit-learn pyarrow yfinance fredapi velodata cot_reports artemis requests
```

---

## Security notes

- None of these are billing-grade credentials — they're free-tier API keys
  on individual-researcher plans. Leakage risk is reputation and rate-limit
  throttling, not financial loss.
- If any of these stop working, the most likely cause is silent expiration
  or temporary rate-limit blocks rather than key invalidation. Try again
  after a cool-down before assuming the key is dead.
- Keys are committed in `pull_all_raw_data.py`, `pull_artemis_etf.py`, and in this file. All
  three locations are inside `/mnt/project/` and searchable by any Claude
  instance with project access. If you want these out of the project
  knowledge index, move them to environment variables and remove from
  all files — doing so would require a small refactor to
  `pull_all_raw_data.py` (Artemis is already env-var-friendly).
- Rotating a key requires updating:
  1. This file
  2. `pull_all_raw_data.py` or `pull_artemis_etf.py` (wherever the key string appears)
  3. Any live environment variables if rotated to env-based config
