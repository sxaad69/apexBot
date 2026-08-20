#!/usr/bin/env python3
"""
APEX HUNTER V14 - Main Trading Bot
Supports paper trading (simulation) and live trading
"""

import sys
import os
import time
import signal
import argparse
import uuid
from datetime import datetime, timedelta
import pandas as pd

from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5, StrategyA6, StrategyA7, StrategyA8
from notifications import TelegramNotificationManager
from risk import RiskManager
from core.spot_logger import SpotLogger
from core.spot_trading_engine import SpotTradingEngine
from risk.layers.trailing_stop import TrailingStopLayer
from risk.layers.portfolio_circuit_breaker import PortfolioCircuitBreaker
from risk.layers.portfolio_profit_ratchet import PortfolioProfitRatchet
from core.trade_manager import TradeManager
from core.market_data import MarketDataMixin
from core.exits import ExitsMixin
from core.entry import EntryMixin
from core.reporting import ReportingMixin
from core.sync import SyncMixin
from exchange.algo_orders import AlgoOrdersMixin
from exchange.wss_manager import BinanceFuturesWSSManager

class PaperTradingEngine(MarketDataMixin, AlgoOrdersMixin, ExitsMixin, EntryMixin, ReportingMixin):
    """
    Paper Trading Engine - Simulates trading with live market data
    Supports both Paper (Virtual) and Live (API) capital modes
    """

    def __init__(self, config, logger, telegram, mode='paper'):
        self.config = config
        self.logger = logger
        self.telegram = telegram
        self.mode = mode
        
        # Parallel Execution Architecture properties
        import threading
        self.recent_liquidations = {}
        self.symbol_loss_streak = {}  # C1: consecutive SL losses per symbol (re-entry blacklist)
        self.trade_lock = threading.Lock()

        # --- RATE-LIMIT FIX: OHLCV cache to avoid hammering Binance REST ---
        # Key: (symbol, timeframe). Value: pandas DataFrame of candles.
        # We only refetch once per timeframe window (candles are immutable
        # within their period) instead of re-downloading 300 candles for all
        # 100 symbols every sweep.
        self._ohlcv_cache = {}          # (symbol, tf) -> df
        self._ohlcv_cache_ts = {}       # (symbol, tf) -> last fetch epoch time

        # --- RATE-LIMIT FIX: Order status cache (Task 1.1) ---
        # check_exits() polls fetch_order(sl_id) + fetch_order(tp_id) every 0.5s
        # per open position (~1,200 calls/min with 5 positions). Orders don't
        # fill that fast, so we cache status for a short TTL.
        self._order_status_cache = {}   # order_id -> (status, timestamp)
        self._order_status_ttl = getattr(config, 'ORDER_STATUS_CACHE_TTL', 5.0)
        self._open_algo_cache = {}      # symbol -> (set_of_algo_ids, timestamp)

        # --- PHASE 2 FIX: OHLCV batch rotation (Task 2.3) ---
        # On cold start, all ~600 symbols' candles are stale. Refreshing them
        # all at once would burst the rate limit. We cap stale refreshes per
        # sweep at OHLCV_BATCH_MAX, rotating through the rest as TTLs expire.
        self._ohlcv_batch_count = 0
        self._ohlcv_batch_max = getattr(config, 'OHLCV_BATCH_MAX', 100)
        self._batch_cap_skipped_symbols = set()  # Track symbols skipped due to batch cap (reset each sweep)
        
        # Initialize WebSocket Manager for high-frequency price awareness
        is_testnet = getattr(config, 'EXCHANGE_ENVIRONMENT', 'live') == 'testnet'
        self.wss_manager = BinanceFuturesWSSManager(logger, testnet=is_testnet)

        # Initialize exchange for market data
        self.exchange = CCXTExchangeClient(config, logger, config.FUTURES_EXCHANGE)

        # Capital Initialization logic (Safety Integrity Fix: Sync balance BEFORE risk init)
        if self.mode == 'live':
            self.logger.info("Fetching REAL balance from exchange...")
            try:
                full_balance = self.exchange.get_balance()
                # Unified access for USDT balance across major exchanges
                self.total_capital = float(full_balance.get('USDT', {}).get('total', 0))
                
                if self.total_capital <= 0:
                    self.logger.warning("Real USDT balance is 0 or could not be fetched. Falling back to virtual.")
                    self.total_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
                else:
                    self.logger.info(f"💰 LIVE BALANCE SYNCED: ${self.total_capital:.2f} USDT")
            except Exception as e:
                self.logger.error(f"Failed to fetch real balance: {e}")
                self.total_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
        else:
            self.total_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)

        # Initialize Trade Manager (Centralized Entry/Exit Handler)
        self.trade_manager = TradeManager(self.config, self.logger.db, self.exchange, self.logger)
        # Convenience alias so _persist_tp_watermark / _persist_tp_update can call self.db directly
        self.db = self.logger.db

        # IMPORTANT: Update the config's primary Initial Capital for risk layers
        self.config.INITIAL_CAPITAL = self.total_capital

        # Initialize Profit Ratchet (Global Trailing Stop)
        self.profit_ratchet = PortfolioProfitRatchet(
            config, 
            self.logger.db if hasattr(self.logger, 'db') else None,
            self.exchange, 
            logger, 
            telegram,
            trade_manager=self.trade_manager,
            engine=self
        )

        # Initialize risk manager (11 layers) - Now accurately aware of capital and live ratchet
        self.risk_manager = RiskManager(config, logger, db_manager=logger.db if hasattr(logger, 'db') else None, profit_ratchet=self.profit_ratchet)

        # Initialize strategies
        self.strategies = []
        if hasattr(config, 'STRATEGY_A1_ENABLED') and config.STRATEGY_A1_ENABLED:
            self.strategies.append(StrategyA1(config, logger))
        if hasattr(config, 'STRATEGY_A2_ENABLED') and config.STRATEGY_A2_ENABLED:
            self.strategies.append(StrategyA2(config, logger))
        if hasattr(config, 'STRATEGY_A3_ENABLED') and config.STRATEGY_A3_ENABLED:
            self.strategies.append(StrategyA3(config, logger))
        if hasattr(config, 'STRATEGY_A4_ENABLED') and config.STRATEGY_A4_ENABLED:
            self.strategies.append(StrategyA4(config, logger))
        if getattr(self.config, 'STRATEGY_A5_ENABLED', False):
            self.strategies.append(StrategyA5(self.config, self.logger))
            
        if getattr(self.config, 'STRATEGY_A6_ENABLED', False):
            self.strategies.append(StrategyA6(self.config, self.logger))

        if getattr(self.config, 'STRATEGY_A7_ENABLED', False):
            self.strategies.append(StrategyA7(self.config, self.logger))

        if getattr(self.config, 'STRATEGY_A8_ENABLED', False):
            self.strategies.append(StrategyA8(self.config, self.logger))

        # If no strategies explicitly enabled, enable all including A6
        if not self.strategies:
            self.strategies = [
                StrategyA1(config, logger),
                StrategyA2(config, logger),
                StrategyA3(config, logger),
                StrategyA4(config, logger),
                StrategyA5(config, logger),
                StrategyA6(config, logger),
                StrategyA7(config, logger),
                StrategyA8(config, logger)
            ]
        
        # Inject exchange client into strategies for microstructure analysis (A5)
        
        # (Initialization moved up to inject into RiskManager)
        for strategy in self.strategies:
            strategy.exchange_client = self.exchange
        
        # Virtual positions (key: "strategy_name:symbol" -> position_data)
        self.positions = {}

        # Cache for top pairs
        self.top_pairs_cache = []
        self.last_pairs_update = None

        # Peak balance tracking for drawdown
        self.peak_balance = self.total_capital

        # Performance tracking
        self.trades = []

        # Current market prices tracker for dashboard
        self.current_prices = {}

        # Hourly Telegram reporting system
        self.hourly_reports_enabled = getattr(self.config, 'TELEGRAM_ENABLE_HOURLY_REPORTS', True)
        self.report_interval_hours = getattr(self.config, 'TELEGRAM_REPORT_INTERVAL_HOURS', 1)
        self.last_report_time = datetime.now()
        self.hourly_metrics = {
            'futures': {
                'total_analyses': 0,
                'signals_generated': 0,
                'total_rejections': 0,
                'trades_opened': 0
            },
            'spot': {
                'total_analyses': 0,
                'signals_generated': 0,
                'total_rejections': 0,
                'trades_opened': 0
            },
            'arbitrage': {
                'total_analyses': 0,
                'opportunities_found': 0,
                'trades_executed': 0,
                'total_rejections': 0
            }
        }

        self.logger.info(f"Paper trading initialized with {len(self.strategies)} strategies")
        self.logger.info(f"Initial capital: ${self.total_capital} per strategy")
        self.logger.info(f"Risk management: 11 layers active")

        # Deduplication for Telegram notifications
        self.recent_exit_notifications = {}

    def run_cycle(self, symbol='BTC/USDT', global_positions=None, exit_only=False, entry_only=False):
        """Run one trading cycle for a specific symbol"""
        mark_price = None
        # --- PHASE 1: HIGH-SPEED VIRTUAL EXITS ---
        if not entry_only:
            # 100% latency-free stop loss checks using the global markPrice
            if global_positions is not None:
                # Match strict symbol ("BTC/USDT") or Binance format ("BTCUSDT")
                matched_pos = next((p for p in global_positions if p.get('symbol') == symbol or p.get('info', {}).get('symbol') == symbol.replace('/', '')), None)
                if matched_pos:
                    mark_price = float(matched_pos.get('markPrice', matched_pos.get('info', {}).get('markPrice', 0)))

            # [FIX 1] Paper mode fallback: Sentinel thread cannot get markPrice from exchange
            # in paper mode (no real positions on Binance). Use price already cached by
            # the main entry loop so the Sentinel can still evaluate stop-loss/take-profit.
            if not mark_price and self.mode == 'paper':
                mark_price = self.current_prices.get(symbol)

            if mark_price and mark_price > 0:
                self.current_prices[symbol] = mark_price
                
                # Fireboard Isolation for Exits
                try:
                    self.update_trailing_stops(symbol, mark_price)
                    self.update_trailing_tier(symbol, mark_price)
                    self.update_trailing_take_profit(symbol, mark_price)
                    self.check_exits(symbol, mark_price)
                except Exception as e:
                    self.logger.error(f"🚨 Module Isolation: Exit calculation failed for {symbol}: {e}", exc_info=True)

        if exit_only:
            return

        # --- PHASE 2: ALGORITHMIC STRATEGY ENTRY ---
        # Fetch market data (Blocks on API Call)
        df = self.fetch_market_data(symbol)

        if df is None or len(df) == 0:
            # Check if this was a batch-cap skip (expected during cold start) vs genuine failure
            if symbol in self._batch_cap_skipped_symbols:
                self.logger.debug(f"Batch-cap skip: {symbol} (will retry next sweep)")
            else:
                self.logger.error(f"Genuinely failed to fetch market data for {symbol}")
            return

        current_price = df.iloc[-1]['close']
        self.current_prices[symbol] = current_price

        # Fallback check exits just in case global polling missed it or it wasn't open yet
        if not mark_price and not entry_only:
            try:
                self.update_trailing_stops(symbol, current_price)
                self.update_trailing_tier(symbol, current_price)
                self.update_trailing_take_profit(symbol, current_price)
                self.check_exits(symbol, current_price)
            except Exception as e:
                self.logger.error(f"🚨 Module Isolation: Fallback Exit calculation failed for {symbol}: {e}", exc_info=True)

        # Check for new signals
        # Only if not in circuit breaker cooldown
        in_cooldown = False
        rejections = {}
        
        if hasattr(self, 'portfolio_circuit_breaker') and self.portfolio_circuit_breaker:
            in_cooldown = self.portfolio_circuit_breaker.is_in_cooldown()
            
        if not in_cooldown:
            for strategy in self.strategies:
                position_key = f"{strategy.name}:{symbol}"

                # Skip if strategy already has open position for this symbol
                if position_key in self.positions:
                    continue

                # Clear previous rejection
                strategy.last_rejection = None
                
                # Generate signal
                import inspect
                kwargs = {}
                sig = inspect.signature(strategy.generate_signal)
                if 'symbol' in sig.parameters:
                    kwargs['symbol'] = symbol
                if 'market_type' in sig.parameters:
                    kwargs['market_type'] = 'futures'
                
                signal = strategy.generate_signal(df, **kwargs)

                if signal:
                    self.execute_entry(signal, strategy.name, symbol)
                elif hasattr(strategy, 'last_rejection') and strategy.last_rejection:
                    rejections[strategy.name] = strategy.last_rejection

        # Collect market analysis data for dashboard (Bulk Aggregation)
        collected_data = self._collect_market_analysis_data(symbol, df, current_price)

        return {'symbol': symbol, 'rejections': rejections, 'collected_data': collected_data}

    def _collect_market_analysis_data(self, symbol, df, current_price):
        """Collect and save market analysis data for dashboard"""
        try:
            # Get current date and hour
            now = datetime.now()
            current_date = now.strftime('%Y-%m-%d')
            current_hour = now.strftime('%H:00')

            # Count total analyses performed this hour
            total_analyses = 0
            futures_analyses = 0
            spot_analyses = 0
            pairs_analyzed = set([symbol])  # Start with current pair
            strategies_active = [s.name for s in self.strategies]

            # Count signals generated this hour
            strategy_signals = {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0}

            # Detailed rejection tracking
            filter_rejections = {
                'volume': [],
                'adx': [],
                'volatility': [],
                'other': []
            }

            # Import strategy filters to get detailed rejection reasons
            from strategies.filters import get_strategy_filters
            strategy_filters = get_strategy_filters(self.config)

            # Generate signals for each strategy to count them and capture rejections
            for strategy in self.strategies:
                total_analyses += 1
                futures_analyses += 1

                # Check filters first (this captures detailed rejection reasons)
                should_trade, filter_reason = strategy_filters.should_trade_symbol(df, symbol, strategy.name)

                if not should_trade:
                    # Categorize rejection reason
                    if 'Volume <' in filter_reason and 'x average' in filter_reason:
                        filter_rejections['volume'].append({
                            'strategy': strategy.name,
                            'symbol': symbol,
                            'reason': filter_reason,
                            'timestamp': now
                        })
                    elif 'ADX <' in filter_reason:
                        filter_rejections['adx'].append({
                            'strategy': strategy.name,
                            'symbol': symbol,
                            'reason': filter_reason,
                            'timestamp': now
                        })
                    elif 'Volatility >' in filter_reason:
                        filter_rejections['volatility'].append({
                            'strategy': strategy.name,
                            'symbol': symbol,
                            'reason': filter_reason,
                            'timestamp': now
                        })
                    else:
                        filter_rejections['other'].append({
                            'strategy': strategy.name,
                            'symbol': symbol,
                            'reason': filter_reason,
                            'timestamp': now
                        })

                    # Log the filter rejection (same as current logging)
                    self.logger.debug(f"[{strategy.name}] {symbol} FILTERED: {filter_reason}")
                    continue

                # Generate signal only if filters pass
                import inspect
                kwargs = {}
                sig = inspect.signature(strategy.generate_signal)
                if 'symbol' in sig.parameters:
                    kwargs['symbol'] = symbol
                if 'market_type' in sig.parameters:
                    kwargs['market_type'] = 'futures'
                    
                signal = strategy.generate_signal(df, **kwargs)
                if signal:
                    strategy_name = signal.get('strategy', strategy.name)
                    if strategy_name in strategy_signals:
                        strategy_signals[strategy_name] += 1

            # Calculate rejection counts (detailed + legacy aggregate)
            volume_rejections = len(filter_rejections['volume'])
            adx_rejections = len(filter_rejections['adx'])
            volatility_rejections = len(filter_rejections['volatility'])
            other_rejections = len(filter_rejections['other'])
            total_rejections = volume_rejections + adx_rejections + volatility_rejections + other_rejections

            # Calculate metrics
            signals_generated = sum(strategy_signals.values())
            conversion_rate = (signals_generated / max(total_analyses, 1) * 100)

            # Prepare market analysis data
            analysis_data = {
                'date': current_date,
                'hour': current_hour,
                'trading_type': 'futures',  # This is futures trading engine
                'total_analyses': total_analyses,
                'futures_analyses': futures_analyses,
                'spot_analyses': spot_analyses,
                'pairs_analyzed': list(pairs_analyzed),
                'strategies_active': strategies_active,
                'current_price': current_price,
                'timestamp': now
            }

            # Prepare hourly metrics data (with detailed rejections)
            metrics_data = {
                'date': current_date,
                'hour': current_hour,
                'trading_type': 'futures',
                'signals_generated': signals_generated,
                'trades_executed': len([p for p in self.positions.values() if p['symbol'] == symbol]),  # Current positions
                'volume_rejections': volume_rejections,
                'adx_rejections': adx_rejections,
                'volatility_rejections': volatility_rejections,
                'other_rejections': other_rejections,
                'total_rejections': total_rejections,
                'conversion_rate': conversion_rate,
                'detailed_rejections': filter_rejections,  # Full detailed rejection data
                'timestamp': now
            }

            # Return data for bulk aggregation instead of saving immediately
            return {
                'analysis': analysis_data,
                'metrics': metrics_data
            }

        except Exception as e:
            self.logger.error(f"Error collecting market analysis data: {e}")
            return None

