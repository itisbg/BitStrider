# Graph Report - .  (2026-08-03)

## Corpus Check
- Large corpus: 90 files · ~2,904,924 words. Semantic extraction will be expensive (many Claude tokens). Consider running on a subfolder.

## Summary
- 953 nodes · 2016 edges · 59 communities (46 shown, 13 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 89 edges (avg confidence: 0.72)
- Token cost: 692,917 input · 0 output

## Community Hubs (Navigation)
- Trade Orchestration Core
- Broker Abstraction Layer
- Never-Trade List & Order Execution
- Kill-Switch Circuit Breaker
- Trade Ideas Scraper
- Notifications & Reports
- Ticker Universe Store
- Data Source Backtesting
- Options Strategies: Spreads & Covered Calls
- Data Utilities
- Equity Strategies: Sweep & Momentum
- Watchdog Process Management
- Market State & Adaptive Sizing
- Autobot Watchdog Wrapper
- EDGAR Filing Scraper
- Universe Configuration Merge
- Equity Strategies: Power of 3 & VWAP
- Options Chain Data
- Trending Stock Discovery
- Position Exit Management
- Preopen Signal Providers
- Stop-Loss & Risk Enforcement
- Options Backtest Engine
- Preopen Watchlist Scanner
- Trade Execution Core
- Squeeze Fundamentals Screener
- Equity Strategies: Breakouts
- Open-Window Backtest
- Float & Squeeze Detection
- Options Strategies: Condor & Butterfly
- Short-Float & Executor Utils
- Technical Indicator Helpers
- ATR Risk Tiering
- Options Data Cross-Check
- Backtest Signal Logic
- Broker Factory Core
- Bracket Order Builder
- Stale Position Cleanup
- PDT Compliance Tracker
- Breakout Retest Options
- TI Squeeze Screener
- IV Rank Helper
- Volatility Proxy Helpers
- TI Primary Backtest
- HSF Squeeze Screener
- Quarterly State Files
- Squeeze Screener Duplicates
- Package Marker
- Options Adaptive Retry
- Options Contract Sizing
- Options Budget Calc
- Options Spread Conversion
- Options Position Reconcile
- Package Marker
- Gap-Run Watchlist
- Scripts Package Marker

## God Nodes (most connected - your core abstractions)
1. `get_bars` - 65 edges
2. `EnhancedExecutor` - 51 edges
3. `MarketState` - 45 edges
4. `engine.utils facade` - 43 edges
5. `Signal` - 33 edges
6. `OptionsExecutor` - 27 edges
7. `ETradeClient` - 26 edges
8. `_get_options_chain` - 25 edges
9. `_fetch_bar_context` - 25 edges
10. `BrokerFactory` - 24 edges

## Surprising Connections (you probably didn't know these)
- `get_dynamic_universe()` --shares_data_with--> `data/never_trade.txt (permanent ticker exclusion list)`  [INFERRED]
  engine/config.py → data/never_trade.txt
- `data/never_trade.txt (permanent ticker exclusion list)` --conceptually_related_to--> `EnhancedExecutor`  [INFERRED]
  data/never_trade.txt → engine/execution/__init__.py
- `main.py enforces execution under repo-local .venv` --conceptually_related_to--> `_ensure_virtualenv`  [AMBIGUOUS]
  main.py → engine/watchdog.py
- `_place_trailing_stops()` --calls--> `TrailingStopOrderRequest`  [INFERRED]
  scripts/predict_tomorrow.py → engine/broker/etrade_client.py
- `_run_options_scan.py (standalone options universe scan)` --calls--> `scan_options_universe`  [EXTRACTED]
  scripts/_run_options_scan.py → engine/options/strategies.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Broker Abstraction Layer (Alpaca/E*TRADE interface parity)** — engine_broker_broker_factory, engine_broker_etrade_client, execution_enhancedexecutor [INFERRED 0.85]
- **Dynamic Ticker Universe Management (TTL-based universe.json)** — data_universe, engine_equity_universe_get_tier, engine_config_get_dynamic_universe [INFERRED 0.85]
- **Trade Ideas Primary Ticker Discovery Flow** — data_ti_primary, engine_equity_universe_get_ti_primary, engine_equity_scan_get_scan_targets [INFERRED 0.85]
- **PDT Day-Trade Compliance Enforcement** — engine_execution_enhanced_pdttracker, engine_execution_enhanced_enhancedexecutor, engine_options_executor_check_pdt_status [INFERRED 0.75]
- **Kill-Mode Emergency Liquidation** — engine_risk_kill_mode_check, engine_execution_enhanced_enhancedexecutor, engine_options_executor_optionsexecutor [EXTRACTED 1.00]
- **TI Ticker Scrape-Validate-Persist Pipeline** — engine_ti_capture_tradeideas_scrape_tradeideas, engine_ti_capture_tradeideas_patch_config, engine_ti_capture_tradeideas_is_valid_ti_ticker [INFERRED 0.85]
- **Fail-open trading gate design pattern** — engine_utils_data_check_sentiment_gate, engine_utils_earnings_no_earnings_soon, engine_utils_market_check_vix_roc_filter, engine_utils_market_is_bull_regime [INFERRED 0.85]
- **ShortSqueeze gate-check duplication across standalone scripts** — scripts__squeeze_screen, scripts__squeeze_screen_ti, scripts__squeeze_scan_ti, scripts__squeeze_deep, scripts__debug_squeeze, scripts__squeeze_timing [INFERRED 0.80]
- **Inconsistent virtualenv location conventions across launchers** — main_main, engine_watchdog__ensure_virtualenv, run_local_ps, run_local_sh [AMBIGUOUS 0.30]
- **Cross-checking market data across Alpaca / yfinance / Finnhub** — scripts_backtest_finnhub, scripts_check_alpaca_data, scripts_check_options_data [INFERRED 0.80]
- **Options backtesting via shared Black-Scholes simulation and OPTIONS_* config** — scripts_backtest_options, scripts_backtest_open_window, scripts_backtest_ti_primary, config [INFERRED 0.80]
- **Windows Task Scheduler watchdog automation chain** — windows_schedule_apextrader, scripts_run_autobot_task, scripts_monitor_bot_10min [INFERRED 0.85]

## Communities (59 total, 13 thin omitted)

### Community 0 - "Trade Orchestration Core"
Cohesion: 0.05
Nodes (63): filter_universe_by_positions(), Filter out symbols already held or with unfilled buy orders from the scan…, OptionsExecutor.monitor_positions, OptionsExecutor.place_option_order, AppContext, _build_context(), _build_scan_targets(), _build_short_queue() (+55 more)

### Community 1 - "Broker Abstraction Layer"
Cohesion: 0.06
Nodes (32): data/never_trade.txt (permanent ticker exclusion list), BrokerFactory, BrokerFactory.create_stock_client, BrokerFactory.get_broker_type, ApexTrader - Broker Factory Selects the appropriate broker client. Supports…, ETradeClient (Alpaca-compatible E*TRADE adapter), _Account, _Asset (+24 more)

### Community 2 - "Never-Trade List & Order Execution"
Cohesion: 0.06
Nodes (31): LimitOrderRequest, is_never_trade(), load_never_trade(), Permanent ticker exclusion list — data/never_trade.txt. Shared by universe…, Return the set of permanently excluded tickers. Re-reads the file when its…, _alpaca_option_symbol, _bs_option_price, OptionsExecutor (+23 more)

### Community 3 - "Kill-Switch Circuit Breaker"
Cohesion: 0.09
Nodes (38): _calc_atr14(), risk package API, kill_mode.check, kill_mode.is_active, ApexTrader — Kill Mode Extreme bear-market circuit breaker. Extracted from…, Return True when kill mode is engaged for today., Check extreme bear conditions and trigger emergency close if needed. Returns…, calculate_atr (+30 more)

### Community 4 - "Trade Ideas Scraper"
Cohesion: 0.08
Nodes (36): _create_edge_driver(), _extract_race_sides(), _extract_tickers(), _find_existing_edgedriver(), _get_driver, _is_driver_alive(), _is_valid_ti_ticker, main() (+28 more)

### Community 5 - "Notifications & Reports"
Cohesion: 0.10
Nodes (27): notifications package API, _bool_env(), build_eod_report, _build_html_section(), build_top5_report, _format_currency(), _format_signal_text(), _get_env() (+19 more)

### Community 6 - "Ticker Universe Store"
Cohesion: 0.11
Nodes (29): data/ti_primary.json (TI primary ticker capture), data/universe.json (dynamic ticker universe), engine/data/universe.json (alternate/stale universe snapshot), add_tickers(), get_latest_batch(), get_ti_primary, get_tier, _is_expired() (+21 more)

### Community 7 - "Data Source Backtesting"
Cohesion: 0.14
Nodes (28): requirements.txt — Python dependency manifest, alpaca_client(), compare_series(), fetch_alpaca_bars(), fetch_finnhub_bars(), fetch_yfinance_bars(), format_return(), main() (+20 more)

### Community 8 - "Options Strategies: Spreads & Covered Calls"
Cohesion: 0.13
Nodes (24): BearCallSpreadStrategy, BearPutStrategy, _check_memory(), CoveredCallStrategy, get_dynamic_option_filters, _get_filters, _get_options_chain, _get_options_universe() (+16 more)

### Community 9 - "Data Utilities"
Cohesion: 0.10
Nodes (26): bool_env, filter_trending_momentum, format_currency, get_env, Logger, engine.utils.data ----------------- External data integrations: Finnhub…, Filter a list of ticker dicts to those with >= min_momentum_pct 5-day move., Parse a boolean environment variable. Truthy: '1', 'true', 'yes'. (+18 more)

### Community 10 - "Equity Strategies: Sweep & Momentum"
Cohesion: 0.10
Nodes (19): get_strategy_instances, LiquiditySweepStrategy, MomentumStrategy, OpeningBellSurgeStrategy, ORBStrategy, ApexTrader - Strategies Trading strategy implementations: - TechnicalStrategy :…, Return instantiated strategy objects for the current market regime., Trade based on market sentiment with technical confirmation. (+11 more)

### Community 11 - "Watchdog Process Management"
Cohesion: 0.09
Nodes (23): _drain_subprocess_output, _ensure_virtualenv, _heartbeat_age_seconds, _heartbeat_monitor, _parse_env_file, _python_is_healthy, _repair_pyvenv_home, _run_command (+15 more)

### Community 12 - "Market State & Adaptive Sizing"
Cohesion: 0.10
Nodes (19): Fetch Alpaca Most Actives + Market Movers and inject qualifying symbols into…, scan_alpaca_movers(), scan_preopen_intelligence(), get_adaptive_equity_allocation(), Returns adaptive position size percentage for equities based on pre-…, Store the active market snapshot for per-cycle execution decisions., is_bull_regime as single canonical regime source, get_allocation_split (+11 more)

### Community 13 - "Autobot Watchdog Wrapper"
Cohesion: 0.16
Nodes (7): AutoBotWatchdog, Logger, Path, Seconds since engine/orchestrator.py last wrote HEARTBEAT_FILE, or None if it…, Runs for the life of the watchdog, independent of any single main.py subprocess…, Popen, Thread

### Community 14 - "EDGAR Filing Scraper"
Cohesion: 0.11
Nodes (21): _load_cik_map (engine.data.edgar_scraper), _ticker_from_cik (engine.data.edgar_scraper), CORRELATION_GROUPS (leveraged_inverse basket cap), get_edgar_triggered_tickers (EDGAR 8-K scraper), _load_cik_map(), EDGAR 8-K RSS Scraper ===================== Polls the SEC's free public 8-K…, Fetch the latest EDGAR 8-K ATOM feed and return tickers for companies that…, Fetch SEC company_tickers.json and build CIK → ticker lookup (once per session). (+13 more)

### Community 15 - "Universe Configuration Merge"
Cohesion: 0.12
Nodes (19): data/ti_unusual_options.json (TI unusual options tickers), get_dynamic_universe(), _load_options_universe(), ApexTrader - Configuration Professional Automated Trading System Modular…, Return (p1, p2, p3) merged lists, re-reading universe.json on every call., Load live TI unusual-options-volume tickers. Returns the scraped unusual…, get_priority_scan_queue, Return the current sympathy/EDGAR/screener tickers (read-only peek). Does NOT… (+11 more)

### Community 16 - "Equity Strategies: Power of 3 & VWAP"
Cohesion: 0.14
Nodes (19): PowerOf3Strategy, ICT Power of 3: tight morning accumulation → sweep below the range low…, Price reclaims VWAP from below with accelerating volume — second-leg setup.…, VWAPReclaimStrategy, calc_macd, calc_rsi, get_bars, Series (+11 more)

### Community 17 - "Options Chain Data"
Cohesion: 0.13
Nodes (21): _apply_oi_to_df(), _fetch_oi_from_contracts(), _get_chain_alpaca, _is_bullish_reversal(), OptionsChainInfo, _parse_occ_symbol(), DataFrame, date (+13 more)

### Community 18 - "Trending Stock Discovery"
Cohesion: 0.18
Nodes (19): _apply_tradeideas_results(), get_discovered_trending(), ApexTrader — Discovery Manages live trending-stock scans and Trade Ideas…, Submit or check a background TI toplist scrape., Return tickers found by trending scans this session (read-only copy)., Merge TI scrape results into *priority_1* / *priority_2* lists in-place., Submit or check a background TI scrape for core Trade Ideas pages., Submit or check a background Trade Ideas unusual options scrape. (+11 more)

### Community 19 - "Position Exit Management"
Cohesion: 0.12
Nodes (11): EnhancedExecutor, date, TradingClient, Symbols currently blocked from re-entry after an after-hours stop-loss exit., Optimized trade executor with consolidated long/short logic., Return the date a position was opened. Checks the in-memory entry log first,…, Close swing-strategy positions (i.e. any long NOT opened by a strategy in…, On startup, reconstruct today's entry log from Alpaca filled buy orders.… (+3 more)

### Community 20 - "Preopen Signal Providers"
Cohesion: 0.15
Nodes (9): PreopenSignalProvider, Refresh ``trending_stocks`` from live feeds (Finnhub, etc.). New tickers are…, scan_trending_stocks(), check_sentiment_gate, get_finnhub_trending_tickers, get_trending_tickers, Parse Finnhub general news for mentioned ticker symbols., Return (passes_gate, bullish_pct) from Alpaca News headline sentiment. Scores… (+1 more)

### Community 21 - "Stop-Loss & Risk Enforcement"
Cohesion: 0.12
Nodes (10): TrailingStopOrderRequest, Submit a position-closing order. During regular hours this is a plain market…, Close any position whose broker-rejected PDT stop has been breached. Called…, Actively watch every open position's loss while the market is NOT in regular…, Trim any position whose market value exceeds MAX_POSITION_CONCENTRATION_PCT of…, Trim a correlated basket (e.g. leveraged inverse-market ETFs) whose COMBINED…, Kill mode emergency exit. Closes every open position as safely as possible. PDT…, Scan open positions against stored ATR-based TP targets. Submits a market close… (+2 more)

### Community 22 - "Options Backtest Engine"
Cohesion: 0.18
Nodes (16): get_options_universe(), Return the live options universe, applying override rules. Core liquid names…, backtest_symbol(), _bs_delta(), _bs_price(), main(), _no_earnings_soon_bt(), _norm_cdf() (+8 more)

### Community 23 - "Preopen Watchlist Scanner"
Cohesion: 0.16
Nodes (4): get_preopen_watchlist(), PreopenIntelligenceScanner, Build a scored pre-open watchlist and inject high-priority tickers. This is…, Return the current pre-open intelligence watchlist.

### Community 24 - "Trade Execution Core"
Cohesion: 0.30
Nodes (6): Signal, AccountSnapshot, PositionInfo, Cached Alpaca account state — equity, buying power, live PDT count., Returns (shares, skip_reason). Downsizes if BP constrained, skips if below min., Cached snapshot of open positions.

### Community 25 - "Squeeze Fundamentals Screener"
Cohesion: 0.17
Nodes (11): _fetch_bar_context, _fetch_squeeze_fundamentals(), _fetch_squeeze_rs(), Fetch short float %, gross margins, and revenue growth via yfinance (daily-…, Fetch 13-week price return relative to S&P 500 via Finnhub (daily-cached)., Directional call / bull-call-spread on high short-float stocks with confirmed…, Fetch 80-day daily bars and compute common indicators for all strategies.…, ShortSqueezeStrategy (+3 more)

### Community 26 - "Equity Strategies: Breakouts"
Cohesion: 0.13
Nodes (11): BearBreakdownStrategy, _calc_atr14(), GapBreakoutStrategy, PMHighBreakoutStrategy, PreMarketMomentumStrategy, DataFrame, Short-entry: daily breakdown below 20-SMA + 10-day low with volume spike. Only…, Gap-up continuation: stock opens significantly above prior close. Logic: - Load… (+3 more)

### Community 27 - "Open-Window Backtest"
Cohesion: 0.23
Nodes (14): logs/bt_ow.txt — Open-Window Backtest run log, backtest(), _bs_price(), _hv30(), _load_spy_200sma(), main(), _norm_cdf(), _prep_bars() (+6 more)

### Community 28 - "Float & Squeeze Detection"
Cohesion: 0.16
Nodes (10): _passes_guardrails, Pre-scan gates: dollar-volume, RVOL, and gap-chase guard. Returns False to skip…, EarlySqueezeDetector, FloatRotationStrategy, _get_float_shares, _get_market_cap, Cached float share count sourced from yfinance. Returns None when float data is…, Cached market cap sourced from yfinance. Returns None when unavailable. Same… (+2 more)

### Community 29 - "Options Strategies: Condor & Butterfly"
Cohesion: 0.19
Nodes (10): ButterflyStrategy, IronCondorStrategy, MeanReversionCallStrategy, OptionSignal, Sell iron condor: Sell OTM call and put, buy further OTM call and put for…, Buy call butterfly: Buy lower-wing call, sell 2 ATM calls, buy upper-wing call.…, Buy ITM calls on oversold bounces from the lower Bollinger Band. Entry…, Scan the options-eligible universe and return A+ ranked signals. Active… (+2 more)

### Community 30 - "Short-Float & Executor Utils"
Cohesion: 0.15
Nodes (12): is_high_short_float(), Return True if symbol is in the static HSF set OR in the live tier-2 universe., TrendBreakerStrategy.scan, ApexTrader - Enhanced Executor Optimized trade executor with consolidated…, # NOTE: closing an existing position is NOT a new day trade., engine.execution package public API, check_vix_roc_filter, Return (allow_entry, vix_roc_pct). Blocks new entries when VIX has risen… (+4 more)

### Community 31 - "Technical Indicator Helpers"
Cohesion: 0.15
Nodes (12): _at_ema20_pullback(), _calc_hv30(), _calc_rsi_scalar(), _ema50_above(), _lower_bollinger_touch(), Series, True if the last close is at or almost exactly at the lower Bollinger Band., True if the last close is above the 50-day EMA. (+4 more)

### Community 32 - "ATR Risk Tiering"
Cohesion: 0.22
Nodes (11): get_dynamic_tier, engine.utils.risk ----------------- ATR-based tier assignment (with 15-min…, Return ATR-based TP/TS tier info for *symbol*. Result is cached per symbol for…, 15-min ATR tier cache avoids repeated bar fetches, main(), _place_trailing_stops(), DataFrame, 1. Write top_n tickers to predictions/watchlist.json (for reference). 2. Add… (+3 more)

### Community 33 - "Options Data Cross-Check"
Cohesion: 0.20
Nodes (8): CONFIG.md — ApexTrader Configuration Reference, check_alpaca_options(), check_yfinance(), main(), check_options_data.py -------------------- Cross-checks options data…, Check which symbols have optionable contracts on Alpaca., Check yfinance options availability for a symbol., CLI utility to print and validate ApexTrader config. Run with: python…

### Community 34 - "Backtest Signal Logic"
Cohesion: 0.24
Nodes (12): _backtest_rsi(), _bear_put_signal(), _breakout_retest_signal(), _mean_reversion_signal(), DataFrame, Unified signal logic for all strategies used in backtest., RSI-14 helper for backtest signal functions., True if breakout-retest pattern is confirmed at index `idx`. Uses the sub-… (+4 more)

### Community 35 - "Broker Factory Core"
Cohesion: 0.20
Nodes (7): BrokerFactory, Factory for creating broker clients., Create a stock trading client. Args: broker: 'alpaca' or 'etrade', Create an options trading client. Currently only Alpaca supports options., Determine broker type from client instance., _BarCtx, Pre-computed bar data and indicators, shared across all strategy scan() calls.

### Community 36 - "Bracket Order Builder"
Cohesion: 0.27
Nodes (4): MarketOrderRequest, OrderType, Close all intraday-strategy positions at EOD_CLOSE_TIME. Targets FloatRotation,…, Submit market entry then a GTC trailing stop at risk_info['stop_loss_pct']%. TP…

### Community 37 - "Stale Position Cleanup"
Cohesion: 0.29
Nodes (4): datetime, Return the UTC fill timestamp a position was opened — hour-precision…, Close any long position that has shown zero positive unrealized gain within…, Return the symbol of the oldest closable long position held >= min_hours…

### Community 38 - "PDT Compliance Tracker"
Cohesion: 0.33
Nodes (4): PDTTracker, Pattern Day Trader tracking — syncs with live Alpaca daytrade_count., Returns day trades remaining. 999 = exempt if account is PDT-exempt or equity…, OptionsExecutor._check_pdt_status

### Community 39 - "Breakout Retest Options"
Cohesion: 0.29
Nodes (6): BreakoutRetestCallStrategy, _calc_rr, Detect breakout-and-retest pattern. Returns (pattern_found: bool,…, Buy ATM calls when price retests a prior breakout level and bounces., R/R ratio: ATR-scaled expected move in the DTE window vs premium paid.…, _resistance_breakout_retest()

### Community 41 - "IV Rank Helper"
Cohesion: 0.40
Nodes (4): _calc_iv_rank, Series, Options IV rank helper utilities for ApexTrader., Calculate IV rank as current IV versus trailing historical volatility range.…

### Community 42 - "Volatility Proxy Helpers"
Cohesion: 0.50
Nodes (5): _calc_hv(), _iv_proxy(), Series, 30-day historical volatility (annualised)., IV proxy = 30d HV × 1.15 (typical options premium over realized vol).

### Community 43 - "TI Primary Backtest"
Cohesion: 0.50
Nodes (4): main(), parse_date(), date, backtest_ti_primary.py ----------------------- Backtest the latest Trade Ideas…

## Ambiguous Edges - Review These
- `_ensure_virtualenv` → `main.py enforces execution under repo-local .venv`  [AMBIGUOUS]
  main.py · relation: conceptually_related_to

## Knowledge Gaps
- **38 isolated node(s):** `engine.equity package public API`, `options package API`, `run_local_sh.sh script`, `TRADE_MODE`, `_S` (+33 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **13 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `_ensure_virtualenv` and `main.py enforces execution under repo-local .venv`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `get_bars` connect `Equity Strategies: Power of 3 & VWAP` to `ATR Risk Tiering`, `Never-Trade List & Order Execution`, `Kill-Switch Circuit Breaker`, `Options Strategies: Spreads & Covered Calls`, `Data Utilities`, `Equity Strategies: Sweep & Momentum`, `Market State & Adaptive Sizing`, `Universe Configuration Merge`, `Options Chain Data`, `Trending Stock Discovery`, `Preopen Signal Providers`, `Options Backtest Engine`, `Squeeze Fundamentals Screener`, `Equity Strategies: Breakouts`, `Open-Window Backtest`, `Float & Squeeze Detection`, `Short-Float & Executor Utils`?**
  _High betweenness centrality (0.155) - this node is a cross-community bridge._
- **Why does `EnhancedExecutor` connect `Position Exit Management` to `Trade Orchestration Core`, `Never-Trade List & Order Execution`, `Kill-Switch Circuit Breaker`, `Bracket Order Builder`, `Stale Position Cleanup`, `PDT Compliance Tracker`, `Notifications & Reports`, `Market State & Adaptive Sizing`, `Stop-Loss & Risk Enforcement`, `Trade Execution Core`, `Short-Float & Executor Utils`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Why does `MarketState` connect `Market State & Adaptive Sizing` to `Trade Orchestration Core`, `Never-Trade List & Order Execution`, `Data Utilities`, `Universe Configuration Merge`, `Options Chain Data`, `Trending Stock Discovery`, `Position Exit Management`, `Preopen Signal Providers`, `Preopen Watchlist Scanner`, `Float & Squeeze Detection`, `Options Strategies: Condor & Butterfly`, `Short-Float & Executor Utils`?**
  _High betweenness centrality (0.079) - this node is a cross-community bridge._
- **Are the 3 inferred relationships involving `EnhancedExecutor` (e.g. with `Signal` and `OptionsExecutor`) actually correct?**
  _`EnhancedExecutor` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `Signal` (e.g. with `AccountSnapshot` and `EnhancedExecutor`) actually correct?**
  _`Signal` has 5 INFERRED edges - model-reasoned connections that need verification._
- **What connects `engine.equity package public API`, `options package API`, `run_local_sh.sh script` to the rest of the system?**
  _38 weakly-connected nodes found - possible documentation gaps or missing edges._