# Graph Report - .  (2026-08-08)

## Corpus Check
- 183 files · ~125,718 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1687 nodes · 4059 edges · 61 communities detected
- Extraction: 54% EXTRACTED · 46% INFERRED · 0% AMBIGUOUS · INFERRED: 1863 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Trading Engine & Entry Pipeline|Trading Engine & Entry Pipeline]]
- [[_COMMUNITY_Strategy Base & Indicators|Strategy Base & Indicators]]
- [[_COMMUNITY_Bot Orchestration & Config|Bot Orchestration & Config]]
- [[_COMMUNITY_Logging & Data Persistence|Logging & Data Persistence]]
- [[_COMMUNITY_A6 Performance Simulation|A6 Performance Simulation]]
- [[_COMMUNITY_Risk Layers & Safety|Risk Layers & Safety]]
- [[_COMMUNITY_Exchange Client & API Layer|Exchange Client & API Layer]]
- [[_COMMUNITY_Telegram Notifications|Telegram Notifications]]
- [[_COMMUNITY_System Docs & Position Review|System Docs & Position Review]]
- [[_COMMUNITY_Forensics OHLCV & Audit Engine|Forensics OHLCV & Audit Engine]]
- [[_COMMUNITY_Arbitrage Scanner|Arbitrage Scanner]]
- [[_COMMUNITY_SQLite TradeRejection Queries|SQLite Trade/Rejection Queries]]
- [[_COMMUNITY_MongoDB Manager|MongoDB Manager]]
- [[_COMMUNITY_KuCoin Client|KuCoin Client]]
- [[_COMMUNITY_API Error & Rate Monitoring|API Error & Rate Monitoring]]
- [[_COMMUNITY_Batch Rotation Tests|Batch Rotation Tests]]
- [[_COMMUNITY_Position Value Helpers|Position Value Helpers]]
- [[_COMMUNITY_OHLCV Cache Tests|OHLCV Cache Tests]]
- [[_COMMUNITY_Forensics Report Renderer|Forensics Report Renderer]]
- [[_COMMUNITY_Top Gainers Retrospective|Top Gainers Retrospective]]
- [[_COMMUNITY_TradFi Filter Tests|TradFi Filter Tests]]
- [[_COMMUNITY_Log Cleanup Script|Log Cleanup Script]]
- [[_COMMUNITY_WSS Manager Tests|WSS Manager Tests]]
- [[_COMMUNITY_Stop Loss Management|Stop Loss Management]]
- [[_COMMUNITY_A3 What-if Analysis|A3 What-if Analysis]]
- [[_COMMUNITY_Winners Safety Verify|Winners Safety Verify]]
- [[_COMMUNITY_A6 Loser Pattern|A6 Loser Pattern]]
- [[_COMMUNITY_Session Analysis|Session Analysis]]
- [[_COMMUNITY_A5 What-if Analysis|A5 What-if Analysis]]
- [[_COMMUNITY_Drawdown Position Sizing|Drawdown Position Sizing]]
- [[_COMMUNITY_Drawdown Leverage Adjust|Drawdown Leverage Adjust]]
- [[_COMMUNITY_Env Detection|Env Detection]]
- [[_COMMUNITY_Config Repr|Config Repr]]
- [[_COMMUNITY_Live Mode Detection|Live Mode Detection]]
- [[_COMMUNITY_Config Refactor|Config Refactor]]
- [[_COMMUNITY_SQLite Fix|SQLite Fix]]
- [[_COMMUNITY_Strategy Dashboard|Strategy Dashboard]]
- [[_COMMUNITY_Live Positions Fetch|Live Positions Fetch]]
- [[_COMMUNITY_Trade Audit Tag|Trade Audit Tag]]
- [[_COMMUNITY_Net PnL Calc|Net PnL Calc]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]
- [[_COMMUNITY_ScratchUtility Tools|Scratch/Utility Tools]]

## God Nodes (most connected - your core abstractions)
1. `Config` - 230 edges
2. `MongoLogger` - 138 edges
3. `TradeManager` - 102 edges
4. `PortfolioProfitRatchet` - 99 edges
5. `SpotTradingEngine` - 84 edges
6. `SpotLogger` - 82 edges
7. `BaseStrategy` - 82 edges
8. `TrailingStopLayer` - 77 edges
9. `PortfolioCircuitBreaker` - 76 edges
10. `SQLiteManager` - 61 edges

