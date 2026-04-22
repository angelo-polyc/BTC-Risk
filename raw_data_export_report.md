# Raw data export report

- **File**: `raw_data_export.csv`
- **Total columns**: 162 (incl. date)
- **Total rows**: 26470
- **Date range**: 1954-07-01 → 2026-12-19
- **Size**: 11.5 MB

## Wide-format sources (31)

- price/btc_ohlc: 4228 rows, cols=['open', 'high', 'low', 'close', 'volume']
- price/eth_ohlc: 3079 rows, cols=['open', 'high', 'low', 'close', 'volume']
- fred/BAMLH0A0HYM2: 7645 rows
- fred/DEXJPUS: 13854 rows
- fred/DFF: 26217 rows
- fred/DFII10: 5822 rows
- fred/DGS10: 16053 rows
- fred/DGS2: 12461 rows
- fred/DTWEXBGS: 5082 rows
- fred/SP500: 2513 rows
- fred/T10Y2Y: 12462 rows
- fred/VIXCLS: 9162 rows
- cftc/TFF_133741: 418 rows, cols=['Dealer_Positions_Long_All', 'Dealer_Positions_Short_All', 'Asset_Mgr_Positions_Long_All', 'Asset_Mgr_Positions_Short_All', 'Lev_Money_Positions_Long_All', 'Lev_Money_Positions_Short_All', 'Open_Interest_All', 'Market_and_Exchange_Names', 'CFTC_Contract_Market_Code']
- coinglass_cycle/200w_heatmap: 5218 rows, cols=['price', 'mA1440']
- coinglass_cycle/2yr_ma_multiplier: 5718 rows, cols=['price', 'ma2y', 'ma2y_x5']
- coinglass_cycle/ahr999: 5551 rows, cols=['ahr999', 'price', 'ahr999_inv']
- coinglass_cycle/bmo: 5209 rows, cols=['price', 'bmo_value']
- coinglass_cycle/bubble_index: 5749 rows, cols=['bubble_index', 'price']
- coinglass_cycle/fear_greed: 2973 rows, cols=['fear_greed', 'price']
- coinglass_cycle/golden_ratio: 5718 rows, cols=['price', 'x8', '2LowBullHigh', 'ma350', '1.6AccumulationHigh', 'x21', 'x13', 'x3', 'x5']
- coinglass_cycle/rainbow_chart: 5968 rows, cols=['price', 'band_1', 'band_2', 'band_3', 'band_4', 'band_5', 'band_6', 'band_7', 'band_8', 'band_9', 'band_10']
- coinglass_h2/basis_btc: 1930 rows, cols=['open_basis', 'close_basis', 'open_change', 'close_change']
- coinglass_h2/basis_eth: 1930 rows, cols=['open_basis', 'close_basis', 'open_change', 'close_change']
- coinglass_h2/coin_margin_oi_btc: 1929 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h2/coin_margin_oi_eth: 1930 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h2/funding_rate_oi_weighted_btc: 1930 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h2/funding_rate_oi_weighted_eth: 1930 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h2/oi_aggregated_btc: 1930 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h2/oi_aggregated_eth: 1930 rows, cols=['open', 'high', 'low', 'close']
- coinglass_h3/etf_flow_history: 581 rows, cols=['flow_usd', 'price_usd']
- coinglass_h3/etf_premium_discount: 484 rows, cols=['avg_premium_pct', 'n_etfs']

## Long-format sources — pivoted wide on exchange (16)

Per the user's 'zero lossiness' instruction, these sources were NOT aggregated with first-non-null. Instead, each per-exchange row was pivoted into its own column, so column naming for these sources is `{group}__{series}__{exchange}__{column}` — longer than the spec's `{group}__{series}__{column}` but preserves all data.

- coinglass_h2/liquidations_btc: pivoted on exchange=['Binance', 'Bybit', 'OKX'], metrics=['long_liquidation_usd', 'short_liquidation_usd'], 1930 unique dates × 6 cols
- coinglass_h2/liquidations_eth: pivoted on exchange=['Binance', 'Bybit', 'OKX'], metrics=['long_liquidation_usd', 'short_liquidation_usd'], 1930 unique dates × 6 cols
- velo_btc/buy_dollar_volume: pivoted on exchange=['binance', 'binance-futures', 'bybit', 'coinbase', 'okex-swap'], 1930 unique dates × 5 cols
- velo_btc/buy_liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_btc/coin_open_interest_close: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_btc/funding_rate: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_btc/liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_btc/sell_dollar_volume: pivoted on exchange=['binance', 'binance-futures', 'bybit', 'coinbase', 'okex-swap'], 1930 unique dates × 5 cols
- velo_btc/sell_liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_eth/buy_dollar_volume: pivoted on exchange=['binance', 'binance-futures', 'bybit', 'coinbase', 'okex-swap'], 1930 unique dates × 5 cols
- velo_eth/buy_liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_eth/coin_open_interest_close: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_eth/funding_rate: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_eth/liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols
- velo_eth/sell_dollar_volume: pivoted on exchange=['binance', 'binance-futures', 'bybit', 'coinbase', 'okex-swap'], 1930 unique dates × 5 cols
- velo_eth/sell_liquidations_dollar_volume: pivoted on exchange=['binance-futures', 'bybit', 'okex-swap'], 1930 unique dates × 3 cols

## Deduplication (wide sources with duplicate date rows) (1)

- coinglass_h2/coin_margin_oi_btc: dropped 1 dup date rows

## Skipped (2)

- coinglass_h3/etf_list.parquet: metadata per spec
- coinglass_h3/etf_detail.parquet: metadata per spec
