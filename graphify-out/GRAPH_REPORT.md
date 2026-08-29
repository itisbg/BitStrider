# Graph Report - .  (2026-08-28)

## Corpus Check
- 143 files · ~153,478 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1401 nodes · 2714 edges · 120 communities (98 shown, 22 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 103 edges (avg confidence: 0.65)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Community 0
- Community 1
- Community 2
- Community 3
- Community 4
- Community 5
- Community 6
- Community 7
- Community 8
- Community 9
- Community 10
- Community 11
- Community 12
- Community 13
- Community 14
- Community 15
- Community 16
- Community 17
- Community 18
- Community 19
- Community 20
- Community 21
- Community 22
- Community 23
- Community 24
- Community 25
- Community 26
- Community 27
- Community 28
- Community 29
- Community 30
- Community 31
- Community 32
- Community 33
- Community 34
- Community 35
- Community 36
- Community 37
- Community 38
- Community 39
- Community 40
- Community 41
- Community 42
- Community 43
- Community 44
- Community 45
- Community 46
- Community 47
- Community 48
- Community 49
- Community 50
- Community 51
- Community 52
- Community 53
- Community 54
- Community 55
- Community 56
- Community 57
- Community 58
- Community 59
- Community 60
- Community 61
- Community 62
- Community 63
- Community 64
- Community 65
- Community 66
- Community 67
- Community 68
- Community 69
- Community 70
- Community 71
- Community 72
- Community 73
- Community 74
- Community 75
- Community 76
- Community 77
- Community 78
- Community 79
- Community 80
- Community 81
- Community 82
- Community 83
- Community 84
- Community 85
- Community 86
- Community 87
- Community 88
- Community 89
- Community 90
- Community 91
- Community 92
- Community 93
- Community 94
- Community 95
- Community 96
- Community 97
- Community 98
- Community 99
- Community 100
- Community 101
- Community 102
- Community 103
- Community 104
- Community 105

## God Nodes (most connected - your core abstractions)
1. `EnhancedExecutor` - 114 edges
2. `get_bars()` - 73 edges
3. `Signal` - 61 edges
4. `MarketState` - 49 edges
5. `PreopenIntelligenceScanner` - 27 edges
6. `AppContext` - 27 edges
7. `ETradeClient` - 26 edges
8. `OptionsExecutor` - 25 edges
9. `_get_options_chain()` - 25 edges
10. `_fetch_bar_context()` - 25 edges

## Surprising Connections (you probably didn't know these)
- `Heartbeat Liveness Timestamp` --semantically_similar_to--> `ApexTraderAutoRun scheduled task installer`  [INFERRED] [semantically similar]
  heartbeat.txt → windows_schedule_apextrader.ps1
- `ApexTraderAutoRun scheduled task installer` --references--> `main()`  [AMBIGUOUS]
  windows_schedule_apextrader.ps1 → main.py
- `data/never_trade.txt (permanent ticker exclusion list)` --conceptually_related_to--> `EnhancedExecutor`  [INFERRED]
  data/never_trade.txt → engine/execution/__init__.py
- `day_picks.json (daily prediction output)` --shares_data_with--> `is_bull_regime()`  [INFERRED]
  predictions/day_picks.json → engine/utils/market.py
- `_demo()` --indirect_call--> `_raise()`  [INFERRED]
  engine/orchestrator.py → scripts/test_drift_backfill.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Broker Abstraction Layer (Alpaca/E*TRADE interface parity)** — engine_broker_broker_factory, engine_broker_etrade_client, execution_enhancedexecutor [INFERRED 0.85]
- **Kill-Mode Emergency Liquidation** — engine_risk_kill_mode_check, engine_execution_enhanced_enhancedexecutor, engine_options_executor_optionsexecutor [EXTRACTED 1.00]
- **Fail-open trading gate design pattern** — engine_utils_data_check_sentiment_gate, engine_utils_earnings_no_earnings_soon, engine_utils_market_check_vix_roc_filter, engine_utils_market_is_bull_regime [INFERRED 0.85]
- **ShortSqueeze gate-check duplication across standalone scripts** — scripts__squeeze_screen, scripts__squeeze_screen_ti, scripts__squeeze_scan_ti, scripts__squeeze_deep, scripts__debug_squeeze, scripts__squeeze_timing [INFERRED 0.80]
- **Cross-checking market data across Alpaca / yfinance / Finnhub** — scripts_backtest_finnhub, scripts_check_alpaca_data, scripts_check_options_data [INFERRED 0.80]
- **Options backtesting via shared Black-Scholes simulation and OPTIONS_* config** — scripts_backtest_options, scripts_backtest_open_window, scripts_backtest_ti_primary, config [INFERRED 0.80]
- **Trade Ideas Data Capture and Universe Persistence Pipeline** — engine_ti_capture_tradeideas_scrape_tradeideas, data_ti_primary_tickers, data_ti_unusual_options_tickers, data_universe_tickers, engine_equity_universe_add_tickers [INFERRED 0.85]
- **TI Capture Task Scheduler Chain** — windows_schedule_ti_capture_task, scripts_run_ti_capture_task_launcher, engine_ti_capture_tradeideas_scrape_tradeideas [EXTRACTED 1.00]
- **Options Eligible Universe Multi-Source Resolution** — engine_config_get_options_universe, engine_config_load_options_universe, engine_equity_universe_get_ti_primary, engine_equity_universe_get_tier, data_ti_unusual_options_tickers [INFERRED 0.85]

## Communities (120 total, 22 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (49): CONFIG.md — ApexTrader Configuration Reference, TI Primary Tickers (ti_primary.json), TI Unusual Options Tickers (ti_unusual_options.json), Dynamic Universe Tickers (universe.json), _load_options_universe(), Load live TI unusual-options-volume tickers. Returns the scraped unusual…, add_tickers(), get_latest_batch() (+41 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (53): get_options_universe(), OPTIONS_ELIGIBLE_UNIVERSE, _OPTIONS_FALLBACK_UNIVERSE, Return the live options universe, applying override rules. Core liquid names…, _calc_iv_rank, Series, Options IV rank helper utilities for ApexTrader., Calculate IV rank as current IV versus trailing historical volatility range.… (+45 more)

### Community 2 - "Community 2"
Cohesion: 0.06
Nodes (26): _demo(), date, ratchet_scale(), For every open position whose shares are fully free (qty_available > 0 AND no…, Tighten the trailing stop on a position once it's up…, Submit a position-closing order as a marketable limit crossing the spread by…, Close any position whose broker-rejected PDT stop has been breached. Called…, Actively watch every open position's loss while the market is NOT in regular… (+18 more)

### Community 3 - "Community 3"
Cohesion: 0.06
Nodes (22): EOD_CLOSE_STRATEGIES, EnhancedExecutor, Submit at most one active DAY trailing entry per symbol., Live account-wide short-selling gate — Alpaca's own Reg T equity minimum…, Broker rejected a short with "cannot be sold short" / 40310000 / "account is…, How many times `symbol` has already been entered today -- resets on a date…, True if `symbol` should use the trailing-buy entry path instead of the normal…, Submit a trailing-buy entry, then a GTC trailing stop at TRAIL_STOP_PCT%. TP… (+14 more)

### Community 4 - "Community 4"
Cohesion: 0.07
Nodes (23): _alpaca_option_symbol(), _bs_option_price(), OptionsExecutor, OptionsPosition, date, TradingClient, Check open options (Single & MLEG) and close at target/stop. Handles Net MtM…, Tracked open options position. (+15 more)

### Community 5 - "Community 5"
Cohesion: 0.06
Nodes (27): _apply_confidence_size_ramp(), _apply_strategy_kelly_mult(), _apply_thin_liquidity_override(), _entry_rechase_slip_pct(), ApexTrader - Enhanced Executor Optimized trade executor with consolidated…, Decide what _validate_trade does with a _check_momentum_freshness result:…, # NOTE: closing an existing position is NOT a new day trade., Next slip% for an entry re-chase attempt (_sweep_pending_entries) -- starts… (+19 more)

### Community 6 - "Community 6"
Cohesion: 0.08
Nodes (29): BearBreakdownStrategy, FloatRotationStrategy, GapBreakoutStrategy, get_strategy_instances(), LiquiditySweepStrategy, OpeningBellSurgeStrategy, ORBStrategy, PMHighBreakoutStrategy (+21 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (14): OrderType, Enum, _CoverClient, _executor(), _FakeClient, _Order, _Pos, _Quote (+6 more)

### Community 8 - "Community 8"
Cohesion: 0.15
Nodes (14): Signal, AccountSnapshot, _check_ema_trend_alignment(), _check_momentum_freshness(), PositionInfo, Reject a gap/momentum signal (MOMENTUM_FRESHNESS_STRATEGIES) if price has…, 2026-08-22, user request: simplified from an EMA9-vs-EMA20 crossover to an…, Returns (shares, skip_reason). Downsizes if BP constrained, skips if below min.… (+6 more)

### Community 9 - "Community 9"
Cohesion: 0.15
Nodes (27): alpaca_client(), compare_series(), fetch_alpaca_bars(), fetch_finnhub_bars(), fetch_yfinance_bars(), format_return(), main(), normalize_times() (+19 more)

### Community 10 - "Community 10"
Cohesion: 0.13
Nodes (9): AutoBotWatchdog, Path, Check PID_FILE for a live watchdog before touching anything. Task Scheduler's…, Seconds since engine/orchestrator.py last wrote HEARTBEAT_FILE, or None if it…, Runs for the life of the watchdog, independent of any single main.py subprocess…, Thin outer supervisor around _run_loop(): if anything inside ever raises…, Logger, Popen (+1 more)

### Community 11 - "Community 11"
Cohesion: 0.14
Nodes (24): AppContext, _build_context(), _concentration_check_job(), _eod_close_job(), _fetch_account_and_positions(), _guardrail_close_job(), log_status(), _price_drift_stop_job() (+16 more)

### Community 12 - "Community 12"
Cohesion: 0.09
Nodes (23): _drain_subprocess_output, _ensure_virtualenv, _heartbeat_age_seconds, _heartbeat_monitor, _parse_env_file, _python_is_healthy, _repair_pyvenv_home, _run_command (+15 more)

### Community 13 - "Community 13"
Cohesion: 0.16
Nodes (11): _calc_atr14(), EarlySqueezeDetector, DataFrame, Fires 9:30–10:15 AM ET for low-float stocks showing gap + projected RVOL >4× +…, Calculate Average True Range over the last `period` bars., calc_rsi(), get_bars(), Fetch OHLCV bars via Alpaca (yfinance fallback). Results are cached per… (+3 more)

### Community 14 - "Community 14"
Cohesion: 0.14
Nodes (20): BearCallSpreadStrategy, BearPutStrategy, _check_memory(), CoveredCallStrategy, get_dynamic_option_filters(), _get_filters(), _get_options_chain(), _get_options_universe() (+12 more)

### Community 15 - "Community 15"
Cohesion: 0.12
Nodes (20): check_quarterly(), get_quarter_start(), load_daily_state(), load_quarterly_state(), date, ApexTrader — Session Daily and quarterly P&L tracking state. Extracted from…, Persist current daily-start equity to disk (thread-safe)., Reset daily counters for a new trading day and prune the universe. (+12 more)

### Community 16 - "Community 16"
Cohesion: 0.11
Nodes (21): _demo(), _is_thinly_traded(), _passes_guardrails(), _prefetch_snapshots(), Self-check for get_scan_targets()'s market_state-gated guardrail pre-filter…, Write direction lists, switching sides on the 09:30 open after 10 ET., Persist the latest scanned long and short candidates at the same cadence., Batch-fetch stock snapshots for *symbols* and store in _snapshot_cache. A… (+13 more)

### Community 17 - "Community 17"
Cohesion: 0.14
Nodes (20): calc_macd(), _demo(), get_daily_volume_bars(), get_data_client(), get_option_data_client(), get_premarket_bars(), get_price(), is_dead_ticker() (+12 more)

### Community 18 - "Community 18"
Cohesion: 0.11
Nodes (19): get_adaptive_interval(), Return next scan interval in minutes based on VIX, market phase, and position…, check_vix_roc_filter(), get_market_hours_interval(), get_market_sentiment(), get_position_tuning_interval(), is_market_open(), is_open_window() (+11 more)

### Community 19 - "Community 19"
Cohesion: 0.12
Nodes (19): _apply_tradeideas_results(), _demo(), get_alpaca_movers_queue(), get_discovered_trending(), _load_movers_queue_from_disk(), _prune_queue(), ApexTrader — Discovery Manages live trending-stock scans and Trade Ideas…, Submit or check a background TI toplist scrape. (+11 more)

### Community 20 - "Community 20"
Cohesion: 0.14
Nodes (5): get_preopen_watchlist(), PreopenIntelligenceScanner, Build a scored pre-open watchlist and inject high-priority tickers. This is…, Return the current pre-open intelligence watchlist., scan_preopen_intelligence()

### Community 21 - "Community 21"
Cohesion: 0.16
Nodes (19): _apply_oi_to_df(), _fetch_oi_from_contracts(), _get_chain_alpaca(), _is_bullish_reversal(), OptionsChainInfo, _parse_occ_symbol(), DataFrame, date (+11 more)

### Community 22 - "Community 22"
Cohesion: 0.19
Nodes (15): notifications package API, _bool_env(), build_eod_report, _build_html_section(), _format_currency(), _get_env(), _has_fresh_ticker(), notify_eod (+7 more)

### Community 23 - "Community 23"
Cohesion: 0.14
Nodes (16): Submit or check a background TI scrape for core Trade Ideas pages., scan_tradeideas_universe(), _ensure_logged_in(), _extract_race_sides(), _extract_tickers(), main(), If the page just loaded is TI's login form -- any scan page redirects here once…, Scrape Trade Ideas scan pages using a persistent Edge window. The browser stays… (+8 more)

### Community 24 - "Community 24"
Cohesion: 0.12
Nodes (10): _live_quote_mid(), _marketable_limit_price(), A limit price just past the reference price -- fills like a market order under…, Live bid/ask midpoint -- the reference _marketable_limit_price should bound…, Re-chase a resting ENTRY order that hasn't filled within…, _FakeTradeClient, _Quote, _QuoteClient (+2 more)

### Community 25 - "Community 25"
Cohesion: 0.15
Nodes (17): _automation_user_data_dir(), _create_edge_driver(), _patch_config(), _patch_high_short_float(), Path, Trade Ideas — Screenshot + Universe Updater…, Return a dedicated, non-default --user-data-dir for the automation Edge.…, Spawn a new visible Edge window and return the driver (never headless). (+9 more)

### Community 26 - "Community 26"
Cohesion: 0.21
Nodes (16): _is_valid_ti_ticker(), Return False for obvious scraper garbage: too short, too long, non-alpha, or…, demo(), fetch_long_short_candidates(), fetch_yahoo_universe(), Yahoo Finance equity universe — replaces the TI (Trade Ideas) Selenium/Edge…, Return (gainers, losers), each [(symbol, pct_change_today), ...]. Gainers =…, Gainers + losers + trending, deduped (order preserved, first-seen wins),… (+8 more)

### Community 27 - "Community 27"
Cohesion: 0.13
Nodes (16): BEAR_SHORT_UNIVERSE, get_scan_targets(), Equity scan universe = Alpaca-movers queue + top TI_PRIMARY_SCAN_BATCH_LIMIT…, filter_universe_by_positions(), Filter out symbols already held or with unfilled buy orders from the scan…, _build_scan_targets(), Refresh the filtered active stock lists independently of scan duration., Call fn(*args, **kwargs) and log its wall time under [TIMING] <label>. (+8 more)

### Community 28 - "Community 28"
Cohesion: 0.16
Nodes (12): get_priority_scan_queue(), Return the current sympathy/EDGAR/watchlist tickers (read-only peek), pruning…, ButterflyStrategy, IronCondorStrategy, MeanReversionCallStrategy, OptionSignal, Sell iron condor: Sell OTM call and put, buy further OTM call and put for…, Buy call butterfly: Buy lower-wing call, sell 2 ATM calls, buy upper-wing call.… (+4 more)

### Community 29 - "Community 29"
Cohesion: 0.18
Nodes (16): _get_bars_alpaca(), get_bars_batch(), _get_bars_yfinance(), get_finnhub_bars(), _normalize_df(), DataFrame, A usable, fresh bar came back — clear any suppression immediately., Standardize column names and convert 'time' to ET-aware timestamps. (+8 more)

### Community 30 - "Community 30"
Cohesion: 0.12
Nodes (15): bool_env, filter_trending_momentum, format_currency, get_env, get_finnhub_trending_tickers, get_trending_tickers, Logger, engine.utils.data ----------------- External data integrations: Finnhub… (+7 more)

### Community 31 - "Community 31"
Cohesion: 0.13
Nodes (6): ReplaceOrderRequest, FakeAccount, FakeClient, FakeOrder, FakePosition, Self-check for the 2026-08-17 fix: enforce_position_concentration's trim was…

### Community 32 - "Community 32"
Cohesion: 0.14
Nodes (14): leveraged_underlying(), Best-effort underlying key for the same-underlying entry guard. Exact group…, _check_kill_mode(), _filter_eligible(), _log_skipped(), _margin_cushion_ok(), Return the stock-only execution capacity; broad-market regime is ignored., Apply confidence gate, position cross-ref, and long-only enforcement. Returns… (+6 more)

### Community 33 - "Community 33"
Cohesion: 0.24
Nodes (4): PreopenSignalProvider, Store the active market snapshot for per-cycle execution decisions., MarketState, _run_options_scan.py (standalone options universe scan)

### Community 34 - "Community 34"
Cohesion: 0.17
Nodes (9): _fetch_squeeze_fundamentals(), _fetch_squeeze_rs(), Fetch short float %, gross margins, and revenue growth via yfinance (daily-…, Fetch 13-week price return relative to S&P 500 via Finnhub (daily-cached)., Directional call / bull-call-spread on high short-float stocks with confirmed…, ShortSqueezeStrategy, Gate-by-gate debug for ShortSqueezeStrategy on a single symbol., Deep-dive ShortSqueeze gate check for a single symbol. Usage: python -m… (+1 more)

### Community 35 - "Community 35"
Cohesion: 0.21
Nodes (13): kelly_pct(), _pull_matched_trades(), Strategy scoreboard -- recurring Kelly/win-rate health check across every…, {strategy: (n, win_rate, avg_win, avg_loss, kelly)} from a trade list., Pull data, compute the scoreboard, log + print it, return flagged strategies., Kelly fraction: W - (1-W)/R, R = avg_win/avg_loss (both positive $ amounts).…, True if a strategy is worth surfacing: currently enabled, enough trades to…, Network call: pull every apex-tagged entry, match to its exit, pull confidence… (+5 more)

### Community 36 - "Community 36"
Cohesion: 0.22
Nodes (11): _load_cik_map (engine.data.edgar_scraper), _ticker_from_cik (engine.data.edgar_scraper), get_edgar_triggered_tickers (EDGAR 8-K scraper), _load_cik_map(), EDGAR 8-K RSS Scraper ===================== Polls the SEC's free public 8-K…, Fetch the latest EDGAR 8-K ATOM feed and return tickers for companies that…, Fetch SEC company_tickers.json and build CIK → ticker lookup (once per session)., Resolve a raw CIK string (any length) to a ticker via the CIK map. (+3 more)

### Community 37 - "Community 37"
Cohesion: 0.15
Nodes (8): is_high_short_float(), ApexTrader - Configuration Professional Automated Trading System Modular…, Return True if symbol is in the static HSF set OR in the live tier-2 universe., _raise(), Self-check for the price-drift-stop restart backfill (2026-08-14, at the user's…, Self-check for the momentum-entry freshness guard (2026-08-11, widened…, _sig(), Self-check for the strategy enable/disable toggles (2026-08-14, at the user's…

### Community 38 - "Community 38"
Cohesion: 0.17
Nodes (7): PDTTracker, TradingClient, On startup, reconstruct today's entry log from Alpaca filled orders. Prevents…, On startup, reconstruct self.order_cache from any BUY trailing-stop orders…, Pattern Day Trader tracking — syncs with live Alpaca daytrade_count., Returns day trades remaining. 999 = exempt if account is PDT-exempt or equity…, OptionsExecutor._check_pdt_status

### Community 39 - "Community 39"
Cohesion: 0.17
Nodes (12): OptionsExecutor.monitor_positions, One iteration's worth of _start_software_stop_thread's checks. Returns the…, Monitor existing options positions and attempt one new entry per cycle., Retry the latest top eligible signals on the five-second poller. Every attempt…, True if now_et (ET, tz-aware) falls within [ENTRY_WINDOW_START_ET,…, _retry_top_entries(), _run_options_cycle(), _tick() (+4 more)

### Community 40 - "Community 40"
Cohesion: 0.15
Nodes (12): _at_ema20_pullback(), _calc_hv30(), _calc_rsi_scalar(), _ema50_above(), _lower_bollinger_touch(), Series, True if the last close is at or almost exactly at the lower Bollinger Band., True if the last close is above the 50-day EMA. (+4 more)

### Community 41 - "Community 41"
Cohesion: 0.19
Nodes (11): _parse_timeframe(), HMM fetch bypasses get_bars staleness gate, _fetch_hmm_bars, _fit_and_classify, get_hmm_regime, Per-symbol 2-state Gaussian HMM regime detector. Fits on 7 days of 1-min close-…, Return True (bull) / False (bear) / None (insufficient data or fit failure).…, Fetch historical 1-min bars for HMM fitting directly via Alpaca. Bypasses… (+3 more)

### Community 42 - "Community 42"
Cohesion: 0.15
Nodes (4): FakeClient, FakePosition, _FixedDateTime, Self-check for the 2026-08-17 fix: close_eod_positions and…

### Community 43 - "Community 43"
Cohesion: 0.24
Nodes (5): ETradeClient, Alpaca-interface-compatible E*TRADE REST client. Usage: client =…, Load cached access token from disk (survives restarts within same session)., Interactive OAuth 1.0a flow. Call once per trading day. Prints the…, Cancel an order — mirrors alpaca TradingClient.cancel_order_by_id().

### Community 44 - "Community 44"
Cohesion: 0.21
Nodes (10): risk package API, kill_mode.check, kill_mode.is_active, ApexTrader — Kill Mode Extreme bear-market circuit breaker. Extracted from…, Return True when kill mode is engaged for today., Check extreme bear conditions and trigger emergency close if needed. Returns…, get_vix(), get_vix_interval() (+2 more)

### Community 45 - "Community 45"
Cohesion: 0.20
Nodes (12): _automation_edge_present(), _create_edge_driver_with_timeout(), _get_driver(), _is_driver_alive(), _iter_own_automation_edge(), _kill_orphaned_automation_edge(), Return True if the Edge WebDriver session is still responsive., Yield psutil.Process objects for our own automation's main msedge.exe (never… (+4 more)

### Community 46 - "Community 46"
Cohesion: 0.17
Nodes (4): FakeClient, FakeOrder, FakePosition, Self-check for the 2026-08-18 fix: _sweep_force_closes no longer chases…

### Community 47 - "Community 47"
Cohesion: 0.18
Nodes (6): datetime, Return the UTC fill timestamp a position was opened — hour-precision…, Close any position (long or short) that hasn't settled into a clear positive…, Pure decision logic for check_price_drift_stop: return a reason string if the…, When _price_drift_history has no rolling history yet for symbol (a fresh…, Every PRICE_DRIFT_CHECK_INTERVAL_MIN (10 min), exit any same-day position…

### Community 48 - "Community 48"
Cohesion: 0.22
Nodes (10): _calc_atr14(), calculate_atr(), Compute Average True Range over the last `period` bars. Returns 0.0 on failure., calculate_risk_adjusted_size, Local effective_* vars instead of mutating imported config, get_dynamic_tier, engine.utils.risk ----------------- ATR-based tier assignment (with 15-min…, Return position-sizing metadata for an entry. Uses local effective_* variables… (+2 more)

### Community 49 - "Community 49"
Cohesion: 0.24
Nodes (11): _demo(), _poller_staleness_job(), datetime, Scheduled every minute (see run()) -- alerts if SoftwareStopPoller hasn't…, Return the configured interval (minutes) for now_et's tier, or None if outside…, Scheduled every minute (see run()) -- refreshes data/ti_primary.json from Yahoo…, python -m engine.orchestrator -- asserts _poller_staleness_job's alert state…, True if now_et falls within [DISCOVERY_WINDOW_START_ET, ENTRY_WINDOW_END_ET] --… (+3 more)

### Community 50 - "Community 50"
Cohesion: 0.20
Nodes (9): data/never_trade.txt (permanent ticker exclusion list), BrokerFactory.create_stock_client, BrokerFactory.get_broker_type, ETradeClient (Alpaca-compatible E*TRADE adapter), _Account, LimitOrderRequest, ApexTrader — E*TRADE Client Alpaca-compatible adapter over the E*TRADE OAuth…, Return account snapshot — mirrors alpaca TradingClient.get_account(). (+1 more)

### Community 51 - "Community 51"
Cohesion: 0.22
Nodes (5): True if *symbol* already has a resting non-GTC order (i.e. something other than…, Return the symbol of the open long position with the worst unrealized P&L %.…, Return the symbol of the oldest closable long position held >= min_hours…, Return (symbol, entry_confidence) of the held long position with the lowest…, Try to close the stalest (24h+, falling back to weakest P&L) position to make…

### Community 52 - "Community 52"
Cohesion: 0.31
Nodes (10): _BarCtx, _fetch_bar_context(), Pre-computed bar data and indicators, shared across all strategy scan() calls., Fetch 80-day daily bars and compute common indicators for all strategies.…, _fetch_squeeze_fundamentals (engine.options.strategies), _fetch_squeeze_rs (engine.options.strategies), ShortSqueezeStrategy (engine.options.strategies), _debug_squeeze.py (ShortSqueeze gate-by-gate debug script) (+2 more)

### Community 53 - "Community 53"
Cohesion: 0.22
Nodes (8): BreakoutRetestCallStrategy, _calc_rr(), MomentumCallStrategy, Buy ATM calls when price retests a prior breakout level and bounces., R/R ratio: ATR-scaled expected move in the DTE window vs premium paid.…, Check 20-EMA trend alignment. Returns (aligned: bool, ema20_value: float).…, Buy near-term calls on confirmed bullish breakouts with A+ filters. Entry…, _trend_aligned()

### Community 54 - "Community 54"
Cohesion: 0.20
Nodes (9): _find_existing_edgedriver(), _installed_edge_version(), Full version string (e.g. '151.0.4129.78') of the installed Edge, or None if it…, Locate msedgedriver.exe — checks repo .drivers/ first, then ~/.wdm cache., Pure selection logic split out of _find_existing_edgedriver for testability:…, Try to attach to an already-running Edge instance that was started with…, _select_cached_driver(), _try_attach_edge() (+1 more)

### Community 55 - "Community 55"
Cohesion: 0.22
Nodes (4): _FakeClient, _FakeOrder, _make_executor(), Self-check for check_pending_entries_ema (2026-08-24, user request): "every…

### Community 56 - "Community 56"
Cohesion: 0.25
Nodes (7): BrokerFactory, BrokerFactory, ApexTrader - Broker Factory Selects the appropriate broker client. Supports…, Factory for creating broker clients., Create an options trading client. Currently only Alpaca supports options., Determine broker type from client instance., engine.broker package public API

### Community 57 - "Community 57"
Cohesion: 0.22
Nodes (5): MarketOrderRequest, _Position, Return all open positions — mirrors alpaca TradingClient.get_all_positions()., Submit a market, limit, or trailing-stop order. Accepts MarketOrderRequest,…, Market-close an open position (long or short). Mirrors alpaca…

### Community 58 - "Community 58"
Cohesion: 0.31
Nodes (4): _Order, Map E*TRADE order status → Alpaca-style status string., Return list of orders — mirrors alpaca TradingClient.get_orders()., Return single order by ID — mirrors alpaca TradingClient.get_order_by_id().

### Community 59 - "Community 59"
Cohesion: 0.25
Nodes (6): _bars_for(), _FakeClient, _make_executor(), DataFrame, Self-check for the EMA15 reclaim switch (2026-08-24, user request): "ema 15…, Build a synthetic 1-min close series whose ewm(span=15).mean().iloc[-1] equals…

### Community 60 - "Community 60"
Cohesion: 0.25
Nodes (3): FakeClient, Self-check for the entry-log restart rebuild covering SHORT positions…, _rebuild()

### Community 61 - "Community 61"
Cohesion: 0.28
Nodes (5): _closed(), _FakeClient, Self-check for the no-gain-exit band change (2026-08-11) and long+short…, Run the real close_no_gain_positions() against one fake position. Returns…, _run()

### Community 62 - "Community 62"
Cohesion: 0.28
Nodes (4): _Client, _make_executor(), _Pos, Self-check for the price-drift-stop age gate (2026-08-18, user request: "check…

### Community 63 - "Community 63"
Cohesion: 0.25
Nodes (5): _CountingClient, _make_executor(), _Order, Self-check for treating ANY prior-traded stock as a re-entry (2026-08-18, user…, get_orders returns `orders` and counts how many times it's called -- proves the…

### Community 64 - "Community 64"
Cohesion: 0.25
Nodes (3): _FakeClient, _make_executor(), Self-check for the protect_positions() qty_available sign bug (2026-08-12).…

### Community 65 - "Community 65"
Cohesion: 0.32
Nodes (7): CORRELATION_GROUPS, _fetch_quotes(), get_active_sympathies (sector sympathy scanner), Sector Sympathy Scanner ======================= Monitors "leader" stocks for…, Check all leaders and return a deduplicated list of sympathy tickers for any…, Fetch Finnhub quotes for *symbols*; returns {sym: {"c", "pc", "dp"}}., get_finnhub_client

### Community 66 - "Community 66"
Cohesion: 0.25
Nodes (8): DELISTED_STOCKS, HIGH_SHORT_FLOAT_STOCKS, Fetch Alpaca Most Actives + Market Movers and inject qualifying symbols into…, Write the current queue to disk. Best-effort -- a save failure must never block…, Sector sympathy + EDGAR 8-K scanner. Sympathy: checks leader stocks (via…, _save_movers_queue_to_disk(), scan_alpaca_movers(), scan_sympathy_and_edgar()

### Community 67 - "Community 67"
Cohesion: 0.29
Nodes (8): get_dynamic_universe(), PRIORITY_1_MOMENTUM, PRIORITY_2_ESTABLISHED, PRIORITY_3_MARKET, Return (p1, p2, p3) merged lists, re-reading universe.json on every call., STOCKS dict, merge_live(), Merge dynamic (TTL-managed) tickers with core static list, deduplicating and…

### Community 68 - "Community 68"
Cohesion: 0.25
Nodes (8): _build_short_queue(), _execute_bear_plan(), _execute_bull_plan(), _execution_rank(), Keep default basket signals behind ordinary day-scan signals., Pre-screen short candidates: remove cooldown hits and non-shortable assets.…, Bear regime: attempt 1 swap-long then up to BEAR_SHORT_SIGNALS_CAP shorts., Bull (or neutral) regime: try eligible signals ranked by confidence, highest…

### Community 69 - "Community 69"
Cohesion: 0.43
Nodes (7): ti package API, get_scans, ti.is_valid_ti_ticker, _load_capture_module, Any, ApexTrader TI helpers. Encapsulates Trade Ideas scraper module loading and…, ti.scrape_tradeideas (wrapper)

### Community 70 - "Community 70"
Cohesion: 0.33
Nodes (6): predictions package API, Any, Persistence helpers for daily trade predictions., Serialise *picks* (up to 5) to ``predictions/day_picks.json``. Each entry in…, save_day_picks, day_picks.json (daily prediction output)

### Community 71 - "Community 71"
Cohesion: 0.52
Nodes (5): Get-ActiveKeyPrefix(), Get-ActiveMode(), Get-WatchdogAlive(), main polling loop, Start-BotWatchdog()

### Community 72 - "Community 72"
Cohesion: 0.33
Nodes (4): _Asset, _Quote, Return latest bid/ask — mirrors alpaca DataClient.get_latest_quote()., Check if a symbol is tradable — mirrors alpaca TradingClient.get_asset().…

### Community 73 - "Community 73"
Cohesion: 0.33
Nodes (4): ETradeAuthRequired, Raised when OAuth tokens are missing or expired and manual auth is needed., Call at startup. Raises ETradeAuthRequired if interactive auth is needed., Exception

### Community 74 - "Community 74"
Cohesion: 0.40
Nodes (5): is_never_trade(), load_never_trade(), Permanent ticker exclusion list — data/never_trade.txt. Shared by universe…, Return the set of permanently excluded tickers. Re-reads the file when its…, OptionsExecutor.place_option_order

### Community 75 - "Community 75"
Cohesion: 0.33
Nodes (5): build_top5_report, _format_signal_text(), date, ApexTrader A+ Options scan email test — sends a formatted test with sample…, _S

### Community 76 - "Community 76"
Cohesion: 0.33
Nodes (5): check_sentiment_gate, Return (passes_gate, bullish_pct) from Alpaca News headline sentiment. Scores…, no_earnings_soon, Shared earnings-date lookup, used by both options and equity strategies to…, Return True if no earnings are expected within *days* calendar days. Data…

### Community 78 - "Community 78"
Cohesion: 0.40
Nodes (3): _FakeClient, _make_executor(), Self-check for the _ratchet_done reset fix (2026-08-10). Bug: _ratchet_done was…

### Community 79 - "Community 79"
Cohesion: 0.40
Nodes (3): _FakeClient, _make_executor(), Self-check for shorting_blocked (live property) and the HTB/equity conflation…

### Community 80 - "Community 80"
Cohesion: 0.40
Nodes (4): Create a stock trading client. Args: broker: 'alpaca' or 'etrade', TrailingStopOrderRequest, _place_trailing_stops(), Connect to Alpaca (live or paper, per TRADE_MODE env var) and place a GTC…

### Community 81 - "Community 81"
Cohesion: 0.40
Nodes (4): True if a _passes_guardrails() rejection reason should be re-admitted (sized…, _should_admit_thin_liquidity(), Self-check for _should_admit_thin_liquidity's EOD cutoff (2026-08-17, at the…, _state()

### Community 82 - "Community 82"
Cohesion: 0.40
Nodes (3): Register `job` to run at fixed wall-clock marks (:00, :10, :20, ... for…, _schedule_on_clock_grid(), Self-check for _schedule_on_clock_grid (2026-08-14, found while investigating…

### Community 84 - "Community 84"
Cohesion: 0.50
Nodes (4): get_adaptive_equity_allocation(), Returns adaptive position size percentage for equities based on pre-…, get_allocation_split(), Returns (equity_pct, options_pct) allocation based on market hours. - Off…

### Community 85 - "Community 85"
Cohesion: 0.50
Nodes (4): MODULARIZATION_PLAN.md — ApexTrader Refactor Plan, Centralize shared utilities in engine/utils/ (Stage 3), Flatten/remove unnecessary wrapper modules (Stage 2), Remove all `import *` usage (Stage 1)

## Ambiguous Edges - Review These
- `main()` → `ApexTraderAutoRun scheduled task installer`  [AMBIGUOUS]
  windows_schedule_apextrader.ps1 · relation: references
- `._add_candidate()` → `_is_valid_ti_ticker()`  [AMBIGUOUS]
  engine/equity/discovery.py · relation: references

## Knowledge Gaps
- **44 isolated node(s):** `LimitOrderRequest`, `engine.equity package public API`, `options package API`, `run_local_sh.sh script`, `TRADE_MODE` (+39 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **22 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `main()` and `ApexTraderAutoRun scheduled task installer`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **What is the exact relationship between `._add_candidate()` and `_is_valid_ti_ticker()`?**
  _Edge tagged AMBIGUOUS (relation: references) - confidence is low._
- **Why does `EnhancedExecutor` connect `Community 3` to `Community 2`, `Community 4`, `Community 5`, `Community 7`, `Community 8`, `Community 11`, `Community 24`, `Community 32`, `Community 33`, `Community 38`, `Community 44`, `Community 47`, `Community 51`, `Community 55`, `Community 59`, `Community 61`, `Community 63`, `Community 64`, `Community 65`, `Community 66`, `Community 68`, `Community 78`, `Community 79`?**
  _High betweenness centrality (0.180) - this node is a cross-community bridge._
- **Why does `get_bars()` connect `Community 13` to `Community 0`, `Community 1`, `Community 4`, `Community 5`, `Community 6`, `Community 8`, `Community 14`, `Community 16`, `Community 17`, `Community 18`, `Community 19`, `Community 21`, `Community 29`, `Community 30`, `Community 33`, `Community 34`, `Community 41`, `Community 44`, `Community 47`, `Community 48`, `Community 52`, `Community 88`, `Community 89`?**
  _High betweenness centrality (0.130) - this node is a cross-community bridge._
- **Why does `MarketState` connect `Community 33` to `Community 3`, `Community 4`, `Community 5`, `Community 11`, `Community 16`, `Community 17`, `Community 18`, `Community 19`, `Community 20`, `Community 21`, `Community 23`, `Community 27`, `Community 28`, `Community 32`, `Community 39`, `Community 41`, `Community 44`, `Community 66`, `Community 81`, `Community 84`?**
  _High betweenness centrality (0.067) - this node is a cross-community bridge._
- **Are the 18 inferred relationships involving `EnhancedExecutor` (e.g. with `Signal` and `OptionsExecutor`) actually correct?**
  _`EnhancedExecutor` has 18 INFERRED edges - model-reasoned connections that need verification._
- **Are the 14 inferred relationships involving `Signal` (e.g. with `AccountSnapshot` and `EnhancedExecutor`) actually correct?**
  _`Signal` has 14 INFERRED edges - model-reasoned connections that need verification._