## Surprising Connections (you probably didn't know these)
- `Closed Trades Matchmaking (Binance vs DB)` --semantically_similar_to--> `TP/SL Verification Report`  [INFERRED] [semantically similar]
  matchmaking_report.txt → verify_output.txt
- `Position Review (Read-Only)` --semantically_similar_to--> `Closed Trades Matchmaking (Binance vs DB)`  [INFERRED] [semantically similar]
  auditor_report.txt → matchmaking_report.txt
- `Position Review (Read-Only)` --semantically_similar_to--> `TP/SL Verification Report`  [INFERRED] [semantically similar]
  auditor_report.txt → verify_output.txt
- `Unique Imports Analysis` --conceptually_related_to--> `APEX HUNTER V14`  [INFERRED]
  scratch/unique_imports.txt → README.md
- `Env Example Variables Inventory` --conceptually_related_to--> `APEX HUNTER V14`  [INFERRED]
  scratch/env_example_vars.txt → README.md

## Hyperedges (group relationships)
- **Risk Validation Chain (11 Layers + Trailing + Ratchet)** — readme_risk_validation_chain, readme_risk_position_sizing, readme_risk_leverage_control, readme_risk_stop_loss, readme_risk_daily_loss_limit, readme_risk_max_drawdown, readme_risk_correlation, readme_risk_volatility, readme_risk_liquidity, readme_risk_rate_limit, readme_risk_circuit_breaker, readme_risk_capital_preservation, readme_trailing_stop, readme_global_ratchet [EXTRACTED 1.00]
- **Verify-Before-Write Execution Pipeline** — readme_verify_before_write, readme_risk_validation_chain, readme_capital_allocation, readme_ccxt_exchange_client, readme_apex_hunter_db [EXTRACTED 1.00]
- **Satellite Refactor (WSS-driven exit sentinel)** — satellite_refactor_design_wss_manager, satellite_refactor_design_decoupled_sentinel, satellite_refactor_plan_persistence, satellite_refactor_plan_uncage_moonshot, readme_priority_exit_sentinel, readme_trailing_stop [EXTRACTED 1.00]

## Communities

### Community 0 - "Trading Engine & Entry Pipeline"
Cohesion: 0.02
Nodes (96): report_pnl(), main(), main(), get_trades(), Calculate triangular arbitrage for a path, Close a position (futures-specific), check_m_order_id(), check_trailing() (+88 more)

### Community 1 - "Strategy Base & Indicators"
Cohesion: 0.02
Nodes (118): ABC, BaseStrategy, calculate_indicators(), generate_signal(), Base Strategy Class Abstract base for all trading strategies, Reset performance statistics, Abstract base class for all trading strategies     All strategies must implement, Calculate ADX (Average Directional Index) for trend strength          ADX Values (+110 more)

### Community 2 - "Bot Orchestration & Config"
Cohesion: 0.06
Nodes (114): Config, Centralized configuration management for Apex Hunter V14     Loads settings from, NUCLEAR OPTION: Closes all open positions and cancels all open orders on Binance, Run one trading cycle for a specific symbol, Collect and save market analysis data for dashboard, Fetch top N trading pairs by 24h volume          Args:             top_n: Number, Print trading summary, Futures Trading Engine - Simulates or executes trading with live market data. (+106 more)

### Community 3 - "Logging & Data Persistence"
Cohesion: 0.03
Nodes (59): Enum, JSONManager, JSON File Storage Manager Handles all data operations using JSON files instead o, Insert single document (same interface as MongoDB), Insert multiple documents, Find documents with optional query (simplified query support), JSON file-based storage manager     Drop-in replacement for MongoDB with identic, Simple aggregation pipeline support (+51 more)

### Community 4 - "A6 Performance Simulation"
Cohesion: 0.02
Nodes (46): simulate_a6_performance(), analyze_win_volatility(), simulate_a6_performance_v3(), main(), get_trades_from_db(), main(), main(), main() (+38 more)