class ApexHunterBot(SyncMixin):
    """Main bot orchestrator"""
    
    def __init__(self, mode=None):
        self.running = False
        
        print("=" * 80)
        print("  APEX HUNTER V14 - V14.1-FIXED-LOGGING")
        print("=" * 80)
        print()
        
        # Load configuration
        print("⚙️  Loading configuration...")
        self.config = Config()
        self.logger = MongoLogger(self.config)
        
        # Centralized Mode Management: Prioritize config, ignore CLI if provided
        self.mode = self.config.TRADING_MODE
        if self.mode == 'simulation': self.mode = 'paper' # Standardize internally

        # Handle cleanup operations
        self._handle_cleanup()

        # Initialize Telegram
        print("📱 Initializing Telegram bots...")
        self.telegram = TelegramNotificationManager(self.config, self.logger)

        # Initialize trading engine
        if self.mode == 'paper':
            print("🎮 Initializing PAPER TRADING mode...")
        else:
            print("🚀 Initializing LIVE TRADING mode...")

        self.engine = PaperTradingEngine(self.config, self.logger, self.telegram, mode=self.mode)
        self.logger.engine = self.engine

        # Publish A6's shared orderbook feed to the engine so composite strategies
        # (A8) and forensics can read the same real-time WSS book. A6 publishes at
        # init but logger.engine isn't set yet then; this re-binds after wiring.
        try:
            for strat in self.engine.strategies:
                if hasattr(strat, 'latest_orderbooks'):
                    self.engine.latest_orderbooks = strat.latest_orderbooks
                    break
        except Exception:
            pass
        
        # --- PERPETUAL STATE RECOVERY (Phase 14) ---
        # Recover peak_balance and total_capital from SQLite if they exist
        if hasattr(self.logger, 'db'):
            stored_peak = self.logger.db.get_setting('peak_balance')
            if stored_peak:
                self.engine.peak_balance = float(stored_peak)
                self.engine.risk_manager.update_peak_balance(self.engine.peak_balance)
                print(f"📈 Recovered Peak Balance: ${self.engine.peak_balance:.2f}")
            
            # For paper mode, we might want to recover total_capital too to avoid "resetting" on crash
            if self.mode == 'paper':
                stored_capital = self.logger.db.get_setting('paper_total_capital')
                if stored_capital:
                    self.engine.total_capital = float(stored_capital)
                    print(f"💰 Recovered Paper Capital: ${self.engine.total_capital:.2f}")

        # --- STATE HYDRATION (Phase 14) ---
        # Load active positions from SQLite into memory immediately
        self._sync_open_trades()

        pairs_config = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])
 
        if self.mode == 'live':
            print(f"💰 LIVE TRADING ACTIVE (Shared Pool Sync)")
        else:
            print(f"🧪 PAPER TRADING ACTIVE (Virtual Capital: ${self.engine.total_capital:.2f})")

        # Initialize Bot-Side Trailing Stop Engine
        if hasattr(self, 'engine'):
            self.trailing_stop_engine = TrailingStopLayer(
                self.config,
                self.logger,
                self.logger.db,
                self.engine.exchange,
                engine=self.engine,
                trade_manager=self.engine.trade_manager
            )
            # Inject into engine so check_exits() can reach it
            self.engine.trailing_stop_engine = self.trailing_stop_engine
        else:
            self.trailing_stop_engine = None
            
        # Initialize Portfolio Loss Circuit Breaker
        if hasattr(self, 'engine'):
            self.portfolio_circuit_breaker = PortfolioCircuitBreaker(
                self.config, self.logger.db, self.logger
            )
            # Inject into engine so it can check cooldowns
            self.engine.portfolio_circuit_breaker = self.portfolio_circuit_breaker
        else:
            self.portfolio_circuit_breaker = None
            
        # Send startup message
        if self.telegram:
            self.telegram.send_startup_message()

        print()
        print("=" * 80)
        print("  BOT STARTED - Press Ctrl+C to stop")
        print("=" * 80)
        print()

    def _handle_cleanup(self):
        """Handle cleanup operations based on environment variables"""
        clean_logs = getattr(self.config, 'CLEAN_LOGS', False)
        clean_db = getattr(self.config, 'CLEAN_DB', False)
        clean_telegram = getattr(self.config, 'CLEAN_TELEGRAM', False)

        if clean_logs or clean_db or clean_telegram:
            print("🧹 Starting cleanup operations...")

        # Wipe Telegram messages (if history exists)
        if clean_telegram and self.telegram:
            self.telegram.wipe_all_messages()

        # Clean log files
        if clean_logs:
            self._clean_log_files()

        # Clean database files
        if clean_db:
            self._clean_database_files()

        if clean_db:
            self._clean_database_files()

        if clean_logs or clean_db or clean_telegram:
            print("✅ Cleanup operations completed")
            print()

    def _clean_log_files(self):
        """Clean all log files in the logs directory"""
        import os
        import shutil
        from pathlib import Path

        logs_dir = Path(getattr(self.config, 'LOG_FILE_PATH', './logs'))

        if not logs_dir.exists():
            print("⚠️  Logs directory not found, skipping log cleanup")
            return

        # Find all log files
        log_files = list(logs_dir.glob("*.log"))

        if not log_files:
            print("ℹ️  No log files found to clean")
            return

        print(f"🗑️  Cleaning {len(log_files)} log files...")

        # Delete all log files
        for log_file in log_files:
            try:
                log_file.unlink()
                print(f"   Deleted: {log_file.name}")
            except Exception as e:
                print(f"   Error deleting {log_file.name}: {e}")

        print("✅ Log cleanup completed")

    def _clean_database_files(self):
        """Clean all JSON database files"""
        from pathlib import Path

        data_dir = Path("data")

        if not data_dir.exists():
            print("⚠️  Data directory not found, skipping database cleanup")
            return

        # JSON files to clean
        json_files = [
            "futures_trades.json",
            "spot_signals.json",
            "arbitrage_opportunities.json",
            "trailing_stops.json",
            "risk_rejections.json",
            "system_logs.json"
        ]

        cleaned_count = 0
        for json_file in json_files:
            file_path = data_dir / json_file
            if file_path.exists():
                try:
                    file_path.unlink()
                    print(f"   Deleted: {json_file}")
                    cleaned_count += 1
                except Exception as e:
                    print(f"   Error deleting {json_file}: {e}")

        if cleaned_count == 0:
            print("ℹ️  No database files found to clean")
        else:
            print(f"✅ Database cleanup completed ({cleaned_count} files)")
    
    def _run_priority_exit_thread(self):
        """Continuous dedicated Risk Engine thread guarantees 0ms stops using WebSocket data."""
        import time
        self.logger.info("🛡️ Priority Exit Sentinel Thread activated (WebSocket Mode).")
        # Sentinel Telemetry Tracking
        last_telemetry = 0

        while self.running:
            try:
                # 1. Fetch Global Data for strict sync if needed
                global_positions = None
                if self.mode == 'live' or getattr(self.config, 'FUTURES_STRICT_SYNC', False):
                    try:
                        ex_positions = self.engine.exchange.get_positions()
                        global_positions = [p for p in ex_positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                    except: pass

                # 2. Iterate Memory instantly
                active_symbols = list(set([p['symbol'] for p in self.engine.positions.values()]))

                # Periodic Telemetry (every 10s)
                import time
                now = time.time()
                show_telemetry = now - last_telemetry > 10
                if show_telemetry and active_symbols:
                    self.logger.info(f"🛡️ Sentinel Monitoring: {len(active_symbols)} symbols via WSS Feed.")
                    last_telemetry = now

                # --- IP BAN PAUSE ---
                # While Binance is cooling down, skip the per-symbol exit polls too
                # (positions are protected by exchange-side algo orders). The WSS
                # mark-price stream still updates live_prices, so when the cooldown
                # clears we resume with fresh prices and check_exits immediately.
                if getattr(self.engine.exchange, '_is_banned', lambda: False)():
                    if show_telemetry:
                        self.logger.warning(
                            f"🚫 Rate-limit cooldown active — pausing sentinel exit polls "
                            f"(exchange-side SL/TP still protecting {len(active_symbols)} positions)."
                        )
                    time.sleep(5.0)
                    continue
                
                for symbol in active_symbols:
                    if not self.running: break
                    try:
                        # Get real-time price from WSS Manager
                        mark_price = self.engine.wss_manager.live_prices.get(symbol)
                        
                        # Fallback to ticker if WSS hasn't received update yet
                        if not mark_price:
                            ticker = self.engine.exchange.get_ticker(symbol)
                            mark_price = ticker.get('last') if ticker else None
                        
                        if mark_price:
                            if show_telemetry:
                                self.logger.debug(f"🔍 [WSS] {symbol}: {mark_price}")
                            self.engine.current_prices[symbol] = mark_price
                            # TrailingStopLayer (System B) inside check_exits is the
                            # sole trailing stop engine — fee floor + exchange orders + history.
                            self.engine.check_exits(symbol, mark_price)
                            
                    except Exception as e:
                        self.logger.error(f"🚨 Priority Exit failure for {symbol}: {e}")
                        
            except Exception as e:
                self.logger.error(f"Priority Exit Thread Error: {e}")
            
            # High-frequency tick (500ms) for ultra-fast reaction
            time.sleep(0.5)

    def _seed_symbol_loss_streak(self):
        """D4: seed the in-memory C1 re-entry blacklist from the DB at startup so a
        restart doesn't wipe a symbol's consecutive-SL-loss streak (MORPHO/TA/Q pattern)."""
        try:
            db = getattr(self, 'db', None)
            if not db:
                return
            conn = db._get_connection(db.main_db)
            try:
                rows = conn.execute(
                    "SELECT symbol, reason, pnl_amount FROM trades "
                    "WHERE status='CLOSED' ORDER BY exit_time DESC"
                ).fetchall()
            finally:
                conn.close()
            streak = {}
            for row in rows:
                sym = (row['symbol'] or '').split('/')[0]
                if not sym:
                    continue
                if 'stop_loss' in str(row['reason'] or '').lower():
                    streak[sym] = streak.get(sym, 0) + 1
                elif row['pnl_amount'] and row['pnl_amount'] > 0:
                    streak[sym] = 0  # win resets the consecutive streak
            self.symbol_loss_streak = streak
            seeded = {k: v for k, v in streak.items() if v > 0}
            if seeded:
                self.logger.info(f"[D4] Seeded symbol loss-streak blacklist from DB: {seeded}")
        except Exception as e:
            self.logger.warning(f"[D4] Failed to seed symbol loss-streak from DB: {e}")

    def _run_periodic_sweep(self):
        """B2: periodically sweep orphaned Algo API orders (SL/trailing/TP) on
        symbols with no live exchange position. Standard cancel_all_orders does NOT
        touch Algo API orders, so orphans would otherwise accumulate on closed symbols."""
        import time as _t
        interval = getattr(self.config, 'ORPHAN_SWEEP_INTERVAL', 300.0)
        self.logger.info(f"🧹 Orphan Algo sweep thread started (every {interval:.0f}s).")
        while self.running:
            try:
                # live positions -> symbols to protect (their algos are legitimate)
                try:
                    live = self.engine.exchange.get_positions()
                    protected = set(p['symbol'] for p in live if abs(float(p.get('contracts', 0) or 0)) > 0)
                except Exception:
                    protected = set()
                try:
                    resp = self.engine.exchange.exchange.fapiPrivateGetOpenAlgoOrders()
                    orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
                    algo_by_sym = {}
                    for o in orders:
                        s = o.get('symbol')
                        if s:
                            algo_by_sym.setdefault(s, []).append(o.get('algoId'))
                    for s, ids in algo_by_sym.items():
                        canonical = s + '/USDT:USDT' if not s.endswith(':USDT') else s
                        if canonical in protected:
                            continue
                        for algo_id in ids:
                            try:
                                self.engine.exchange.exchange.fapiPrivateDeleteAlgoOrder({'symbol': s, 'algoId': algo_id})
                            except Exception:
                                pass
                        if ids:
                            self.logger.info(f"🧹 [B2] Swept {len(ids)} orphan algo order(s) for {s}")
                except Exception as e:
                    self.logger.warning(f"⚠️ [B2] periodic orphan sweep error: {e}")
                # Paper signal resolution: resolve open paper signals against current mark prices
                try:
                    has_paper = any(
                        getattr(self.config, f'STRATEGY_{s.name.split(":")[0].strip()}_PAPER', False)
                        for s in self.strategies
                    )
                    if has_paper and hasattr(self.logger, 'db'):
                        marks = {}
                        for sym, price in self.engine.current_prices.items():
                            marks[sym] = price
                        resolved = self.logger.db.resolve_paper_signals(marks)
                        if resolved:
                            self.logger.info(f"📋 [PAPER] Resolved {resolved} paper signal(s)")
                except Exception as e:
                    self.logger.debug(f"📋 [PAPER] resolve error: {e}")
            except Exception as e:
                self.logger.warning(f"⚠️ [B2] sweep thread error: {e}")
            _t.sleep(interval)

    def run(self, interval=60):
        """Run the bot"""
        self.running = True

        # D4: seed the in-memory C1 re-entry blacklist from DB so streaks survive restarts
        try:
            self._seed_symbol_loss_streak()
        except Exception as e:
            self.logger.warning(f"[D4] seed symbol loss-streak failed: {e}")

        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)

        # --- [PHASE: PROFIT RATCHET BACKGROUND MONITOR] ---
        # Since the bot is synchronous, we run the Async Ratchet Monitor in a background thread
        import threading
        import asyncio

        def run_ratchet_monitor(ratchet):
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(ratchet.monitor_loop())
            except Exception as e:
                print(f"🚨 Ratchet Monitor Thread Error: {e}")
            finally:
                loop.close()

        self.ratchet_thread = None
        if self.config.PROFIT_RATCHET_ENABLED:
            self.ratchet_thread = threading.Thread(
                target=run_ratchet_monitor,
                args=(self.engine.profit_ratchet,),
                name="RatchetMonitor",
                daemon=True  # Dies automatically when main process exits
            )
            self.ratchet_thread.start()
            self.logger.info("📡 Portfolio Profit Ratchet Monitor started in background thread.")
        else:
            self.logger.info("🔇 Portfolio Profit Ratchet disabled via config. Skipping monitor.")

        # Start WebSocket Manager
        self.engine.wss_manager.start()

        self.sentinel_thread = threading.Thread(
            target=self._run_priority_exit_thread,
            name="PriorityExitSentinel",
            daemon=True
        )
        self.sentinel_thread.start()

        # B2: periodic orphan-algo sweep (separate thread, low frequency)
        self.sweep_thread = threading.Thread(
            target=self._run_periodic_sweep,
            name="OrphanAlgoSweep",
            daemon=True
        )
        self.sweep_thread.start()

        # Startup reconciliation handled in __init__ (Phase 14)

        try:
            while self.running:
                # Get trading pairs (dynamic or static)
                pairs_config = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])

                # Check if auto mode
                if isinstance(pairs_config, str) and pairs_config.lower() == 'auto':
                    top_n = int(getattr(self.config, 'FUTURES_AUTO_TOP_N', 30))
                    min_volume = float(getattr(self.config, 'FUTURES_AUTO_MIN_VOLUME', 1000000))
                    pairs = self.engine.get_top_pairs_by_volume(top_n=top_n, min_volume_usdt=min_volume)
                elif isinstance(pairs_config, str):
                    # Parse comma-separated string
                    pairs = [p.strip() for p in pairs_config.split(',')]
                else:
                    pairs = pairs_config

                # --- GLOBAL EXCHANGE STATE FETCH (Exchange-First Architecture) ---
                global_positions = None
                # --- DISCOVERY LOOP (Concurrent sweeps) ---
                import concurrent.futures
                import time
                max_workers = getattr(self.config, 'DISCOVERY_MAX_WORKERS', 5)

                # --- LIVE BALANCE SYNC ---
                if self.mode == 'live':
                    try:
                        full_balance = self.engine.exchange.get_balance()
                        self.engine.total_capital = float(full_balance.get('USDT', {}).get('total', 0))
                    except Exception as e:
                        self.logger.warning(f"⚠️ Live balance sync failed during sweep: {e}")

                # --- PHASE 2 FIX (Task 2.3): Reset OHLCV batch rotation counter ---
                # Each sweep resets the cap so different symbols rotate through
                # refreshes instead of the same first-100 getting priority forever.
                self.engine._ohlcv_batch_count = 0
                self.engine._batch_cap_skipped_symbols.clear()  # Reset skip tracking

                # --- IP BAN CIRCUIT BREAKER ---
                # If Binance is cooling us down (429 backoff or -1003 ban), abort the
                # entire sweep instead of submitting hundreds of run_cycle jobs that
                # each try fetch_market_data. The per-call ban gate alone isn't enough:
                # with 5 workers draining a 480-symbol queue, blocked calls churn through
                # the queue, and once the short backoff expires the sweep immediately
                # resumes hammering — re-tripping the ban within the same sweep.
                if getattr(self.engine.exchange, '_is_banned', lambda: False)():
                    self.logger.warning(
                        f"🚫 Rate-limit cooldown active — skipping this sweep "
                        f"(resumes when the cooldown clears)."
                    )
                    continue

                sweep_start_time = time.time()
                sweep_stats = {
                    'symbols_scanned': 0,
                    'entries_executed': 0,
                    'strategy_rejections': {},
                    'risk_rejections': {}
                }
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = []
                    for symbol in pairs:
                        if not self.running:
                            break
                        # Feed global positions but restrict to entry processing only
                        future = executor.submit(self.engine.run_cycle, symbol, global_positions=global_positions, entry_only=True)
                        futures.append(future)
                        
                    # Wait for completion of this concurrent batch to prevent unbounded memory growth
                    concurrent.futures.wait(futures)

                    # Aggregate results
                    bulk_analysis = []

                    bulk_metrics = {'signals_generated': 0, 'trades_executed': 0, 'total_rejections': 0}
                    last_metrics_info = None

                    for future in futures:
                        try:
                            result = future.result()
                            if result and isinstance(result, dict):
                                sym = result.get('symbol')
                                
                                # Aggregate rejections for sweep summary
                                if 'rejections' in result:
                                    sweep_stats['symbols_scanned'] += 1
                                    for strategy_name, rejection in result['rejections'].items():
                                        if strategy_name not in sweep_stats['strategy_rejections']:
                                            sweep_stats['strategy_rejections'][strategy_name] = {}
                                        # rejection is now {"reason": ..., ...details} or legacy string
                                        if isinstance(rejection, dict):
                                            reason = rejection.get('reason', 'UNKNOWN')
                                            details = {k: v for k, v in rejection.items() if k != 'reason'}
                                        else:
                                            reason = rejection
                                            details = {}
                                        if reason not in sweep_stats['strategy_rejections'][strategy_name]:
                                            sweep_stats['strategy_rejections'][strategy_name][reason] = {}
                                        sweep_stats['strategy_rejections'][strategy_name][reason][sym] = details
                                
                                # Aggregate bulk DB data
                                collected = result.get('collected_data')
                                if collected:
                                    if collected.get('analysis'):
                                        bulk_analysis.append(collected['analysis'])

                                    if collected.get('metrics'):
                                        m = collected['metrics']
                                        bulk_metrics['signals_generated'] += m.get('signals_generated', 0)
                                        bulk_metrics['trades_executed'] += m.get('trades_executed', 0)
                                        bulk_metrics['total_rejections'] += m.get('total_rejections', 0)
                                        last_metrics_info = m
                        except Exception as e:
                            self.logger.error(f"Error getting future result: {e}")
                            
                    # Bulk Save to Database
                    # [DISABLED] market_analysis is redundant and causes high CPU/disk I/O. 
                    # All data is already captured in trades + rejections + sweep_summary.
                    # if bulk_analysis and hasattr(self.logger, 'save_market_analysis_bulk'):
                    #     self.logger.save_market_analysis_bulk(bulk_analysis)
                        
                    if last_metrics_info and hasattr(self.logger, 'save_hourly_metrics'):
                        unified_metrics = last_metrics_info.copy()
                        unified_metrics.update(bulk_metrics)
                        self.logger.save_hourly_metrics(
                            unified_metrics['date'], 
                            unified_metrics['hour'], 
                            unified_metrics
                        )
                            
                sweep_stats['duration_sec'] = time.time() - sweep_start_time
                # Track batch-cap skips for visibility
                sweep_stats['batch_cap_skipped'] = len(self.engine._batch_cap_skipped_symbols)
                if sweep_stats['batch_cap_skipped'] > 0:
                    self.logger.debug(
                        f"Batch-cap skipped {sweep_stats['batch_cap_skipped']} symbols "
                        f"(will rotate in next sweep)"
                    )
                
                # Check if it's time to send hourly report
                self.engine._check_and_send_hourly_report()

                # Save current active positions for dashboard with live prices
                self.logger.save_active_positions(self.engine.positions, self.engine.current_prices)

                # Log status with high-precision price and per-position profit %
                total_pnl = self.engine.total_capital - getattr(self.config, 'INITIAL_CAPITAL', 100)
                open_positions = len(self.engine.positions)
                self.logger.info(f"Sweep Complete | Open: {open_positions} | Total P&L: ${total_pnl:+.2f}")
                
                # Log the sweep summary to DB
                if hasattr(self.logger, 'log_sweep_summary'):
                    self.logger.log_sweep_summary(sweep_stats)

                # Run spot analysis if enabled
                try:
                    if hasattr(self, 'spot_engine') and self.spot_engine:
                        # Use spot trading engine for full simulation
                        spot_pairs = getattr(self.config, 'SPOT_PAIRS', 'BTC/USDT,ETH/USDT,SOL/USDT')
                        if isinstance(spot_pairs, str):
                            spot_pairs = [p.strip() for p in spot_pairs.split(',')]

                        for symbol in spot_pairs:
                            if not self.running:
                                break
                            self.logger.debug(f"Running spot cycle for {symbol}")
                            self.spot_engine.run_cycle(symbol)

                    elif hasattr(self, 'spot_logger') and self.spot_logger:
                        # Fallback to spot logger for signal logging only
                        spot_pairs = getattr(self.config, 'SPOT_PAIRS', 'BTC/USDT,ETH/USDT,SOL/USDT')
                        if isinstance(spot_pairs, str):
                            spot_pairs = [p.strip() for p in spot_pairs.split(',')]

                        for symbol in spot_pairs:
                            if not self.running:
                                break
                            self._run_spot_cycle(symbol)
                except Exception as e:
                    self.logger.error(f"🚨 CRITICAL: Spot Engine Analysis Failed", exc_info=True)

                # Trailing stops are now processed synchronously in the PriorityExitSentinel thread.

                # Process Portfolio Loss Circuit Breaker
                try:
                    if getattr(self, 'portfolio_circuit_breaker', None) and hasattr(self, 'engine'):
                        self.portfolio_circuit_breaker.check_and_trigger(
                            self.engine.positions, self.engine.current_prices
                        )
                except Exception as e:
                    self.logger.error(f"🚨 CRITICAL: Portfolio Circuit Breaker Failed", exc_info=True)

                # Wait before next cycle
                if self.running:
                    time.sleep(interval)

        except KeyboardInterrupt:
            self.logger.info("Bot stopped by user.")
        except Exception as e:
            self.logger.critical(f"FATAL BOT CRASH: {e}", exc_info=True)
            raise e
        finally:
            self.shutdown()
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signal"""
        print("\n\n⚠️  Shutdown signal received...")
        self.running = False
    
    def _run_spot_cycle(self, symbol: str):
        """Run spot analysis cycle for a symbol"""
        try:
            # Fetch market data using spot exchange
            ohlcv = self.spot_logger.exchange.exchange.fetch_ohlcv(symbol, '15m', limit=200)
            if not ohlcv:
                return

            import pandas as pd
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            if df.empty:
                return

            current_price = df.iloc[-1]['close']

            # Generate signals using same strategies (but without leverage)
            for strategy in self.engine.strategies:
                # Use same strategy logic but adapt for spot (no leverage)
                import inspect
                kwargs = {}
                sig = inspect.signature(strategy.generate_signal)
                if 'symbol' in sig.parameters:
                    kwargs['symbol'] = symbol
                if 'market_type' in sig.parameters:
                    kwargs['market_type'] = 'spot'
                    
                signal = strategy.generate_signal(df, **kwargs)

                if signal:
                    # Adapt signal for spot (remove leverage, adjust stops)
                    spot_signal = {
                        'symbol': signal['symbol'],
                        'side': signal['side'],
                        'price': signal['entry_price'],  # Current price for spot
                        'entry_price': signal['entry_price'],
                        'stop_loss': signal.get('stop_loss'),
                        'take_profit': signal.get('take_profit'),
                        'strategy': f"SPOT-{signal['strategy']}",
                        'confidence': signal.get('confidence', 0.5)
                    }

                    # Process through spot logger
                    self.spot_logger.process_signal(spot_signal)

            # Log spot analysis status
            self.logger.debug(f"SPOT {symbol} | Price: ${current_price:.2f} | Signals: {len(self.spot_logger.signals_today) if hasattr(self.spot_logger, 'signals_today') else 0}")

        except Exception as e:
            self.logger.error(f"Error in spot analysis for {symbol}: {e}")

    def shutdown(self):
        """Graceful shutdown"""
        print("\n🛑 Shutting down...")

        # Signal the ratchet monitor to stop cleanly before the loop closes
        if hasattr(self, 'engine') and hasattr(self.engine, 'profit_ratchet'):
            self.engine.profit_ratchet.stop_event.set()
            self.engine.wss_manager.stop()
            self.logger.info("🛡️ Ratchet monitor and WSS Manager stop signal sent.")

        # Print summary
        if hasattr(self.engine, 'print_summary'):
            self.engine.print_summary()

        self.logger.info("Bot stopped")
        print("✅ Bot stopped successfully")

def main():
    parser = argparse.ArgumentParser(description='APEX HUNTER V14 Trading Bot')
    parser.add_argument('--mode', type=str, choices=['paper', 'live'],
                        help='Trading mode (Deprecated: Use .env TRADING_MODE instead)')
    parser.add_argument('--interval', type=int, default=60,
                        help='Check interval in seconds (default: 60)')
    
    args = parser.parse_args()
    
    # Create bot instance (it will load its own config)
    bot = ApexHunterBot()
    
    # Verify mode from config for safety prompt
    if bot.mode == 'live':
        # Allow headless AWS/Docker deployments to bypass the prompt via env var
        auto_confirm = os.environ.get('APEX_CONFIRM_LIVE', '').strip().upper()
        if auto_confirm == 'YES':
            print("\n✅ LIVE TRADING MODE - Auto-confirmed via APEX_CONFIRM_LIVE env var (headless mode)")
        else:
            print("\n⚠️  WARNING: LIVE TRADING MODE!")
            print("   Real money will be at risk!")
            print()
            confirm = input("Are you sure? Type 'YES' to continue: ")
            if confirm != 'YES':
                print("Aborted.")
                sys.exit(0)
    
    # Run bot
    bot.run(interval=args.interval)

if __name__ == "__main__":
    main()
