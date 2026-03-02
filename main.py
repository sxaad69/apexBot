#!/usr/bin/env python3
"""
APEX HUNTER V14 - Main Trading Bot
Supports paper trading (simulation) and live trading
"""

import sys
import time
import signal
import argparse
import uuid
from datetime import datetime, timedelta
import pandas as pd

from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5, StrategyA6
from notifications import TelegramNotificationManager
from risk import RiskManager
from core.spot_logger import SpotLogger
from core.spot_trading_engine import SpotTradingEngine
from risk.layers.trailing_stop import TrailingStopLayer


class PaperTradingEngine:
    """
    Paper Trading Engine - Simulates trading with live market data
    Supports both Paper (Virtual) and Live (API) capital modes
    """

    def __init__(self, config, logger, telegram, mode='paper'):
        self.config = config
        self.logger = logger
        self.telegram = telegram
        self.mode = mode

        # Initialize exchange for market data
        self.exchange = CCXTExchangeClient(config, logger, config.FUTURES_EXCHANGE)

        # Initialize risk manager (11 layers)
        self.risk_manager = RiskManager(config, logger, db_manager=logger.db if hasattr(logger, 'db') else None)

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
        if hasattr(config, 'STRATEGY_A5_ENABLED') and config.STRATEGY_A5_ENABLED:
            self.strategies.append(StrategyA5(config, logger))
        if hasattr(config, 'STRATEGY_A6_ENABLED') and config.STRATEGY_A6_ENABLED:
            self.strategies.append(StrategyA6(config, logger))

        # If no strategies explicitly enabled, enable all including A6
        if not self.strategies:
            self.strategies = [
                StrategyA1(config, logger),
                StrategyA2(config, logger),
                StrategyA3(config, logger),
                StrategyA4(config, logger),
                StrategyA5(config, logger),
                StrategyA6(config, logger)
            ]
        
        # Inject exchange client into strategies for microstructure analysis (A5)
        for strategy in self.strategies:
            strategy.exchange_client = self.exchange
        
        # Virtual positions (key: "strategy_name:symbol" -> position_data)
        self.positions = {}

        # Capital Initialization logic (Shared Pool)
        if self.mode == 'live':
            self.logger.info("Fetching REAL balance from exchange...")
            try:
                full_balance = self.exchange.get_balance()
                # Unified access for USDT balance across major exchanges
                self.total_capital = float(full_balance.get('USDT', {}).get('free', 0))
                
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

        # Update cache every 15 minutes (faster alpha discovery)
        now = datetime.now()
        if (self.last_pairs_update and
            now - self.last_pairs_update < timedelta(minutes=15) and
            self.top_pairs_cache):
            return self.top_pairs_cache

        try:
            self.logger.info("Fetching top trading pairs by volume...")

            # Fetch all tickers
            tickers = self.exchange.exchange.fetch_tickers()

            # Filter and sort by volume
            usdt_pairs = []
            for symbol, ticker in tickers.items():
                # Only USDT pairs (support varied CCXT futures formats)
                if ':USDT' not in symbol and not symbol.endswith('/USDT'):
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

    def fetch_market_data(self, symbol='BTC/USDT', timeframe=None, limit=300):
        """Fetch spot market data using CCXT (exchange-agnostic)"""
        if timeframe is None:
            timeframe = self.config.TIMEFRAME
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
                # Track highest price since entry
                if current_price > position['highest_price']:
                    position['highest_price'] = current_price
                
                # Check for activation
                profit_percent = (current_price - position['entry_price']) / position['entry_price']
                if not position['trailing_stop_active'] and profit_percent >= activation_threshold:
                    position['trailing_stop_active'] = True
                    position['trailing_activation_price'] = current_price
                    # Activation moves SL relative to peak
                    new_stop = position['highest_price'] * (1 - trailing_distance)
                    if new_stop > position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING ACTIVATED @ ${current_price:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")

                # Ratchet logic: move SL up if current highest_price justifies it
                if position['trailing_stop_active']:
                    new_stop = position['highest_price'] * (1 - trailing_distance)
                    if new_stop > position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING RATCHET @ ${current_price:.2f} | Peak: ${position['highest_price']:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")

            else:  # sell position
                # Track lowest price since entry
                if current_price < position['lowest_price']:
                    position['lowest_price'] = current_price

                # Check for activation
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                if not position['trailing_stop_active'] and profit_percent >= activation_threshold:
                    position['trailing_stop_active'] = True
                    position['trailing_activation_price'] = current_price
                    # Activation moves SL relative to peak
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    if new_stop < position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING ACTIVATED @ ${current_price:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")

                # Ratchet logic: move SL down if current lowest_price justifies it
                if position['trailing_stop_active']:
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    if new_stop < position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING RATCHET @ ${current_price:.2f} | Peak: ${position['lowest_price']:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")










    def update_trailing_take_profit(self, symbol, current_price):
        """Update trailing take profit for all positions on a symbol."""
        if not getattr(self.config, 'TRAILING_TP_ENABLED', True):
            return

        for position_key, position in self.positions.items():
            if position['symbol'] != symbol:
                continue

            strategy_name = position['strategy']
            tp_activation_threshold = getattr(self.config, 'TRAILING_TP_ACTIVATION', 3.0) / 100
            tp_trailing_distance = getattr(self.config, 'TRAILING_TP_DISTANCE', 1.5) / 100

            if position['side'] == 'buy':
                profit_percent = (current_price - position['entry_price']) / position['entry_price']
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_peak_price'] = None

                if position['trailing_tp_peak_price'] is None or current_price > position['trailing_tp_peak_price']:
                    position['trailing_tp_peak_price'] = current_price

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 - tp_trailing_distance)
                        if new_tp > old_tp and new_tp > position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.2f} (Profit: {profit_percent*100:.1f}%) | TP: ${old_tp:.2f} → ${new_tp:.2f}")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_peak_price'] * (1 - tp_trailing_distance)
                        if new_tp > position['take_profit'] and new_tp > position['entry_price']: # Move TP HIGHER for Long
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")

            else:  # sell position
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_trough_price'] = None

                if position['trailing_tp_trough_price'] is None or current_price < position['trailing_tp_trough_price']:
                    position['trailing_tp_trough_price'] = current_price

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 + tp_trailing_distance)
                        if new_tp < old_tp and new_tp < position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.2f} (Profit: {profit_percent*100:.1f}%) | TP: ${old_tp:.2f} → ${new_tp:.2f}")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_trough_price'] * (1 + tp_trailing_distance)
                        if new_tp < position['take_profit'] and new_tp < position['entry_price']: # Move TP LOWER for Short
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")


    def check_position_exit(self, position, current_price):
        """Check if position should be exited"""
        if position['side'] == 'buy':
            if current_price <= position['stop_loss']:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
            elif current_price >= position['take_profit']:
                return True, 'take_profit'
        else:  # sell
            if current_price >= position['stop_loss']:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
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
        current_capital = self.total_capital

        if current_capital < initial_capital:
            drawdown_percent = ((initial_capital - current_capital) / initial_capital) * 100
            # Use config's drawdown-adjusted leverage
            max_leverage = self.config.get_drawdown_adjusted_leverage(drawdown_percent)

        # Dynamic leverage based on actual strategy confidence output
        # Adjusted thresholds to match real strategy performance (0.40-0.70 range)
        if confidence > 0.75:  # A5 exceptional signals
            leverage = max_leverage  # Use full power
        elif confidence > 0.65:  # A1-A4 strong signals
            leverage = max_leverage * 0.7  # Use 70% of max power
        elif confidence > 0.55:  # Medium signals
            leverage = max_leverage * 0.4  # Use 40% of max power
        else:
            leverage = 1.0  # Low confidence: no leverage

        leverage = max(1, int(leverage))  # Ensure integer, minimum 1x

        return min(leverage, max_leverage)

    def execute_paper_trade(self, signal, strategy_name, symbol):
        """Simulate trade execution with risk validation"""
        position_key = f"{strategy_name}:{symbol}"

        # Entry
        if signal and position_key not in self.positions:
            # --- Confidence-Based Position Sizing ---
            # Higher conviction signals deserve proportionally more capital
            confidence = signal.get('confidence', 0.5)
            if confidence >= 0.90:
                base_size_pct = 0.15  # 15% — Elite conviction
            elif confidence >= 0.80:
                base_size_pct = 0.12  # 12% — High conviction
            elif confidence >= 0.70:
                base_size_pct = 0.10  # 10% — Standard
            else:
                base_size_pct = 0.07  # 7% — Low conviction, cautious

            total_capital = self.total_capital

            # --- Opportunity Reserve System ---
            # 20% of capital is always held in reserve ("Opportunity Reserve")
            # This reserve is ONLY unlocked for signals with confidence >= 0.90
            OPPORTUNITY_RESERVE_PCT  = 0.20   # 20% always held as reserve
            OPPORTUNITY_THRESHOLD   = 0.90   # Confidence needed to tap reserve

            if confidence >= OPPORTUNITY_THRESHOLD:
                # Elite signal: can use up to 80% total exposure (full + reserve access)
                max_exposure = total_capital * 0.80
            else:
                # Normal signal: only 60% exposure (leaves 20% reserve + 20% safety)
                max_exposure = total_capital * 0.60

            current_exposure = sum(p['size'] for p in self.positions.values() if p['strategy'] == strategy_name)
            available_for_new_trade = max_exposure - current_exposure

            if available_for_new_trade < total_capital * 0.03:  # Min 3% must be free
                if confidence >= OPPORTUNITY_THRESHOLD:
                    self.logger.warning(f"[{strategy_name}] {symbol} HIGH-CONFIDENCE SIGNAL SKIPPED - Even reserve is exhausted")
                else:
                    self.logger.warning(f"[{strategy_name}] {symbol} INSUFFICIENT RESERVE CAPITAL - Max exposure reached")
                return

            # Limit position size to available capital, respecting confidence tier
            max_position_size = min(
                total_capital * base_size_pct,
                available_for_new_trade
            )

            self.logger.debug(
                f"[{strategy_name}] Position sizing: Conf {confidence:.2f} → "
                f"{base_size_pct*100:.0f}% size (${max_position_size:.2f}) | "
                f"Reserve mode: {'OPPORTUNITY' if confidence >= OPPORTUNITY_THRESHOLD else 'NORMAL'}"
            )

            # Prepare trade parameters for risk evaluation
            leverage = self.calculate_dynamic_leverage(strategy_name, confidence)
            indicators = signal.get('indicators', {})
            trade_params = {

                'symbol': symbol,
                'side': signal['side'],
                'entry_price': signal['entry_price'],
                'size': max_position_size,  # Respect reserve limits
                'leverage': leverage,
                'stop_loss': signal['stop_loss'],
                'take_profit': signal['take_profit'],
                'strategy': strategy_name,
                'confidence': signal.get('confidence', 0.5),
                'atr': indicators.get('atr', None),  # Pass ATR for dynamic leverage
            }

            # Unified drawdown tracking for the shared pool
            current_capital = self.total_capital
            peak_capital = self.peak_balance
            drawdown_percent = 0
            if current_capital < peak_capital:
                drawdown_percent = ((peak_capital - current_capital) / peak_capital) * 100
            
            account_state = {
                'total_balance': current_capital,
                'available_balance': current_capital,  # For paper trading, all capital is available
                'drawdown_percent': drawdown_percent,
                'peak_balance': peak_capital,  # Keep for compatibility
                'open_positions': [p for p in self.positions.values() if p['strategy'] == strategy_name],
                'open_positions_count': len(self.positions),
                'recent_trades': [t for t in self.trades if t['strategy'] == strategy_name][-20:],  # Last 20 trades
                'current_time': datetime.now()

            }
            
            # Debug: Log account state for visibility
            self.logger.debug(
                f"[{strategy_name}] Account state: "
                f"available=${account_state['available_balance']:.2f}, "
                f"total=${account_state['total_balance']:.2f}, "
                f"drawdown={account_state['drawdown_percent']:.2f}%"
            )

            # Validate through risk management system (11 layers)
            approved_params = self.risk_manager.evaluate_trade(trade_params, account_state)

            if approved_params is None:
                # Trade rejected by risk management
                self.logger.warning(f"[{strategy_name}] {symbol} TRADE REJECTED by risk management")
                return

            # Approved trade - execute
            entry_price = approved_params['entry_price']
            size = approved_params['size']
            leverage = approved_params.get('leverage', leverage)
            
            # Calculate entry fee
            fee_percent = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
            entry_fee = size * fee_percent
            
            # Deduct from shared pool
            self.total_capital -= (size + entry_fee)
            
            trade_id = f"FUT-{uuid.uuid4().hex[:8].upper()}"
            
            position = {
                'trade_id': trade_id,
                'entry_time': datetime.now(),
                'entry_price': entry_price,
                'side': approved_params['side'],
                'stop_loss': approved_params['stop_loss'],
                'take_profit': approved_params['take_profit'],
                'size': size,
                'entry_fee': entry_fee,
                'leverage': approved_params['leverage'],
                'strategy': strategy_name,
                'symbol': symbol,
                # Trailing stop initialization
                'trailing_stop_active': False,
                'highest_price': entry_price,
                'lowest_price': entry_price,

                'trailing_activation_price': None,
                'original_stop_loss': approved_params['stop_loss']
            }

            self.positions[position_key] = position
            
            self.logger.info(f"[{strategy_name}] {symbol} BALANCE DEDUCTED: ${size + entry_fee:.2f} (Entry: ${size:.2f} + Fee: ${entry_fee:.2f})")

            self.logger.info(f"[{strategy_name}] {symbol} ENTRY {signal['side'].upper()} @ ${signal['entry_price']:.2f} (Leverage: {leverage}x) ✅ Risk Approved")

            # MongoDB structured logging
            self.logger.trade_entry(
                symbol=symbol,
                side=signal['side'],
                size=position['size'],
                price=signal['entry_price'],
                leverage=leverage,
                market_type='futures',
                strategy=strategy_name,
                confidence=confidence,
                stop_loss=signal['stop_loss'],
                take_profit=signal['take_profit'],
                trade_id=position['trade_id']
            )

            # Telegram notification
            if self.telegram and self.telegram.futures_bot:
                self.telegram.send_futures_trade_entry({
                    'trade_id': position['trade_id'],
                    'symbol': symbol,
                    'side': signal['side'],
                    'entry_price': signal['entry_price'],
                    'size': position['size'],
                    'leverage': leverage,
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'strategy': strategy_name,
                    'confidence': confidence,
                    'remaining_capital': self.total_capital,
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
                gross_pnl_amount = position['size'] * leveraged_pnl_percent
                
                # Calculate exit fee
                fee_percent = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
                exit_fee = position['size'] * (1 + pnl_percent) * fee_percent # Fee based on exit value
                
                # Net P&L after all fees (entry + exit)
                total_fees = position.get('entry_fee', 0) + exit_fee
                net_pnl_amount = gross_pnl_amount - exit_fee # subtract exit fee from pnl
                
                # Return margin and P&L to shared pool
                self.total_capital += (position['size'] + net_pnl_amount)
                
                pnl_amount = net_pnl_amount # Use net for logging

                # Update peak balance (for drawdown tracking)
                if self.total_capital > self.peak_balance:
                    self.peak_balance = self.total_capital
                    self.risk_manager.update_peak_balance(self.total_capital)

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
                    'capital_after': self.total_capital
                }

                self.trades.append(trade)

                self.logger.info(f"[{strategy_name}] {symbol} EXIT {reason.upper()} @ ${current_price:.2f}, "
                               f"Leverage: {leverage}x, P&L: ${pnl_amount:+.2f} ({leveraged_pnl_percent*100:+.2f}%)")

                # MongoDB structured logging
                self.logger.trade_exit(
                    symbol=symbol,
                    pnl=pnl_amount,
                    pnl_percent=leveraged_pnl_percent * 100,
                    duration=f"{(trade['exit_time'] - trade['entry_time']).seconds // 60} minutes",
                    market_type='futures',
                    exit_price=current_price,
                    reason=reason,
                    strategy=strategy_name,
                    entry_price=position['entry_price'],
                    side=position['side'],
                    leverage=leverage,
                    stop_loss=position['stop_loss'],
                    take_profit=position['take_profit'],
                    trade_id=position.get('trade_id')
                )

                # Telegram notification
                if self.telegram and self.telegram.futures_bot:
                    # Deduplication check
                    import time
                    current_time_ts = time.time()
                    last_notification = self.recent_exit_notifications.get(position_key, 0)
                    
                    if current_time_ts - last_notification > 5:  # 5-second cooldown
                        duration = (trade['exit_time'] - trade['entry_time']).seconds // 60
                        self.telegram.send_futures_trade_exit({
                            'trade_id': position.get('trade_id', 'N/A'),
                            'symbol': symbol,
                            'entry_price': position['entry_price'],
                            'exit_price': current_price,
                            'pnl': pnl_amount,
                            'pnl_percent': leveraged_pnl_percent * 100,
                            'leverage': leverage,
                            'remaining_capital': self.total_capital,
                            'duration': f"{duration} minutes",
                            'strategy': strategy_name
                        })
                        self.recent_exit_notifications[position_key] = current_time_ts
                        
                        # Cleanup old entries (keep last hour only)
                        cutoff = current_time_ts - 3600
                        keys_to_remove = [k for k, v in self.recent_exit_notifications.items() if v < cutoff]
                        for k in keys_to_remove:
                            del self.recent_exit_notifications[k]
                    else:
                        self.logger.debug(f"Skipping duplicate exit notification for {position_key}")

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
        self.current_prices[symbol] = current_price

        # Update trailing stops for existing positions
        self.update_trailing_stops(symbol, current_price)

        # Update trailing take profit for existing positions
        self.update_trailing_take_profit(symbol, current_price)

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

        # Collect and save market analysis data for dashboard
        self._collect_market_analysis_data(symbol, df, current_price)

        # Check if it's time to send hourly report
        self._check_and_send_hourly_report()

        # Save current active positions for dashboard with live prices
        self.logger.save_active_positions(self.positions, self.current_prices)

        # Log status with more details
        total_pnl = self.total_capital - getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
        
        self.logger.info(f"{symbol} | Price: ${current_price:.2f} | "
                        f"Open: {len(self.positions)} | Total P&L: ${total_pnl:+.2f}")

        # Debug: Log strategy analysis summary
        for strategy in self.strategies:
            position_key = f"{strategy.name}:{symbol}"
            has_position = position_key in self.positions
            self.logger.debug(f"Strategy {strategy.name}: {'HAS POSITION' if has_position else 'AVAILABLE'} for {symbol}")

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
                signal = strategy.generate_signal(df)
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

            # Prepare strategy signals data
            signals_data = {
                'date': current_date,
                'hour': current_hour,
                'trading_type': 'futures',
                **strategy_signals,
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

            # Save to database (MongoDB first, JSON fallback)
            success1 = self.logger.save_market_analysis(current_date, current_hour, analysis_data)
            success2 = self.logger.save_strategy_signals(current_date, current_hour, signals_data)
            success3 = self.logger.save_hourly_metrics(current_date, current_hour, metrics_data)

            if not (success1 and success2 and success3):
                self.logger.warning(f"Failed to save market analysis data for {symbol}")

        except Exception as e:
            self.logger.error(f"Error collecting market analysis data: {e}")

    def _aggregate_hourly_report_data(self):
        """Aggregate hourly report data from database files"""
        try:
            now = datetime.now()
            start_time = self.last_report_time

            # Calculate the reporting period
            report_hours = []
            current_time = start_time
            while current_time < now:
                report_hours.append(current_time.strftime('%H:00'))
                current_time += timedelta(hours=1)

            # Aggregate data from database for each trading type
            report_data = {
                'futures': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'spot': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'arbitrage': {'total_analyses': 0, 'opportunities_found': 0, 'trades_executed': 0, 'total_rejections': 0}
            }

            current_date = now.strftime('%Y-%m-%d')

            # Load market analysis data
            market_data = self.logger.mongo_manager.load_market_analysis(current_date)
            if market_data:
                for hour in report_hours:
                    if hour in market_data:
                        hour_data = market_data[hour]
                        trading_type = hour_data.get('trading_type', 'futures')

                        if trading_type in report_data:
                            report_data[trading_type]['total_analyses'] += hour_data.get('total_analyses', 0)

            # Load hourly metrics data
            metrics_data = self.logger.mongo_manager.load_hourly_metrics(current_date)
            if metrics_data:
                for hour in report_hours:
                    if hour in metrics_data:
                        hour_data = metrics_data[hour]
                        trading_type = hour_data.get('trading_type', 'futures')

                        if trading_type in report_data:
                            report_data[trading_type]['signals_generated'] += hour_data.get('signals_generated', 0)
                            report_data[trading_type]['total_rejections'] += hour_data.get('total_rejections', 0)
                            report_data[trading_type]['trades_opened'] += hour_data.get('trades_executed', 0)

            # For arbitrage, we'd need to implement similar logic if arbitrage data is stored
            # For now, arbitrage reports will show 0

            self.logger.debug(f"Aggregated hourly report data: {report_data}")
            return report_data

        except Exception as e:
            self.logger.error(f"Error aggregating hourly report data: {e}")
            return {
                'futures': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'spot': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'arbitrage': {'total_analyses': 0, 'opportunities_found': 0, 'trades_executed': 0, 'total_rejections': 0}
            }

    def _send_hourly_reports_from_db(self, report_data):
        """Send hourly reports using database data"""
        try:
            # Generate time range for the report
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=self.report_interval_hours)
            time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M UTC')}"

            # Send futures report
            self._send_futures_hourly_report_from_db(report_data['futures'], time_range)

            # Send spot report (if spot trading enabled)
            if hasattr(self.config, 'ENABLE_SPOT_TRADING') and self.config.ENABLE_SPOT_TRADING:
                self._send_spot_hourly_report_from_db(report_data['spot'], time_range)

            # Send arbitrage report (if arbitrage enabled)
            if hasattr(self.config, 'ENABLE_ARBITRAGE_SCANNER') and self.config.ENABLE_ARBITRAGE_SCANNER:
                self._send_arbitrage_hourly_report_from_db(report_data['arbitrage'], time_range)

        except Exception as e:
            self.logger.error(f"Error sending hourly reports from database: {e}")

    def _send_futures_hourly_report_from_db(self, futures_data, time_range):
        """Send futures hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY FUTURES REPORT
⏰ {time_range}

🔄 Market Analysis:
• Total Analyses: {futures_data['total_analyses']:,}
• Signals Generated: {futures_data['signals_generated']:,}
• Total Rejections: {futures_data['total_rejections']:,}
• Trades Opened: {futures_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(futures_data['signals_generated'] / max(futures_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(futures_data['trades_opened'] / max(futures_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to futures Telegram bot
            if self.telegram and hasattr(self.telegram, 'futures_bot') and self.telegram.futures_bot:
                self.telegram.futures_bot.send_message(
                    chat_id=self.config.TELEGRAM_FUTURES_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Futures hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Futures Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending futures hourly report from database: {e}")

    def _send_spot_hourly_report_from_db(self, spot_data, time_range):
        """Send spot hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY SPOT REPORT
⏰ {time_range}

💰 Market Analysis:
• Total Analyses: {spot_data['total_analyses']:,}
• Signals Generated: {spot_data['signals_generated']:,}
• Total Rejections: {spot_data['total_rejections']:,}
• Trades Opened: {spot_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(spot_data['signals_generated'] / max(spot_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(spot_data['trades_opened'] / max(spot_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to spot Telegram bot
            if self.telegram and hasattr(self.telegram, 'spot_bot') and self.telegram.spot_bot:
                self.telegram.spot_bot.send_message(
                    chat_id=self.config.TELEGRAM_SPOT_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Spot hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Spot Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending spot hourly report from database: {e}")

    def _send_arbitrage_hourly_report_from_db(self, arb_data, time_range):
        """Send arbitrage hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY ARBITRAGE REPORT
⏰ {time_range}

🔀 Arbitrage Activity:
• Opportunities Found: {arb_data['opportunities_found']:,}
• Trades Executed: {arb_data['trades_executed']:,}
• Total Rejections: {arb_data['total_rejections']:,}

📈 Performance:
• Execution Rate: {(arb_data['trades_executed'] / max(arb_data['opportunities_found'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to arbitrage Telegram bot
            if self.telegram and hasattr(self.telegram, 'arbitrage_bot') and self.telegram.arbitrage_bot:
                self.telegram.arbitrage_bot.send_message(
                    chat_id=self.config.TELEGRAM_ARBITRAGE_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Arbitrage hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Arbitrage Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending arbitrage hourly report from database: {e}")

    def _check_and_send_hourly_report(self):
        """Check if it's time to send hourly report and send if needed"""
        if not self.hourly_reports_enabled:
            return

        now = datetime.now()
        time_since_last_report = (now - self.last_report_time).total_seconds() / 3600  # Hours

        if time_since_last_report >= self.report_interval_hours:
            # Aggregate data from database for the reporting period
            report_data = self._aggregate_hourly_report_data()

            # Send hourly reports using database data
            self._send_hourly_reports_from_db(report_data)
            self.last_report_time = now

    def _send_hourly_reports(self):
        """Generate and send hourly reports to appropriate Telegram bots"""
        try:
            # Generate time range for the report
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=self.report_interval_hours)
            time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M UTC')}"

            # Send futures report
            self._send_futures_hourly_report(time_range)

            # Send spot report (if spot trading enabled)
            if hasattr(self.config, 'ENABLE_SPOT_TRADING') and self.config.ENABLE_SPOT_TRADING:
                self._send_spot_hourly_report(time_range)

            # Send arbitrage report (if arbitrage enabled)
            if hasattr(self.config, 'ENABLE_ARBITRAGE_SCANNER') and self.config.ENABLE_ARBITRAGE_SCANNER:
                self._send_arbitrage_hourly_report(time_range)

        except Exception as e:
            self.logger.error(f"Error sending hourly reports: {e}")

    def _send_futures_hourly_report(self, time_range):
        """Send futures hourly report to Telegram"""
        try:
            futures_data = self.hourly_metrics['futures']

            report_message = f"""
📊 HOURLY FUTURES REPORT
⏰ {time_range}

🔄 Market Analysis:
• Total Analyses: {futures_data['total_analyses']:,}
• Signals Generated: {futures_data['signals_generated']:,}
• Total Rejections: {futures_data['total_rejections']:,}
• Trades Opened: {futures_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(futures_data['signals_generated'] / max(futures_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(futures_data['trades_opened'] / max(futures_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to futures Telegram bot
            if self.telegram and hasattr(self.telegram, 'futures_bot') and self.telegram.futures_bot:
                self.telegram.futures_bot.send_message(
                    chat_id=self.config.TELEGRAM_FUTURES_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Futures hourly report sent to Telegram")
            else:
                self.logger.warning("Futures Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending futures hourly report: {e}")

    def _send_spot_hourly_report(self, time_range):
        """Send spot hourly report to Telegram"""
        try:
            spot_data = self.hourly_metrics['spot']

            report_message = f"""
📊 HOURLY SPOT REPORT
⏰ {time_range}

💰 Market Analysis:
• Total Analyses: {spot_data['total_analyses']:,}
• Signals Generated: {spot_data['signals_generated']:,}
• Total Rejections: {spot_data['total_rejections']:,}
• Trades Opened: {spot_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(spot_data['signals_generated'] / max(spot_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(spot_data['trades_opened'] / max(spot_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to spot Telegram bot
            if self.telegram and hasattr(self.telegram, 'spot_bot') and self.telegram.spot_bot:
                self.telegram.spot_bot.send_message(
                    chat_id=self.config.TELEGRAM_SPOT_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Spot hourly report sent to Telegram")
            else:
                self.logger.warning("Spot Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending spot hourly report: {e}")

    def _send_arbitrage_hourly_report(self, time_range):
        """Send arbitrage hourly report to Telegram"""
        try:
            arb_data = self.hourly_metrics['arbitrage']

            report_message = f"""
📊 HOURLY ARBITRAGE REPORT
⏰ {time_range}

🔀 Arbitrage Activity:
• Opportunities Found: {arb_data['opportunities_found']:,}
• Trades Executed: {arb_data['trades_executed']:,}
• Total Rejections: {arb_data['total_rejections']:,}

📈 Performance:
• Execution Rate: {(arb_data['trades_executed'] / max(arb_data['opportunities_found'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to arbitrage Telegram bot
            if self.telegram and hasattr(self.telegram, 'arbitrage_bot') and self.telegram.arbitrage_bot:
                self.telegram.arbitrage_bot.send_message(
                    chat_id=self.config.TELEGRAM_ARBITRAGE_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Arbitrage hourly report sent to Telegram")
            else:
                self.logger.warning("Arbitrage Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending arbitrage hourly report: {e}")

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
                print(f"    Final Shared Capital: ${self.total_capital:.2f}")
                print(f"    Total Strategy P&L: ${total_pnl:+.2f}")

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
        print("  APEX HUNTER V14 - V14.1-FIXED-LOGGING")
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
        else:
            print("🚀 Initializing LIVE TRADING mode...")

        self.engine = PaperTradingEngine(self.config, self.logger, self.telegram, mode=self.mode)
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
                self.engine.exchange
            )
        else:
            self.trailing_stop_engine = None
            
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
    
    def _sync_open_trades(self):
        """Startup Reconciliation Phase to catch 'Ghost Trades' that closed while bot was down."""
        try:
            conn = self.logger.db._get_connection(self.logger.db.main_db)
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            open_trades = cursor.fetchall()
            
            if not open_trades:
                conn.close()
                return
                
            self.logger.info(f"🔄 Reconciling {len(open_trades)} OPEN trades with Exchange...")
            
            for trade in open_trades:
                symbol = trade['symbol']
                trade_id = trade['trade_id']
                
                # Check actual exchange positions (wrap in try-except for varied exchange support)
                try:
                    positions = self.engine.exchange.get_positions(symbol)
                    active_pos = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
                    
                    if not active_pos:
                        self.logger.warning(f"👻 Ghost Trade detected! {symbol} closed while bot was offline.")
                        # Close out in SQLite
                        exit_time = datetime.utcnow().isoformat()
                        cursor.execute("UPDATE trades SET status = 'CLOSED', exit_time = ?, reason = 'exchange_closed_offline' WHERE trade_id = ?",
                                     (exit_time, trade_id))
                        self.logger.info(f"✅ Synced {symbol} as CLOSED.")
                except Exception as exchange_e:
                    # If exchange doesn't support fetchPositions (like Kucoin), we log and proceed 
                    # relying on the bot's standard risk checks later.
                    self.logger.warning(f"⚠️ Reconciliation partially skipped for {symbol}: Exchange doesn't support position fetching.")
                    
            conn.commit()
            conn.close()
        except Exception as e:
            self.logger.error(f"Failed to run Startup Reconciliation: {e}")

    def run(self, interval=60):
        """Run the bot"""
        self.running = True

        # Setup signal handler for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)

        # Run Startup Reconciliation Phase to handle ghost trades
        if hasattr(self, 'engine'):
            self._sync_open_trades()

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

                # Run spot analysis if enabled
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

                # Process Bot-Side Trailing Stops if enabled
                if getattr(self.config, 'TRAILING_TP_ENABLED', True) and getattr(self, 'trailing_stop_engine', None):
                    # For performance, only pass prices of top pairs we're currently monitoring
                    self.trailing_stop_engine.process_open_trades(self.engine.current_prices)

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
                signal = strategy.generate_signal(df)

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