### Community 5 - "Risk Layers & Safety"
Cohesion: 0.03
Nodes (38): CapitalPreservationLayer, Layer 11: Capital Preservation, CircuitBreakerLayer, Layer 10: Circuit Breaker Emergency shutdown on abnormal conditions, Layer 10: Emergency Circuit Breaker     Triggers emergency halt on critical fail, Record a critical failure and trigger halt, Configuration Management Loads and validates environment variables and configura, Initialize configuration from environment variables                  Args: (+30 more)

### Community 6 - "Exchange Client & API Layer"
Cohesion: 0.04
Nodes (52): API Manager Handles HTTP requests with retry logic, rate limiting, and error han, BaseExchangeClient, cancel_order(), get_balance(), get_markets(), get_orderbook(), get_positions(), get_ticker() (+44 more)

### Community 7 - "Telegram Notifications"
Cohesion: 0.05
Nodes (44): Log signal to Telegram, Check if spot positions should be closed, Check if position should exit (stop loss or take profit), Close position and calculate P&L including fees, Telegram Bot Notifications Supports 3 separate bots: Futures, Spot, Arbitrage, Send formatted alert                  Args:             title: Alert title, Single Telegram bot instance, Manages all 3 Telegram bots (Futures, Spot, Arbitrage) (+36 more)

### Community 8 - "System Docs & Position Review"
Cohesion: 0.04
Nodes (68): Position Review (Read-Only), Size Desync Finding, Closed Trades Matchmaking (Binance vs DB), data/apex_hunter.db (SQLite), APEX HUNTER V14, Capital Allocation (Confidence-Tiered), CCXTExchangeClient, config/config.py (ACTIVE) (+60 more)

### Community 9 - "Forensics OHLCV & Audit Engine"
Cohesion: 0.06
Nodes (46): analyze_rejection_fx(), audit_trade(), cache_file_count(), cache_status(), compute_window_change(), fetch_ohlcv(), _fetch_range_raw(), fetch_tickers() (+38 more)

### Community 10 - "Arbitrage Scanner"
Cohesion: 0.05
Nodes (29): ArbitrageScanner, Arbitrage Scanner Detects and logs arbitrage opportunities across multiple excha, Calculate simple arbitrage opportunity, Multi-type arbitrage opportunity scanner     Logs opportunities to Telegram with, Scan for triangular arbitrage on same exchange         Example: USDT → BTC → ETH, Initialize arbitrage scanner                  Args:             config: Configur, Calculate total fees for arbitrage trade         Includes: trading fees, withdra, Log top N opportunities of the hour (+21 more)

### Community 11 - "SQLite Trade/Rejection Queries"
Cohesion: 0.05
Nodes (31): fetch_rejections_from_sqlite(), fetch_trades_from_sqlite(), format_currency(), get_day_key(), main(), Fetch all trades from main SQLite DB (The Sole Source), Fetch all rejections from activity_log DB (The Sole Source), clean_database_files() (+23 more)

### Community 12 - "MongoDB Manager"
Cohesion: 0.06
Nodes (22): debug_spot_data(), json_serial(), main(), JSON serializer for objects not serializable by default json code, MongoManager, MongoDB Database Manager Handles all MongoDB operations for APEX HUNTER V14, Create database indexes for optimal query performance, MongoDB connection and operations manager     Supports both sync and async opera (+14 more)

### Community 13 - "KuCoin Client"
Cohesion: 0.08
Nodes (18): KuCoinClient, KuCoin Futures API Client Handles authentication, request signing, and API commu, Build full endpoint URL with query parameters                  Args:, Get account overview including balance and margin info                  Returns:, Get current ticker data for a symbol                  Args:             symbol:, Get order book data                  Args:             symbol: Trading symbol, KuCoin Futures API Client     Implements HMAC-SHA256 authentication and API comm, Get all open positions                  Returns:             Positions data (+10 more)

### Community 14 - "API Error & Rate Monitoring"
Cohesion: 0.17
Nodes (8): APIManager, Record a successful request for rate limiting, Make an API request with retry logic                  Args:             method:, API Request Manager     Handles HTTP communication with retry logic and rate lim, Initialize API Manager                  Args:             config: Configuration, Get API error statistics, Check if request would exceed rate limits                  Args:             end, Check if error threshold has been exceeded                  Returns:

