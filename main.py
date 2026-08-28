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
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5, StrategyA6, StrategyA7, StrategyA8, StrategyA9
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
        self._momentum_pending = {}   # Phase-1: momentum gate — "strategy:symbol" -> pending signal + refs
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

        if getattr(self.config, 'STRATEGY_A9_ENABLED', False):
            self.strategies.append(StrategyA9(self.config, self.logger))

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
                StrategyA8(config, logger),
                StrategyA9(config, logger)
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
                    # Phase-1 MOMENTUM GATE: wall/imbalance signals are held for
                    # confirmation instead of executing instantly. Only commit when
                    # price confirms the move with volume on the next data window.
                    if self._momentum_gate_applies(strategy.name) and self._momentum_gate_blocked(symbol, strategy.name, signal, df):
                        # not yet confirmed (or awaiting) — rejections captured below
                        self.logger.info(f"⏳ [MOMENTUM GATE] {strategy.name} {symbol} signal held for confirmation")
                        continue

                    self.execute_entry(signal, strategy.name, symbol)
                elif hasattr(strategy, 'last_rejection') and strategy.last_rejection:
                    rejections[strategy.name] = strategy.last_rejection

        return {'symbol': symbol, 'rejections': rejections}

    # =====================================================================
    # Phase-1 MOMENTUM GATE (core entry-path fix)
    # A wall/imbalance signal alone no longer executes. The signal is parked
    # in self._momentum_pending; on the next data window we re-check the same
    # strategy's signal and only commit if price confirmed >= MIN_MOVE_PCT in
    # the signal side with volume ratio >= MIN_VOL_RATIO. Pending entries that
    # never confirm expire after MAX_WAIT_SECONDS (dead-entry killer).
    # =====================================================================
    def _momentum_gate_applies(self, strategy_name):
        """Gate applies only to strategies listed in MOMENTUM_GATE_STRATEGIES
        (A6 by default). Momentum-native strategies bypass via the front hook."""
        if not getattr(self.config, 'MOMENTUM_GATE_ENABLED', False):
            return False
        prefix = str(strategy_name).split(':')[0].strip().upper()
        scoped = getattr(self.config, 'MOMENTUM_GATE_STRATEGIES', [])
        if not scoped:
            return prefix == 'A6'
        return prefix in scoped

    def _momentum_gate_blocked(self, symbol, strategy_name, signal, df):
        """Returns True if the signal must be HELD (not executed this sweep).
        Stores the pending signal; checks confirmation on subsequent sweeps.
        Stale pendings (wall died, no new signal) are pruned on every call so
        they can't leak into the next signal cycle for that symbol."""
        import time as _t
        key = f"{strategy_name}:{symbol}"
        now = _t.time()
        max_wait_local = getattr(self.config, 'MOMENTUM_MAX_WAIT_SECONDS', 900)

        # Prune expired pendings regardless of whether a new signal arrived
        expired = [k for k, p in self._momentum_pending.items()
                   if (now - p.get('ts', 0)) > (p.get('max_wait', max_wait_local) * 2)]
        for k in expired:
            self.logger.warning(
                f"⏱️ [MOMENTUM GATE] {k} pruned — signal died before confirmation"
            )
            del self._momentum_pending[k]

        # Build the confirmation basis from the candle (volume ratio vs 20-bar avg)
        vol_ratio = 0.0
        move_pct = 0.0
        try:
            if df is not None and len(df) > 20 and 'volume' in df.columns and 'close' in df.columns:
                cur_vol = float(df.iloc[-1]['volume'])
                avg_vol = float(df['volume'].rolling(20).mean().iloc[-1])
                vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 0.0
                # Move measured against the entry reference of the held signal
                ref_price = self._momentum_pending.get(key, {}).get('ref_price')
                if ref_price:
                    px = float(df.iloc[-1]['close'])
                    move_pct = (px - ref_price) / ref_price * 100
                    if signal.get('side') == 'sell':
                        move_pct = -move_pct
        except Exception as e:
            self.logger.debug(f"[MOMENTUM GATE] calc error {symbol}: {e}")

        pending = self._momentum_pending.get(key)
        if pending is None:
            # First sighting: park the signal, ask for confirmation on next sweep
            self._momentum_pending[key] = {
                'signal': dict(signal),
                'ts': now,
                'ref_price': float(df.iloc[-1]['close']) if df is not None and len(df) else signal.get('entry_price'),
            }
            return True

        # Held signal — check confirmation
        min_move = getattr(self.config, 'MOMENTUM_MIN_MOVE_PCT', 0.5)
        min_vol = getattr(self.config, 'MOMENTUM_MIN_VOL_RATIO', 1.5)
        max_wait = getattr(self.config, 'MOMENTUM_MAX_WAIT_SECONDS', 120)

        if move_pct >= min_move and vol_ratio >= min_vol:
            self.logger.info(
                f"✅ [MOMENTUM GATE] {strategy_name} {symbol} CONFIRMED "
                f"(move {move_pct:+.2f}%, vol x{vol_ratio:.2f}) → executing"
            )
            del self._momentum_pending[key]
            return False  # not blocked — caller executes the fresh signal

        if (now - pending['ts']) > max_wait:
            self.logger.warning(
                f"⏱️ [MOMENTUM GATE] {strategy_name} {symbol} EXPIRED after {max_wait:.0f}s "
                f"(move {move_pct:+.2f}%, vol x{vol_ratio:.2f}) — dropped, no entry"
            )
            del self._momentum_pending[key]
            return True  # still no entry this sweep

        self.logger.debug(
            f"[MOMENTUM GATE] {strategy_name} {symbol} waiting "
            f"(move {move_pct:+.2f}%, vol x{vol_ratio:.2f}, needed {min_move:+}%/{min_vol}x)"
        )
        return True

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
        self.logger.info("🛡️ Priority Exit Sentinel Thread activated (WebSocket Mode).")
        last_telemetry = 0
        last_heartbeat = 0
        loop_count = 0
        tick = float(getattr(self.config, 'SENTINEL_TICK_SECONDS', 0.5))

        while self.running:
            try:
                active_symbols = list(set([p['symbol'] for p in self.engine.positions.values()]))

                now = time.time()
                show_telemetry = now - last_telemetry > 10
                if show_telemetry and active_symbols:
                    self.logger.info(f"🛡️ Sentinel Monitoring: {len(active_symbols)} symbols via WSS Feed. [tick={tick:.1f}s]")
                    last_telemetry = now
                # Watchdog heartbeat file
                if now - last_heartbeat > 5:
                    try:
                        import pathlib as _hb_path
                        _hb_path.Path('data/sentinel_heartbeat').write_text(str(int(now)))
                    except Exception:
                        pass
                    last_heartbeat = now

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
            
            # Configurable tick — default 0.5s, overridden via SENTINEL_TICK_SECONDS for CPU savings
            loop_count += 1
            # Lightweight metrics line every 60 ticks (~60s at 1s tick, ~30s at 0.5s)
            if loop_count % 120 == 0 and active_symbols:
                try:
                    rl = getattr(self.engine.exchange, 'rate_limiter', None)
                    avail = getattr(rl, 'available_weight', '?') if rl else '?'
                    self.logger.info(f"📊 Sentinel: {len(active_symbols)} positions | loop {loop_count} | rate_budget={avail}")
                except Exception:
                    pass
            time.sleep(tick)

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
                # Use in-memory positions dict (always authoritative, no rate-limit risk).
                # Exchange API fallback is skipped — rate-limit timeouts cause stale cache
                # that misses positions, leading to live algos being wrongly swept.
                protected = set()
                try:
                    for pos in self.engine.positions.values():
                        sym = pos.get('symbol', '')
                        if sym:
                            protected.add(sym)
                except Exception:
                    pass
                if not protected:
                    self.logger.warning("⚠️ [B2] Position fetch returned empty — skipping sweep to avoid wiping live algos")
                    _t.sleep(interval)
                    continue
                try:
                    resp = self.engine.exchange.exchange.fapiPrivateGetOpenAlgoOrders()
                    orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
                    algo_by_sym = {}
                    for o in orders:
                        s = o.get('symbol')
                        if s:
                            algo_by_sym.setdefault(s, []).append(o.get('algoId'))
                    for s, ids in algo_by_sym.items():
                        # Binance algo API uses bare symbols ("DODOXUSDT");
                        # ccxt positions use unified format ("DODOX/USDT:USDT").
                        if s.endswith('USDT') and '/' not in s:
                            canonical = s[:-4] + '/USDT:USDT'
                        elif not s.endswith(':USDT'):
                            canonical = s + '/USDT:USDT'
                        else:
                            canonical = s
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
                # S2: Periodic DB↔exchange reconciliation — detect orphan positions
                # that filled on exchange but weren't recorded in DB (the AIOT/MORPHO
                # bug from stale get_positions() cache). Detection only; adoption
                # happens at next startup via _sync_open_trades() (S1 prevents recurrence).
                try:
                    db = self.logger.db if hasattr(self.logger, 'db') else None
                    if db is not None and self.mode == 'live':
                        db_open = db.get_trades(status='OPEN')
                        db_syms = {t['symbol'] for t in db_open}
                        # Check protected set (already built above for B2)
                        orphans = protected - db_syms
                        if orphans:
                            self.logger.warning(
                                f"🔄 [S2] {len(orphans)} exchange position(s) not in DB: "
                                f"{', '.join(sorted(orphans)[:10])} — "
                                f"will be adopted at next restart"
                            )
                        zombies = db_syms - protected
                        if zombies and len(zombies) < 5:
                            self.logger.warning(
                                f"👻 [S2-ZOMBIE] {len(zombies)} DB position(s) "
                                f"not on exchange: {', '.join(sorted(zombies))}"
                            )
                except Exception as e:
                    self.logger.debug(f"[S2] reconciliation error: {e}")
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

                    # Aggregate results (rejections only)
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
                                
                        except Exception as e:
                            self.logger.error(f"Error getting future result: {e}")
                            
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
