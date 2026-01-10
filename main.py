#!/usr/bin/env python3
"""
APEX HUNTER V14 - Main Trading Bot
Supports paper trading (simulation) and live trading
"""

import sys
import time
import signal
import argparse
from datetime import datetime
import pandas as pd

from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4
from notifications import TelegramNotificationManager
from risk import RiskManager


class PaperTradingEngine:
    """
    Paper Trading Engine - Simulates trading with live market data
    """

    def __init__(self, config, logger, telegram):
        self.config = config
        self.logger = logger
        self.telegram = telegram

        # Initialize exchange for market data
        self.exchange = CCXTExchangeClient(config, logger, config.FUTURES_EXCHANGE)

        # Initialize risk manager (11 layers)
        self.risk_manager = RiskManager(config, logger)

        # Cache for top pairs
        self.top_pairs_cache = []
        self.last_pairs_update = None

        # Peak balance tracking for drawdown
        self.peak_balance = {}

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
        
        # If no strategies explicitly enabled, enable all
        if not self.strategies:
            self.strategies = [
                StrategyA1(config, logger),
                StrategyA2(config, logger),
                StrategyA3(config, logger),
                StrategyA4(config, logger)
            ]
        
        # Virtual positions (key: "strategy_name:symbol" -> position_data)
        self.positions = {}

        # Virtual capital per strategy (shared across all symbols)
        initial_capital = getattr(config, 'FUTURES_VIRTUAL_CAPITAL', 100)
        self.capital = {s.name: initial_capital for s in self.strategies}

        # Initialize peak balance for each strategy (for drawdown tracking)
        self.peak_balance = {s.name: initial_capital for s in self.strategies}

        # Performance tracking
        self.trades = []

        self.logger.info(f"Paper trading initialized with {len(self.strategies)} strategies")
        self.logger.info(f"Initial capital: ${initial_capital} per strategy")
        self.logger.info(f"Risk management: 11 layers active")

    def get_top_pairs_by_volume(self, top_n=30, min_volume_usdt=1000000):
        """
        Fetch top N trading pairs by 24h volume

        Args:
            top_n: Number of top pairs to return
            min_volume_usdt: Minimum 24h volume in USDT

        Returns:
            List of trading pair symbols
        """
        from datetime import datetime, timedelta

        # Update cache every 1 hour
        now = datetime.now()
        if (self.last_pairs_update and
            now - self.last_pairs_update < timedelta(hours=1) and
            self.top_pairs_cache):
            return self.top_pairs_cache

        try:
            self.logger.info("Fetching top trading pairs by volume...")

            # Fetch all tickers
            tickers = self.exchange.exchange.fetch_tickers()

            # Filter and sort by volume
            usdt_pairs = []
            for symbol, ticker in tickers.items():
                # Only USDT pairs (or configured quote currency)
                if not symbol.endswith('/USDT'):
                    continue

                # Skip if no volume data
                quote_volume = ticker.get('quoteVolume', 0)
                if not quote_volume or quote_volume < min_volume_usdt:
                    continue

                usdt_pairs.append({
                    'symbol': symbol,
                    'volume': quote_volume
                })

            # Sort by volume (descending)
            usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)

            # Get top N
            top_pairs = [p['symbol'] for p in usdt_pairs[:top_n]]

            # Update cache
            self.top_pairs_cache = top_pairs
            self.last_pairs_update = now

            self.logger.info(f"Found {len(top_pairs)} top pairs (min volume: ${min_volume_usdt:,.0f})")
            self.logger.info(f"Top 10: {', '.join(top_pairs[:10])}")

            return top_pairs

        except Exception as e:
            self.logger.error(f"Error fetching top pairs: {e}")
            # Fallback to configured pairs
            return getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'])

    def fetch_market_data(self, symbol='BTC/USDT', timeframe='15m', limit=200):
        """Fetch live market data"""
        try:
            ohlcv = self.exchange.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            self.logger.error(f"Error fetching market data: {e}")
            return None
    
    def update_trailing_stops(self, symbol, current_price):
        """Update trailing stops for all positions on a symbol"""
        for position_key, position in self.positions.items():
            if position['symbol'] != symbol:
                continue

            strategy_name = position['strategy']

            # Calculate profit threshold for activation
            activation_threshold = self.config.TRAILING_STOP_ACTIVATION / 100  # Convert to decimal
            trailing_distance = self.config.TRAILING_STOP_DISTANCE / 100  # Convert to decimal

            if position['side'] == 'buy':
                # Track highest price for long positions
                if position['highest_price'] is None or current_price > position['highest_price']:
                    position['highest_price'] = current_price

                    # Check if trailing stop should activate
                    profit_percent = (current_price - position['entry_price']) / position['entry_price']
                    if profit_percent >= activation_threshold and not position['trailing_stop_active']:
                        # Activate trailing stop
                        position['trailing_stop_active'] = True
                        position['trailing_activation_price'] = current_price
                        old_stop = position['stop_loss']
                        new_stop = current_price * (1 - trailing_distance)

                        # Only move stop loss upward (never downward for safety)
                        if new_stop > position['stop_loss']:
                            position['stop_loss'] = new_stop
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING STOP ACTIVATED @ ${current_price:.2f} "
                                           f"(Profit: {profit_percent*100:.1f}%) | Stop Loss: ${old_stop:.2f} → ${new_stop:.2f}")

                            # Send Telegram notification
                            if self.telegram:
                                self.telegram.send_futures_trailing_stop_update({
                                    'type': 'activated',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_percent * 100,
                                    'highest_price': position['highest_price'],
                                    'old_stop_loss': old_stop,
                                    'new_stop_loss': new_stop
                                })

                            # Log to MongoDB
                            if hasattr(self.logger, 'log_trailing_stop'):
                                self.logger.log_trailing_stop(
                                    'activated', symbol, strategy_name,
                                    current_price, profit_percent * 100,
                                    old_stop, new_stop,
                                    highest_price=position['highest_price']
                                )

                    # Update trailing stop if active
                    elif position['trailing_stop_active']:
                        new_stop = position['highest_price'] * (1 - trailing_distance)
                        if new_stop > position['stop_loss']:
                            old_stop = position['stop_loss']
                            position['stop_loss'] = new_stop
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING STOP UPDATED @ ${current_price:.2f} "
                                           f"(Highest: ${position['highest_price']:.2f}) | Stop Loss: ${old_stop:.2f} → ${new_stop:.2f}")

                            # Send Telegram notification
                            if self.telegram:
                                profit_percent = (current_price - position['entry_price']) / position['entry_price']
                                self.telegram.send_futures_trailing_stop_update({
                                    'type': 'update',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_percent * 100,
                                    'highest_price': position['highest_price'],
                                    'old_stop_loss': old_stop,
                                    'new_stop_loss': new_stop
                                })

                            # Log to MongoDB
                            if hasattr(self.logger, 'log_trailing_stop'):
                                self.logger.log_trailing_stop(
                                    'update', symbol, strategy_name,
                                    current_price, profit_percent * 100,
                                    old_stop, new_stop,
                                    highest_price=position['highest_price']
                                )

            else:  # sell position
                # Track lowest price for short positions
                if position['lowest_price'] is None or current_price < position['lowest_price']:
                    position['lowest_price'] = current_price

                    # Check if trailing stop should activate
                    profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                    if profit_percent >= activation_threshold and not position['trailing_stop_active']:
                        # Activate trailing stop
                        position['trailing_stop_active'] = True
                        position['trailing_activation_price'] = current_price
                        old_stop = position['stop_loss']
                        new_stop = current_price * (1 + trailing_distance)

                        # Only move stop loss downward (never upward for safety)
                        if new_stop < position['stop_loss']:
                            position['stop_loss'] = new_stop
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING STOP ACTIVATED @ ${current_price:.2f} "
                                           f"(Profit: {profit_percent*100:.1f}%) | Stop Loss: ${old_stop:.2f} → ${new_stop:.2f}")

                            # Send Telegram notification
                            if self.telegram:
                                self.telegram.send_futures_trailing_stop_update({
                                    'type': 'activated',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_percent * 100,
                                    'highest_price': position['lowest_price'],  # For shorts, this is the lowest price
                                    'old_stop_loss': old_stop,
                                    'new_stop_loss': new_stop
                                })

                    # Update trailing stop if active
                    elif position['trailing_stop_active']:
                        new_stop = position['lowest_price'] * (1 + trailing_distance)
                        if new_stop < position['stop_loss']:
                            old_stop = position['stop_loss']
                            position['stop_loss'] = new_stop
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING STOP UPDATED @ ${current_price:.2f} "
                                           f"(Lowest: ${position['lowest_price']:.2f}) | Stop Loss: ${old_stop:.2f} → ${new_stop:.2f}")

                            # Send Telegram notification
                            if self.telegram:
                                profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                                self.telegram.send_futures_trailing_stop_update({
                                    'type': 'update',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_percent * 100,
                                    'highest_price': position['lowest_price'],  # For shorts, this is the lowest price
                                    'old_stop_loss': old_stop,
                                    'new_stop_loss': new_stop
                                })

                            # Log to MongoDB
                            if hasattr(self.logger, 'log_trailing_stop'):
                                self.logger.log_trailing_stop(
                                    'update', symbol, strategy_name,
                                    current_price, profit_percent * 100,
                                    old_stop, new_stop,
                                    lowest_price=position['lowest_price']
                                )

    def check_position_exit(self, position, current_price):
        """Check if position should be exited"""
        if position['side'] == 'buy':
            if current_price <= position['stop_loss']:
                return True, 'stop_loss'
            elif current_price >= position['take_profit']:
                return True, 'take_profit'
        else:  # sell
            if current_price >= position['stop_loss']:
                return True, 'stop_loss'
            elif current_price <= position['take_profit']:
                return True, 'take_profit'

        return False, None
    
    def calculate_dynamic_leverage(self, strategy_name, confidence):
        """
        Calculate dynamic leverage based on:
        - Strategy confidence (0.0 to 1.0)
        - Configured max leverage
        - Current drawdown state

        Returns:
            int: Leverage to use (1 to MAX_LEVERAGE)
        """
        # Get max leverage from config
        max_leverage = getattr(self.config, 'FUTURES_MAX_LEVERAGE', 10)

        # Calculate current drawdown
        initial_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
        current_capital = self.capital[strategy_name]

        if current_capital < initial_capital:
            drawdown_percent = ((initial_capital - current_capital) / initial_capital) * 100
            # Use config's drawdown-adjusted leverage
            max_leverage = self.config.get_drawdown_adjusted_leverage(drawdown_percent)

        # Scale leverage by confidence
        # Confidence 0.5 = 50% of max leverage
        # Confidence 0.75 = 75% of max leverage
        # Confidence 1.0 = 100% of max leverage
        leverage = max(1, int(confidence * max_leverage))

        return min(leverage, max_leverage)

    def execute_paper_trade(self, signal, strategy_name, symbol):
        """Simulate trade execution with risk validation"""
        position_key = f"{strategy_name}:{symbol}"

        # Entry
        if signal and position_key not in self.positions:
            # Calculate dynamic leverage
            confidence = signal.get('confidence', 0.5)
            leverage = self.calculate_dynamic_leverage(strategy_name, confidence)

            # Prepare trade parameters for risk evaluation
            trade_params = {
                'symbol': symbol,
                'side': signal['side'],
                'entry_price': signal['entry_price'],
                'size': self.capital[strategy_name] * 0.1,  # 10% of capital
                'leverage': leverage,
                'stop_loss': signal['stop_loss'],
                'take_profit': signal['take_profit'],
                'strategy': strategy_name
            }

            # Prepare account state for risk evaluation
            account_state = {
                'balance': self.capital[strategy_name],
                'peak_balance': self.peak_balance[strategy_name],
                'open_positions': [p for k, p in self.positions.items() if p['strategy'] == strategy_name],
                'recent_trades': [t for t in self.trades if t['strategy'] == strategy_name][-20:],  # Last 20 trades
                'current_time': datetime.now()
            }

            # Validate through risk management system (11 layers)
            approved_params = self.risk_manager.evaluate_trade(trade_params, account_state)

            if approved_params is None:
                # Trade rejected by risk management
                self.logger.warning(f"[{strategy_name}] {symbol} TRADE REJECTED by risk management")
                return

            # Trade approved - execute
            position = {
                'entry_time': datetime.now(),
                'entry_price': approved_params['entry_price'],
                'side': approved_params['side'],
                'stop_loss': approved_params['stop_loss'],
                'take_profit': approved_params['take_profit'],
                'size': approved_params['size'],
                'leverage': approved_params['leverage'],
                'strategy': strategy_name,
                'symbol': symbol,
                # Trailing stop initialization
                'trailing_stop_active': False,
                'highest_price': approved_params['entry_price'] if approved_params['side'] == 'buy' else None,
                'lowest_price': approved_params['entry_price'] if approved_params['side'] == 'sell' else None,
                'trailing_activation_price': None,
                'original_stop_loss': approved_params['stop_loss']
            }

            self.positions[position_key] = position

            self.logger.info(f"[{strategy_name}] {symbol} ENTRY {signal['side'].upper()} @ ${signal['entry_price']:.2f} (Leverage: {leverage}x) ✅ Risk Approved")

            # Telegram notification
            if self.telegram and self.telegram.futures_bot:
                self.telegram.send_futures_trade_entry({
                    'symbol': symbol,
                    'side': signal['side'],
                    'entry_price': signal['entry_price'],
                    'size': position['size'],
                    'leverage': leverage,
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'strategy': strategy_name,
                    'confidence': confidence,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
    
    def check_exits(self, symbol, current_price):
        """Check all positions for exit conditions for a specific symbol"""
        closed_positions = []

        for position_key, position in list(self.positions.items()):
            # Only check positions for this symbol
            if position['symbol'] != symbol:
                continue

            should_exit, reason = self.check_position_exit(position, current_price)

            if should_exit:
                strategy_name = position['strategy']
                leverage = position.get('leverage', 1)

                # Calculate P&L (price movement percentage)
                if position['side'] == 'buy':
                    pnl_percent = (current_price - position['entry_price']) / position['entry_price']
                else:
                    pnl_percent = (position['entry_price'] - current_price) / position['entry_price']

                # Apply leverage to P&L
                leveraged_pnl_percent = pnl_percent * leverage
                pnl_amount = position['size'] * leveraged_pnl_percent

                self.capital[strategy_name] += pnl_amount

                # Update peak balance (for drawdown tracking)
                if self.capital[strategy_name] > self.peak_balance[strategy_name]:
                    self.peak_balance[strategy_name] = self.capital[strategy_name]
                    self.risk_manager.update_peak_balance(self.capital[strategy_name])

                # Record trade result with risk manager
                is_win = pnl_amount > 0
                self.risk_manager.record_trade_result(is_win, pnl_amount)

                # Record trade
                trade = {
                    'entry_time': position['entry_time'],
                    'exit_time': datetime.now(),
                    'strategy': strategy_name,
                    'symbol': symbol,
                    'side': position['side'],
                    'entry_price': position['entry_price'],
                    'exit_price': current_price,
                    'leverage': leverage,
                    'pnl': pnl_amount,
                    'pnl_percent': leveraged_pnl_percent * 100,
                    'reason': reason,
                    'capital_after': self.capital[strategy_name]
                }

                self.trades.append(trade)

                self.logger.info(f"[{strategy_name}] {symbol} EXIT {reason.upper()} @ ${current_price:.2f}, "
                               f"Leverage: {leverage}x, P&L: ${pnl_amount:+.2f} ({leveraged_pnl_percent*100:+.2f}%)")

                # Telegram notification
                if self.telegram and self.telegram.futures_bot:
                    duration = (trade['exit_time'] - trade['entry_time']).seconds // 60
                    self.telegram.send_futures_trade_exit({
                        'symbol': symbol,
                        'exit_price': current_price,
                        'pnl': pnl_amount,
                        'pnl_percent': leveraged_pnl_percent * 100,
                        'leverage': leverage,
                        'duration': f"{duration} minutes",
                        'strategy': strategy_name
                    })

                closed_positions.append(position_key)

        # Remove closed positions
        for position_key in closed_positions:
            del self.positions[position_key]
    
    def run_cycle(self, symbol='BTC/USDT'):
        """Run one trading cycle for a specific symbol"""
        # Fetch market data
        df = self.fetch_market_data(symbol)

        if df is None or len(df) == 0:
            self.logger.error(f"Failed to fetch market data for {symbol}")
            return

        current_price = df.iloc[-1]['close']

        # Update trailing stops for existing positions
        self.update_trailing_stops(symbol, current_price)

        # Check exits first for this symbol
        self.check_exits(symbol, current_price)

        # Check for new signals
        for strategy in self.strategies:
            position_key = f"{strategy.name}:{symbol}"

            # Skip if strategy already has open position for this symbol
            if position_key in self.positions:
                continue

            # Generate signal
            signal = strategy.generate_signal(df)

            if signal:
                self.execute_paper_trade(signal, strategy.name, symbol)

        # Log status with more details
        total_pnl = sum(self.capital[s.name] - getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
                       for s in self.strategies)

        self.logger.info(f"{symbol} | Price: ${current_price:.2f} | "
                        f"Open: {len(self.positions)} | Total P&L: ${total_pnl:+.2f}")

        # Debug: Log strategy analysis summary
        for strategy in self.strategies:
            position_key = f"{strategy.name}:{symbol}"
            has_position = position_key in self.positions
            self.logger.debug(f"Strategy {strategy.name}: {'HAS POSITION' if has_position else 'AVAILABLE'} for {symbol}")
    
    def print_summary(self):
        """Print trading summary"""
        print("\n" + "=" * 80)
        print("  PAPER TRADING SUMMARY")
        print("=" * 80)

        initial_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)

        if not self.trades:
            print("\n  No trades executed.")
            print("\n" + "=" * 80 + "\n")
            return

        # Summary by strategy
        for strategy in self.strategies:
            strategy_trades = [t for t in self.trades if t['strategy'] == strategy.name]

            if strategy_trades:
                wins = [t for t in strategy_trades if t['pnl'] > 0]
                total_pnl = sum(t['pnl'] for t in strategy_trades)
                win_rate = len(wins) / len(strategy_trades) * 100
                avg_leverage = sum(t.get('leverage', 1) for t in strategy_trades) / len(strategy_trades)

                print(f"\n  {strategy.name}:")
                print(f"    Trades: {len(strategy_trades)}")
                print(f"    Wins: {len(wins)} ({win_rate:.1f}%)")
                print(f"    Avg Leverage: {avg_leverage:.1f}x")
                print(f"    Final Capital: ${self.capital[strategy.name]:.2f}")
                print(f"    Total P&L: ${total_pnl:+.2f} ({total_pnl/initial_capital*100:+.2f}%)")

                # Show breakdown by symbol
                symbols = set(t['symbol'] for t in strategy_trades)
                if len(symbols) > 1:
                    print(f"    Breakdown by symbol:")
                    for sym in sorted(symbols):
                        sym_trades = [t for t in strategy_trades if t['symbol'] == sym]
                        sym_pnl = sum(t['pnl'] for t in sym_trades)
                        print(f"      {sym}: {len(sym_trades)} trades, ${sym_pnl:+.2f}")

        print("\n" + "=" * 80 + "\n")


class ApexHunterBot:
    """Main bot orchestrator"""
    
    def __init__(self, mode='paper'):
        self.mode = mode
        self.running = False
        
        print("=" * 80)
        print("  APEX HUNTER V14")
        print("=" * 80)
        print()
        
        # Load configuration
        print("⚙️  Loading configuration...")
        self.config = Config()
        self.logger = MongoLogger(self.config)

        # Handle cleanup operations
        self._handle_cleanup()

        # Initialize Telegram
        print("📱 Initializing Telegram bots...")
        self.telegram = TelegramNotificationManager(self.config, self.logger)

        # Initialize trading engine
        if self.mode == 'paper':
            print("🎮 Initializing PAPER TRADING mode...")
            self.engine = PaperTradingEngine(self.config, self.logger, self.telegram)
            pairs_config = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])

            print(f"✅ Paper trading ready with {len(self.engine.strategies)} strategies")

            if isinstance(pairs_config, str) and pairs_config.lower() == 'auto':
                top_n = getattr(self.config, 'FUTURES_AUTO_TOP_N', 30)
                min_vol = getattr(self.config, 'FUTURES_AUTO_MIN_VOLUME', 1000000)
                print(f"   Mode: AUTO-DISCOVERY (Top {top_n} pairs, min ${min_vol:,.0f} volume)")
            else:
                pairs = pairs_config if isinstance(pairs_config, list) else [p.strip() for p in pairs_config.split(',')]
                print(f"   Monitoring: {', '.join(pairs)}")
        else:
            print("⚠️  LIVE TRADING mode not implemented yet!")
            print("   Use --mode paper for now")
            sys.exit(1)

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

        if clean_logs or clean_db:
            print("🧹 Starting cleanup operations...")

        # Clean log files
        if clean_logs:
            self._clean_log_files()

        # Clean database files
        if clean_db:
            self._clean_database_files()

        if clean_logs or clean_db:
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
    
    def run(self, interval=60):
        """Run the bot"""
        self.running = True

        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)

        try:
            while self.running:
                # Get trading pairs (dynamic or static)
                pairs_config = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])

                # Check if auto mode
                if isinstance(pairs_config, str) and pairs_config.lower() == 'auto':
                    top_n = int(getattr(self.config, 'FUTURES_AUTO_TOP_N', 30))
                    min_volume = float(getattr(self.config, 'FUTURES_AUTO_MIN_VOLUME', 1000000))
                    pairs = self.engine.get_top_pairs_by_volume(top_n, min_volume)
                elif isinstance(pairs_config, str):
                    # Parse comma-separated string
                    pairs = [p.strip() for p in pairs_config.split(',')]
                else:
                    pairs = pairs_config

                # Run cycle for each trading pair
                for symbol in pairs:
                    if not self.running:
                        break
                    self.engine.run_cycle(symbol)

                # Wait before next cycle
                if self.running:
                    time.sleep(interval)

        except KeyboardInterrupt:
            pass

        finally:
            self.shutdown()
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signal"""
        print("\n\n⚠️  Shutdown signal received...")
        self.running = False
    
    def shutdown(self):
        """Graceful shutdown"""
        print("\n🛑 Shutting down...")
        
        # Print summary
        if hasattr(self.engine, 'print_summary'):
            self.engine.print_summary()
        
        self.logger.info("Bot stopped")
        print("✅ Bot stopped successfully")


def main():
    parser = argparse.ArgumentParser(description='APEX HUNTER V14 Trading Bot')
    parser.add_argument('--mode', type=str, choices=['paper', 'live'], default='paper',
                        help='Trading mode (default: paper)')
    parser.add_argument('--interval', type=int, default=60,
                        help='Check interval in seconds (default: 60)')
    
    args = parser.parse_args()
    
    # Verify mode
    if args.mode == 'live':
        print("\n⚠️  WARNING: LIVE TRADING MODE!")
        print("   Real money will be at risk!")
        print()
        confirm = input("Are you sure? Type 'YES' to continue: ")
        if confirm != 'YES':
            print("Aborted.")
            sys.exit(0)
    
    # Create and run bot
    bot = ApexHunterBot(mode=args.mode)
    bot.run(interval=args.interval)


if __name__ == "__main__":
    main()