### Community 15 - "Batch Rotation Tests"
Cohesion: 0.27
Nodes (9): FakeSymbols, Unit tests for OHLCV batch rotation (Task 2.3): ensures no symbol is missed and, Simulates a sweep: processes 600 symbols with a batch cap of 100., Returns the set of symbols analyzed (fetched or cache-hit)., Over 6 sweeps, the batch cap rotates so all symbols get refreshed., Open positions should be fetched even if batch capped (they need fresh data)., test_batch_rotates_across_sweeps(), test_no_symbol_missed() (+1 more)

### Community 16 - "Position Value Helpers"
Cohesion: 0.21
Nodes (9): calculate_pnl_percent(), calculate_position_value(), format_duration(), format_timestamp(), Utility Helper Functions Common utility functions used across the application, Calculate total position value, Format datetime for display, Calculate P&L percentage (+1 more)

### Community 17 - "OHLCV Cache Tests"
Cohesion: 0.23
Nodes (4): Fetch market data using CCXT (exchange-agnostic), Unit tests for the OHLCV TTL cache in PaperTradingEngine.fetch_market_data. Mock, Simulates PaperTradingEngine's cache logic directly (unit isolation)., TestOHLCVCache

### Community 18 - "Forensics Report Renderer"
Cohesion: 0.53
Nodes (10): _alpha_section(), _fmt(), _line(), _load(), main(), _pct(), render(), _resolve_report_path() (+2 more)

### Community 19 - "Top Gainers Retrospective"
Cohesion: 0.36
Nodes (8): compute_window_change(), fetch_klines(), get_traded_symbols(), main(), Fetch 1h klines between start/end for one symbol., Return (symbol, pct_change_over_window, open_price) or None., symbol -> trade summary from DB, to_ms()

### Community 20 - "TradFi Filter Tests"
Cohesion: 0.44
Nodes (8): _is_tradfi(), Unit tests for the TradFi contract filter (Task 2.2)., Replicates the filter logic in main.py get_top_pairs_by_volume., test_commodity_perp_filtered(), test_equity_perp_filtered(), test_missing_contract_type_not_filtered(), test_no_info_not_filtered(), test_regular_perp_not_filtered()

### Community 21 - "Log Cleanup Script"
Cohesion: 0.38
Nodes (5): cleanup_old_files(), cleanup_sqlite(), Log Cleanup Script Deletes log files older than 7 days to keep the server clean., Purge old records from SQLite activity logs, Delete files older than RETENTION_DAYS in the specified directories

### Community 22 - "WSS Manager Tests"
Cohesion: 0.4
Nodes (4): Test that non-USDT symbols are also converted correctly., Test that the WSS manager correctly parses the Binance array payload., test_wss_manager_parsing(), test_wss_manager_symbol_conversion()

### Community 23 - "Stop Loss Management"
Cohesion: 0.4
Nodes (2): Layer 3: Stop Loss Management, StopLossManagementLayer

### Community 24 - "A3 What-if Analysis"
Cohesion: 0.67
Nodes (2): get_total_pnl(), get_trades()

### Community 25 - "Winners Safety Verify"
Cohesion: 0.67
Nodes (2): get_all_trades(), would_be_stopped()

### Community 26 - "A6 Loser Pattern"
Cohesion: 0.83
Nodes (2): get_session(), main()

### Community 27 - "Session Analysis"
Cohesion: 0.83
Nodes (2): get_session(), main()

### Community 28 - "A5 What-if Analysis"
Cohesion: 0.67
Nodes (2): get_total_pnl(), get_trades()

### Community 29 - "Drawdown Position Sizing"
Cohesion: 0.67
Nodes (2): Calculate position size adjustment based on current drawdown                  Ar, Calculate position size adjustment based on current drawdown                  Ar

### Community 30 - "Drawdown Leverage Adjust"
Cohesion: 0.67
Nodes (2): Calculate leverage adjustment based on current drawdown                  Args:, Calculate leverage adjustment based on current drawdown                  Args:

### Community 31 - "Env Detection"
Cohesion: 0.67
Nodes (2): Check if connected to production exchange, Check if connected to production exchange

### Community 32 - "Config Repr"
Cohesion: 0.67
Nodes (2): String representation of configuration, String representation of configuration

### Community 33 - "Live Mode Detection"
Cohesion: 0.67
Nodes (2): Check if bot is in live trading mode, Check if bot is in live trading mode

### Community 34 - "Config Refactor"
Cohesion: 0.67
Nodes (1): replace_getenv()

### Community 35 - "SQLite Fix"
Cohesion: 0.67
Nodes (1): replacer()

### Community 36 - "Strategy Dashboard"
Cohesion: 0.67
Nodes (1): StrategyDashboard

### Community 37 - "Live Positions Fetch"
Cohesion: 0.67
Nodes (1): fetch_live_positions()

### Community 38 - "Trade Audit Tag"
Cohesion: 0.67
Nodes (1): analyze_trade()

### Community 39 - "Net PnL Calc"
Cohesion: 0.67
Nodes (1): calculate_net_pnl()

### Community 40 - "Scratch/Utility Tools"
Cohesion: 0.67
Nodes (1): price_feed_to_txt()

### Community 41 - "Scratch/Utility Tools"
Cohesion: 0.67
Nodes (1): audit()

### Community 42 - "Scratch/Utility Tools"
Cohesion: 0.67
Nodes (1): wipe_telegram()

### Community 43 - "Scratch/Utility Tools"
Cohesion: 0.67
Nodes (1): run_isolated_test()

### Community 45 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Convert string to boolean

### Community 52 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Initialize exchange client

### Community 53 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get account balance                  Returns:             Dictionary with balanc

### Community 54 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get open positions                  Args:             symbol: Optional symbol fi

### Community 55 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get current ticker/price data                  Args:             symbol: Trading

### Community 56 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get order book                  Args:             symbol: Trading symbol

### Community 57 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Place an order                  Args:             symbol: Trading symbol

### Community 58 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Cancel an order                  Args:             order_id: Order ID to cancel

### Community 59 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get all available markets                  Returns:             Markets informat

### Community 60 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Get trading fees                  Args:             symbol: Optional symbol for

### Community 61 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Convert string to boolean

### Community 62 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Generate trading signal from market data                  Args:             df:

### Community 63 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Calculate technical indicators                  Args:             df: DataFrame

### Community 66 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): Convert string to boolean

### Community 84 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): pymongo+motor (MongoDB)

### Community 85 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): streamlit+plotly Dashboard

### Community 86 - "Scratch/Utility Tools"
Cohesion: 1.0
Nodes (1): pytest==9.1.1

## Ambiguous Edges - Review These
- `Telegram Notifications` → `python-telegram-bot==20.7 (minimal)`  [AMBIGUOUS]
  requirements-minimal.txt · relation: conceptually_related_to

## Knowledge Gaps
- **304 isolated node(s):** `Centralized configuration management for Apex Hunter V14     Loads settings from`, `Initialize configuration from environment variables                  Args:`, `Load all configuration values from environment variables`, `Validate critical configuration values`, `Convert string to boolean` (+299 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Stop Loss Management`** (6 nodes): `stop_loss_aws.py`, `Layer 3: Stop Loss Management`, `StopLossManagementLayer`, `.evaluate()`, `.__init__()`, `stop_loss_aws.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `A3 What-if Analysis`** (4 nodes): `get_total_pnl()`, `get_trades()`, `analyze_a3_whatif.py`, `analyze_a3_whatif.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Winners Safety Verify`** (4 nodes): `verify_winners_safety.py`, `get_all_trades()`, `verify_winners_safety.py`, `would_be_stopped()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `A6 Loser Pattern`** (4 nodes): `get_session()`, `main()`, `a6_loser_pattern.py`, `a6_loser_pattern.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Session Analysis`** (4 nodes): `session_analysis.py`, `get_session()`, `main()`, `session_analysis.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `A5 What-if Analysis`** (4 nodes): `get_total_pnl()`, `get_trades()`, `analyze_a5_whatif.py`, `analyze_a5_whatif.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Drawdown Position Sizing`** (3 nodes): `.get_drawdown_adjusted_position_size()`, `Calculate position size adjustment based on current drawdown                  Ar`, `Calculate position size adjustment based on current drawdown                  Ar`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Drawdown Leverage Adjust`** (3 nodes): `.get_drawdown_adjusted_leverage()`, `Calculate leverage adjustment based on current drawdown                  Args:`, `Calculate leverage adjustment based on current drawdown                  Args:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Env Detection`** (3 nodes): `.is_production_environment()`, `Check if connected to production exchange`, `Check if connected to production exchange`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Config Repr`** (3 nodes): `.__repr__()`, `String representation of configuration`, `String representation of configuration`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Live Mode Detection`** (3 nodes): `.is_live_trading()`, `Check if bot is in live trading mode`, `Check if bot is in live trading mode`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Config Refactor`** (3 nodes): `refactor_config.py`, `replace_getenv()`, `refactor_config.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `SQLite Fix`** (3 nodes): `fix_sqlite.py`, `replacer()`, `fix_sqlite.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Strategy Dashboard`** (3 nodes): `StrategyDashboard`, `app.py`, `app.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Live Positions Fetch`** (3 nodes): `fetch_live_positions()`, `fetch_live_positions.py`, `fetch_live_positions.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Trade Audit Tag`** (3 nodes): `analyze_trade()`, `audit_tag_trade.py`, `audit_tag_trade.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Net PnL Calc`** (3 nodes): `calculate_net_pnl()`, `net_pnl_calculator.py`, `net_pnl_calculator.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (3 nodes): `wss_price_exporter.py`, `wss_price_exporter.py`, `price_feed_to_txt()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (3 nodes): `audit()`, `live_audit.py`, `live_audit.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (3 nodes): `wipe_telegram_standalone.py`, `wipe_telegram_standalone.py`, `wipe_telegram()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (3 nodes): `test_btc_verification.py`, `run_isolated_test()`, `test_btc_verification.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Convert string to boolean`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Initialize exchange client`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get account balance                  Returns:             Dictionary with balanc`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get open positions                  Args:             symbol: Optional symbol fi`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get current ticker/price data                  Args:             symbol: Trading`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get order book                  Args:             symbol: Trading symbol`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Place an order                  Args:             symbol: Trading symbol`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Cancel an order                  Args:             order_id: Order ID to cancel`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get all available markets                  Returns:             Markets informat`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Get trading fees                  Args:             symbol: Optional symbol for`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Convert string to boolean`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Generate trading signal from market data                  Args:             df:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Calculate technical indicators                  Args:             df: DataFrame`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `Convert string to boolean`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `pymongo+motor (MongoDB)`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `streamlit+plotly Dashboard`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Scratch/Utility Tools`** (1 nodes): `pytest==9.1.1`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `Telegram Notifications` and `python-telegram-bot==20.7 (minimal)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **Why does `Config` connect `Bot Orchestration & Config` to `Trading Engine & Entry Pipeline`, `Live Mode Detection`, `Config Repr`, `Strategy Base & Indicators`, `Logging & Data Persistence`, `Risk Layers & Safety`, `Exchange Client & API Layer`, `A6 Performance Simulation`, `Telegram Notifications`, `SQLite Trade/Rejection Queries`, `MongoDB Manager`, `Drawdown Position Sizing`, `Drawdown Leverage Adjust`, `Env Detection`?**
  _High betweenness centrality (0.216) - this node is a cross-community bridge._
- **Why does `Telegram notifications` connect `Risk Layers & Safety` to `Strategy Base & Indicators`, `Bot Orchestration & Config`, `Exchange Client & API Layer`, `Telegram Notifications`, `Arbitrage Scanner`, `API Error & Rate Monitoring`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `MongoLogger` connect `Bot Orchestration & Config` to `Trading Engine & Entry Pipeline`, `Strategy Base & Indicators`, `Logging & Data Persistence`, `A6 Performance Simulation`, `Exchange Client & API Layer`, `SQLite Trade/Rejection Queries`?**
  _High betweenness centrality (0.124) - this node is a cross-community bridge._
- **Are the 189 inferred relationships involving `Config` (e.g. with `PositionReviewer` and `Fetch trades marked as OPEN in SQLite, optionally since a specific date.`) actually correct?**
  _`Config` has 189 INFERRED edges - model-reasoned connections that need verification._
- **Are the 108 inferred relationships involving `MongoLogger` (e.g. with `PositionReviewer` and `Fetch trades marked as OPEN in SQLite, optionally since a specific date.`) actually correct?**
  _`MongoLogger` has 108 INFERRED edges - model-reasoned connections that need verification._
- **Are the 95 inferred relationships involving `TradeManager` (e.g. with `PositionSyncer` and `Fetch all trades marked as OPEN in SQLite`) actually correct?**
  _`TradeManager` has 95 INFERRED edges - model-reasoned connections that need verification._