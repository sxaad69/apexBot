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
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5, StrategyA6
from notifications import TelegramNotificationManager
from risk import RiskManager
from core.spot_logger import SpotLogger
from core.spot_trading_engine import SpotTradingEngine
from risk.layers.trailing_stop import TrailingStopLayer
from risk.layers.portfolio_circuit_breaker import PortfolioCircuitBreaker
from risk.layers.portfolio_profit_ratchet import PortfolioProfitRatchet
from core.trade_manager import TradeManager



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
        
        # Parallel Execution Architecture properties
        import threading
        self.recent_liquidations = {}
        self.trade_lock = threading.Lock()

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
            trade_manager=self.trade_manager
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

    def get_top_pairs_by_volume(self, top_n=None, min_volume_usdt=None):
        """
        Fetch top N trading pairs by 24h volume

        Args:
            top_n: Number of top pairs to return (defaults to config if None)
            min_volume_usdt: Minimum 24h volume in USDT (defaults to config if None)

        Returns:
            List of trading pair symbols
        """
        from datetime import datetime, timedelta

        # Update cache every 15 minutes (faster alpha discovery)
        now = datetime.now()
        
        # Use provided args or fall back to config
        if top_n is None:
            top_n = int(getattr(self.config, 'FUTURES_AUTO_TOP_N', 30))
        if min_volume_usdt is None:
            min_volume_usdt = float(getattr(self.config, 'FUTURES_AUTO_MIN_VOLUME', 1000000))

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
            markets = self.exchange.exchange.markets
            
            for symbol, ticker in tickers.items():
                clean_symbol = symbol.upper()
                if 'USDT' not in clean_symbol:
                    continue

                # PHASE 16: Ensure symbol exists in the Futures market (skip spot-only leakage)
                if markets and symbol in markets:
                    market_info = markets[symbol]
                    # Filter for linear futures (USDT settled)
                    is_futures = market_info.get('future', False) or market_info.get('swap', False)
                    if not is_futures:
                        continue
                        
                    # --- FIX: Filter out inactive/non-trading symbols ---
                    if not market_info.get('active', True):
                        continue
                        
                    # Binance-specific status check
                    if market_info.get('info', {}).get('status') and market_info['info']['status'] != 'TRADING':
                        continue

                # Skip specific non-trading symbols if necessary
                if any(x in clean_symbol for x in ['BUSD', 'EUR', 'GBP', 'AUD']):
                    continue

                # Standard CCXT quoteVolume is preferred
                quote_volume = ticker.get('quoteVolume', 0)
                
                if not quote_volume and 'info' in ticker:
                    info = ticker['info']
                    for vol_key in ['quoteVolume', 'volume', 'vol', '24hVolume', 'quote_volume']:
                        if vol_key in info:
                            try:
                                quote_volume = float(info[vol_key])
                                if quote_volume > 0: break
                            except: continue

                if not quote_volume or quote_volume < min_volume_usdt:
                    continue

                usdt_pairs.append({
                    'symbol': symbol,
                    'volume': quote_volume
                })

            # Sort by volume (descending)
            usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)

            # Get top N base list
            top_pairs = [p['symbol'] for p in usdt_pairs[:top_n]]

            # --- PHASE 26: Inject Open Positions into Monitoring Cycle ---
            try:
                if hasattr(self.logger, 'db'):
                    conn = self.logger.db._get_connection(self.logger.db.main_db)
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT DISTINCT symbol FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
                        must_monitor = [row['symbol'] for row in cursor.fetchall()]
                    finally:
                        conn.close()
                    
                    if must_monitor:
                        # Merge and deduplicate
                        combined = list(set(top_pairs + must_monitor))
                        # Keep original volume order for top N, then append newcomers
                        top_pairs = combined
                        self.logger.info(f"📍 Position-Aware Sync: Added {len(must_monitor)} active trade symbols to monitoring cycle.")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to inject open positions into monitoring: {e}")

            # Update cache
            self.top_pairs_cache = top_pairs
            self.last_pairs_update = now

            if len(top_pairs) == 0:
                self.logger.warning(f"⚠️  MARKET DISCOVERY FAILED: Found 0 pairs matching 'USDT' with volume > ${min_volume_usdt:,.0f}.")
            else:
                self.logger.info(f"Found {len(top_pairs)} top pairs (min volume: ${min_volume_usdt:,.0f})")
                self.logger.info(f"Top 10: {', '.join(top_pairs[:10])}")

            return top_pairs

        except Exception as e:
            self.logger.error(f"Error fetching top pairs: {e}")
            return getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT', 'ETH/USDT'])

    def fetch_market_data(self, symbol='BTC/USDT', timeframe=None, limit=300):
        """Fetch market data using CCXT (exchange-agnostic)"""
        if timeframe is None:
            timeframe = self.config.TIMEFRAME
        
        # Normalize symbol for exchange specificity (e.g. ADA/USDT -> ADA/USDT:USDT if needed)
        try:
            exchange = self.exchange.exchange
            if symbol not in exchange.markets:
                # Try to find the actual symbol in the loaded markets (robust matching)
                for market_symbol in exchange.markets:
                    if market_symbol.split(':')[0] == symbol:
                        symbol = market_symbol
                        break
            
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            self.logger.error(f"Error fetching market data for {symbol}: {e}")
            return None
    
    def update_trailing_stops(self, symbol, current_price):
        """Update trailing stops for all positions on a symbol"""
        for position_key, position in list(self.positions.items()):
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

            else:  # sell position (SHORT)
                # Track lowest price since entry (most profitable price for a short)
                if current_price < position['lowest_price']:
                    position['lowest_price'] = current_price

                # For a SHORT: profit_percent is how much price has DROPPED from entry
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']

                # Activation: once we're >= activation threshold in profit
                if not position['trailing_stop_active'] and profit_percent >= activation_threshold:
                    position['trailing_stop_active'] = True
                    position['trailing_activation_price'] = current_price
                    # New stop is ABOVE lowest price by trailing_distance (locking in gains)
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    # Only valid if this new stop is BELOW the current stop_loss
                    # (i.e., we're locking in profit, not expanding our risk)
                    # For a SHORT, stop_loss is set ABOVE entry — new_stop should be well below entry
                    old_stop = position['stop_loss']
                    position['stop_loss'] = new_stop
                    self.logger.info(f"[{strategy_name}] {symbol} TRAILING ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | SL: ${old_stop:.5f} → ${new_stop:.5f}")

                # Ratchet: keep moving stop DOWN as price falls further (locking in MORE profit)
                if position['trailing_stop_active']:
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    # Ratchet only in the direction of more profit (lower stop for shorts)
                    if new_stop < position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING RATCHET @ ${current_price:.5f} | Trough: ${position['lowest_price']:.5f} | SL: ${old_stop:.5f} → ${new_stop:.5f}")






    def update_trailing_take_profit(self, symbol, current_price):
        """Update trailing take profit for all positions on a symbol."""
        if not getattr(self.config, 'TRAILING_TP_ENABLED', True):
            return

        for position_key, position in list(self.positions.items()):
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
                    self._persist_tp_watermark(position['trade_id'], peak=current_price)  # Phase 31

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 - tp_trailing_distance)
                        if new_tp > old_tp and new_tp > position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | TP: ${old_tp:.5f} → ${new_tp:.5f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "activation")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_peak_price'] * (1 - tp_trailing_distance)
                        if new_tp > position['take_profit'] and new_tp > position['entry_price']: # Move TP HIGHER for Long
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "ratchet")

            else:  # sell position
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_trough_price'] = None

                if position['trailing_tp_trough_price'] is None or current_price < position['trailing_tp_trough_price']:
                    position['trailing_tp_trough_price'] = current_price
                    self._persist_tp_watermark(position['trade_id'], trough=current_price)  # Phase 31

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 + tp_trailing_distance)
                        if new_tp < old_tp and new_tp < position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | TP: ${old_tp:.5f} → ${new_tp:.5f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "activation")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_trough_price'] * (1 + tp_trailing_distance)
                        if new_tp < position['take_profit'] and new_tp < position['entry_price']: # Move TP LOWER for Short
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "ratchet")

    def _persist_tp_update(self, trade_id: str, new_tp: float, current_price: float, event_type: str):
        """Helper to sync trailing take-profit changes to the DB."""
        try:
            import json
            from datetime import datetime
            trades = self.db.get_trades(status='OPEN')
            trade = next((t for t in trades if t['trade_id'] == trade_id), None)
            if trade:
                meta = json.loads(trade['metadata']) if trade['metadata'] else {}
                meta['trailing_tp_history'] = meta.get('trailing_tp_history', []) + [{
                    'price': new_tp, 
                    'trigger_price': current_price, 
                    'type': event_type,
                    'time': datetime.utcnow().isoformat()
                }]
                self.trade_manager.update_trade_params(trade_id, {
                    'take_profit': new_tp,
                    'metadata': meta
                })

                # --- [PHASE 15.4b: EXCHANGE-SIDE TP RATCHET] ---
                if self.mode == 'live' and getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                    # We need the current active TP order ID
                    # Re-fetch from DB to be safe, or get from in-memory positions
                    tp_order_id = None
                    sl_order_id = None
                    symbol = None
                    sl_side = None
                    qty = None
                    
                    for pos in self.positions.values():
                        if pos['trade_id'] == trade_id:
                            tp_order_id = pos.get('tp_order_id')
                            sl_order_id = pos.get('sl_order_id')
                            symbol = pos['symbol']
                            sl_side = 'sell' if pos['side'] == 'buy' else 'buy'
                            qty = pos['size']
                            break
                            
                    if tp_order_id and symbol:
                        try:
                            # 1. Try to cancel old TP
                            self.exchange.exchange.cancel_order(tp_order_id, symbol)
                            
                            # 2. SUCCESS: Place new TP. Use base asset qty from metadata, not dollar size.
                            tp_order_type = getattr(self.config, 'EXCHANGE_TP_ORDER_TYPE', 'TAKE_PROFIT_MARKET')
                            new_tp_price = new_tp
                            # Resolve base asset quantity
                            base_qty = None
                            for pos in self.positions.values():
                                if pos['trade_id'] == trade_id:
                                    # Try getting executed_qty from DB metadata first, fallback to calculating
                                    trades = self.db.get_trades(status='OPEN')
                                    t = next((t for t in trades if t['trade_id'] == trade_id), None)
                                    if t:
                                        meta = {}
                                        try: meta = json.loads(t['metadata']) if t['metadata'] else {}
                                        except: pass
                                        base_qty = meta.get('executed_qty')
                                    if not base_qty and pos.get('entry_price', 0) > 0:
                                        leverage = pos.get('leverage', 1)
                                        base_qty = (pos['size'] * leverage) / pos['entry_price']
                                    break
                            
                            if not base_qty:
                                self.logger.warning(f"⚠️ Could not resolve base quantity for {symbol} TP ratchet. Skipping.")
                                return
                            
                            try:
                                new_tp_price = float(self.exchange.exchange.price_to_precision(symbol, new_tp_price))
                                base_qty = float(self.exchange.exchange.amount_to_precision(symbol, base_qty))
                            except: pass
                            
                            new_tp_order = self.exchange.exchange.create_stop_market_order(
                                symbol=symbol,
                                side=sl_side,
                                amount=base_qty,
                                stopPrice=new_tp_price,
                                params={'reduceOnly': True}
                            )
                            
                            # 3. Save new ID
                            new_id = new_tp_order.get('id')
                            if new_id:
                                self.trade_manager.db.update_trade_order_ids(trade_id, tp_order_id=new_id)
                                for pos in self.positions.values():
                                    if pos['trade_id'] == trade_id: pos['tp_order_id'] = new_id
                                self.logger.info(f"🎯 EXCHANGE TP RATCHET SUCCESS: {symbol} -> ${new_tp_price}")
                        
                        except Exception as e:
                            # 4. CANCEL FAILED: Check if it was already filled (Race Condition)
                            try:
                                old_order = self.exchange.exchange.fetch_order(tp_order_id, symbol)
                                if old_order.get('status') == 'closed':
                                    self.logger.info(f"🎯 [RACE DETECTED] TP already filled for {symbol} during ratchet. Recording exit.")
                                    if sl_order_id:
                                        try: self.exchange.exchange.cancel_order(sl_order_id, symbol)
                                        except: pass
                                    self.trade_manager.record_exit(
                                        symbol=symbol, trade_id=trade_id, reason='take_profit',
                                        current_price=old_order.get('average') or current_price,
                                        order_response=old_order
                                    )
                                    # --- BUG FIX: Remove from in-memory positions to avoid ghost trade ---
                                    position_key = f"{next((p['strategy'] for p in self.positions.values() if p['trade_id'] == trade_id), None)}:{symbol}"
                                    if position_key in self.positions:
                                        del self.positions[position_key]
                                    return
                            except Exception as fetch_e:
                                self.logger.warning(f"Failed to verify old TP status after ratchet fail: {fetch_e}")
                            
                            self.logger.warning(f"⚠️ Trailing TP exchange update failed for {symbol}: {e}. Software fallback active.")
        except Exception as e:
            self.logger.error(f"Failed to persist Trailing TP update: {e}")

    def _persist_tp_watermark(self, trade_id: str, peak: float = None, trough: float = None):
        """Phase 31: Persist trailing TP high-water/low-water marks to metadata for restart recovery."""
        try:
            import json
            trades = self.db.get_trades(status='OPEN')
            trade = next((t for t in trades if t['trade_id'] == trade_id), None)
            if not trade:
                return
            meta = json.loads(trade['metadata']) if trade['metadata'] else {}
            if peak is not None:
                meta['trailing_tp_peak_price'] = peak
            if trough is not None:
                meta['trailing_tp_trough_price'] = trough
            # Also persist the active flag from memory (look it up by trade_id)
            for pos in self.positions.values():
                if pos.get('trade_id') == trade_id:
                    meta['trailing_tp_active'] = pos.get('trailing_tp_active', False)
                    if pos.get('trailing_tp_activation_price'):
                        meta['trailing_tp_activation_price'] = pos['trailing_tp_activation_price']
                    break
            self.trade_manager.update_trade_params(trade_id, {'metadata': meta})
        except Exception as e:
            self.logger.warning(f"Failed to persist TP watermark: {e}")


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
        
        # 0. Concurrent Cooldown Matrix Restriction
        import time
        cooldown_minutes = getattr(self.config, 'FUTURES_SYMBOL_COOLDOWN_MINUTES', 15)
        last_liquidation = self.recent_liquidations.get(symbol, 0)
        elapsed_minutes = (time.time() - last_liquidation) / 60
        if elapsed_minutes < cooldown_minutes:
            self.logger.warning(f"🚫 [COOLDOWN REJECTED] {symbol} hit an exit {elapsed_minutes:.1f}m ago. Under {cooldown_minutes}m cooling off period.")
            return

        # 1. Triple-Check Global Symbol Guard (Phase 28)
        # Prevent taking multiple positions on the same coin across different strategies
        
        # A) In-memory check
        if any(p['symbol'] == symbol for p in self.positions.values()):
            self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (in-memory)")
            return
        
        # B) SQLite check (survives bot restarts)
        if hasattr(self.logger, 'db'):
            try:
                conn = self.logger.db._get_connection(self.logger.db.main_db)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE symbol = ? AND status = 'OPEN'", (symbol,))
                    open_count = cursor.fetchone()[0]
                finally:
                    conn.close()
                if open_count > 0:
                    self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (SQLite OPEN trade exists)")
                    return
            except Exception as guard_e:
                self.logger.warning(f"[{strategy_name}] Symbol guard SQLite check failed: {guard_e}")

        # C) Binance Live Check (Ultimate Source of Truth)
        if self.mode == 'live' and self.exchange:
            try:
                positions = self.exchange.get_positions()
                # Check for any non-zero position on this symbol
                live_pos = next((p for p in positions if p['symbol'] == symbol), None)
                if live_pos and abs(float(live_pos.get('contracts', 0) or 0)) > 0:
                    self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (BINANCE position exists)")
                    return
            except Exception as bin_e:
                self.logger.warning(f"[{strategy_name}] Binance guard check failed: {bin_e}")

        position_key = f"{strategy_name}:{symbol}"

        # Entry
        if signal and position_key not in self.positions:
            # Phase 32: Set Margin Mode before entry
            if self.mode == 'live' and self.exchange:
                margin_mode = getattr(self.config, 'FUTURES_MARGIN_MODE', 'ISOLATED')
                self.exchange.set_margin_mode(symbol, margin_mode)

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

            # Unified drawdown tracking for the shared pool (Equity-Based)
            # We use Equity (Available Balance + Open Margin + Unrealized P&L) 
            # so that capital allocation isn't counted as a loss, but market drops are.
            current_capital = self.total_capital
            
            # Calculate total value of open positions (Margin + P&L)
            # NOTE: Use 'pos_symbol' NOT 'symbol' to avoid overwriting the outer signal symbol!
            open_positions_value = 0
            for pos in self.positions.values():
                pos_symbol = pos['symbol']  # ← renamed to avoid clobbering outer `symbol`
                current_price = self.current_prices.get(pos_symbol, pos['entry_price'])
                
                # Simple leveraged P&L calculation for paper mode
                price_move = (current_price - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
                if pos['side'].lower() == 'sell':
                    price_move = -price_move
                
                unrealized_pnl = pos['size'] * price_move * pos.get('leverage', 1)
                open_positions_value += (pos['size'] + unrealized_pnl)
            
            current_equity = current_capital + open_positions_value
            
            peak_capital = self.peak_balance
            drawdown_percent = 0
            if current_equity < peak_capital:
                drawdown_percent = ((peak_capital - current_equity) / peak_capital) * 100
            
            account_state = {
                'total_balance': current_equity, # Pass equity as total balance for risk layers
                'available_balance': current_capital,
                'drawdown_percent': drawdown_percent,
                'peak_balance': peak_capital,
                'open_positions': list(self.positions.values()),
                'open_positions_count': len(self.positions),
                'recent_trades': self.trades[-20:],
                'current_time': datetime.now()
            }
            
            # Debug: Log account state for visibility
            self.logger.debug(
                f"[{strategy_name}] Account state: "
                f"available=${account_state['available_balance']:.2f}, "
                f"equity=${account_state['total_balance']:.2f}, "
                f"drawdown={account_state['drawdown_percent']:.2f}%"
            )

            # Validate through risk management system (11 layers)
            approved_params = self.risk_manager.evaluate_trade(trade_params, account_state)

            if approved_params is None:
                # Trade rejected by risk management
                self.logger.warning(f"[{strategy_name}] {symbol} TRADE REJECTED by risk management")
                return

            # Approved trade - execute
            try:
                # Initialize safety variables to prevent UnboundLocalError
                position = None
                entry_fee = 0.0
                
                entry_price = approved_params['entry_price']
                size = approved_params['size']
                leverage = approved_params.get('leverage', leverage)
                
                # --- [PHASE 15: LIVE ENTRY BRIDGE] ---
                # This is the "Entry" side of the live trading connection.
                # It only triggers if MODE=live and an exchange is connected.
                order_metadata = {}
                if self.mode == 'live' and self.exchange:
                    try:
                        self.logger.info(f"🚀 LIVE ENTRY: Placing {approved_params['side']} order for {symbol}...")
                        
                        # Calculate quantity for CCXT (Margin * Leverage / Price)
                        quantity = (size * leverage) / entry_price
                        
                        # SAFETY: Ensure total order value (quantity * price) roughly matches intended risk
                        total_value = quantity * entry_price
                        max_allowed_value = (size * leverage) * 1.1 # 10% buffer for slippage
                        if total_value > max_allowed_value:
                            error_msg = f"❌ FORCED ABORT: Calculated value ${total_value:.2f} exceeds safety limit ${max_allowed_value:.2f}"
                            self.logger.critical(error_msg)
                            raise ValueError(error_msg)
                        
                        # Format precision to fix "minimum amount precision" errors
                        try:
                            quantity = float(self.exchange.exchange.amount_to_precision(symbol, quantity))
                        except Exception:
                            pass
                            
                        if quantity <= 0:
                            raise ValueError(f"Calculated target quantity ({quantity}) too small after formatting.")
                            
                        # --- FIX: Check against exchange max quantity limits ---
                        if symbol in self.exchange.exchange.markets:
                            limits = self.exchange.exchange.markets[symbol].get('limits', {})
                            max_qty = limits.get('amount', {}).get('max')
                            if max_qty and quantity > max_qty:
                                self.logger.warning(f"⚠️ Quantity {quantity} exceeds max {max_qty} for {symbol}. Capping.")
                                quantity = max_qty
                            
                        # 0. Set Leverage on Exchange (CRITICAL SYNC)
                        try:
                            # Standardize leverage to int as required by some CCXT implementations
                            self.exchange.exchange.set_leverage(int(leverage), symbol)
                            self.logger.info(f"⚙️ Leverage set to {int(leverage)}x for {symbol} on exchange.")
                        except Exception as lev_e:
                            self.logger.warning(f"⚠️ Failed to set leverage for {symbol}: {lev_e}")

                        # 1. Place Market Entry Order
                        order = self.exchange.exchange.create_order(
                            symbol=symbol,
                            type='market',
                            side=approved_params['side'].lower(),
                            amount=quantity
                        )
                        
                        # --- [PHASE 15.1: ENTRY GROUNDING via TradeManager] ---
                        position = self.trade_manager.record_entry(
                            symbol=symbol,
                            strategy_name=strategy_name,
                            side=approved_params['side'],
                            size=size,
                            leverage=leverage,
                            stop_loss=approved_params['stop_loss'],
                            take_profit=approved_params['take_profit'],
                            order_response=order,
                            planned_price=entry_price,
                            confidence=approved_params.get('confidence', 0.0),
                            stop_loss_roe=approved_params.get('stop_loss_roe', 5.0)
                        )
                        
                        # Use grounded info for SL placement if filled
                        if order and order.get('average'):
                            entry_price = float(order['average'])
                        
                        self.logger.info(f"✅ LIVE ENTRY SUCCESS: {order.get('id')} ({quantity} {symbol}) at {entry_price}")
                        
                        # --- [PHASE 15.2: EXCHANGE-SIDE SL/TP PLACEMENT] ---
                        # Only place hard exchange orders if ENABLE_EXCHANGE_STOPS is True
                        sl_order_id = None
                        tp_order_id = None
                        
                        if getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                            sl_side = 'sell' if approved_params['side'].lower() == 'buy' else 'buy'
                            sl_price = approved_params['stop_loss']
                            
                            try:
                                sl_price = float(self.exchange.exchange.price_to_precision(symbol, sl_price))
                            except Exception:
                                pass
                                
                            # 2. Place Hard Stop Loss on Exchange instantaneously
                            try:
                                sl_order = self.exchange.exchange.create_stop_market_order(
                                    symbol=symbol,
                                    side=sl_side,
                                    amount=quantity,
                                    stopPrice=sl_price,
                                    params={'reduceOnly': True}
                                )
                                sl_order_id = sl_order.get('id') if sl_order else None
                                self.logger.info(f"🛡️ HARD STOP PLACED: {sl_side.upper()} {symbol} @ {sl_price} (ID: {sl_order_id})")
                            except Exception as sl_e:
                                self.logger.warning(f"⚠️ Failed to place hard Stop Loss on exchange: {sl_e}. Bot safety logic still active.")
                                
                            # 3. Place Hard Take Profit on Exchange
                            tp_price = approved_params['take_profit']
                            try:
                                tp_price = float(self.exchange.exchange.price_to_precision(symbol, tp_price))
                            except Exception:
                                pass
                                
                            try:
                                tp_order = self.exchange.exchange.create_stop_market_order(
                                    symbol=symbol,
                                    side=sl_side,
                                    amount=quantity,
                                    stopPrice=tp_price,
                                    params={'reduceOnly': True}
                                )
                                tp_order_id = tp_order.get('id') if tp_order else None
                                self.logger.info(f"🎯 HARD TP PLACED: {sl_side.upper()} {symbol} @ {tp_price} (ID: {tp_order_id})")
                            except Exception as tp_e:
                                self.logger.warning(f"⚠️ Failed to place hard Take Profit on exchange: {tp_e}. Bot safety logic still active.")
                        
                        # 4. Save both order IDs to the DB (Trade Manager)
                        if sl_order_id or tp_order_id:
                            self.trade_manager.db.update_trade_order_ids(
                                position['trade_id'],
                                sl_order_id=sl_order_id,
                                tp_order_id=tp_order_id
                            )
                            # Update in-memory reference too
                            position['sl_order_id'] = sl_order_id
                            position['tp_order_id'] = tp_order_id
                            
                    except Exception as e:
                        self.logger.error(f"🚨 LIVE ENTRY ATTEMPT FAILED for {symbol}: {e}")
                        return

                # --- UNIFIED POSITION TRACKING ---
                with self.trade_lock:
                    if self.mode == 'paper':
                        # Record entry via TradeManager (handles DB persistence)
                        position = self.trade_manager.record_entry(
                            symbol=symbol,
                            strategy_name=strategy_name,
                            side=approved_params['side'],
                            size=size,
                            leverage=leverage,
                            stop_loss=approved_params['stop_loss'],
                            take_profit=approved_params['take_profit'],
                            planned_price=entry_price,
                            confidence=approved_params.get('confidence', 0.0),
                            stop_loss_roe=approved_params.get('stop_loss_roe', 5.0)
                        )
                    
                    # Update Capital accounting ONLY if it's a valid position
                    if not position or 'symbol' not in position:
                        self.logger.error(f"🚨 ATOMIC BOOKING FAILED: Trade manager returned invalid position object for {symbol}. Rejecting.")
                        return
                        
                    fee_percent = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
                    entry_fee = size * fee_percent
                    self.total_capital -= (size + entry_fee)
    
                    self.positions[position_key] = position
                
                self.logger.info(f"[{strategy_name}] {symbol} BALANCE DEDUCTED: ${size + entry_fee:.2f} (Entry: ${size:.2f} + Fee: ${entry_fee:.2f})")
                self.logger.info(f"[{strategy_name}] {symbol} ENTRY {signal['side'].upper()} @ ${position['entry_price']:.2f} (Leverage: {leverage}x) ✅ Risk Approved")

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
                    trade_id=position['trade_id'],
                    total_capital=self.total_capital,
                    metadata=order_metadata
                )
            except Exception as e:
                self.logger.error(f"🚨 CRITICAL: Booking Failure for {symbol} | Error: {e}", exc_info=True)
                return

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

            # --- [PHASE 15.3: EXCHANGE-SIDE SL/TP POLLING] ---
            # If we placed exchange orders, check their status FIRST before software checks
            exchange_exit_triggered = False
            exchange_exit_reason = None
            filled_order = None
            
            if self.mode == 'live' and getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                sl_id = position.get('sl_order_id')
                tp_id = position.get('tp_order_id')
                
                if sl_id or tp_id:
                    try:
                        # 1. Check SL
                        if sl_id:
                            sl_status = self.exchange.exchange.fetch_order(sl_id, symbol)
                            if sl_status.get('status') == 'closed':
                                exchange_exit_triggered = True
                                exchange_exit_reason = 'stop_loss'
                                filled_order = sl_status
                                # Cancel orphan TP
                                if tp_id:
                                    try: self.exchange.exchange.cancel_order(tp_id, symbol)
                                    except: pass
                        
                        # 2. Check TP (if SL didn't fill)
                        if not exchange_exit_triggered and tp_id:
                            tp_status = self.exchange.exchange.fetch_order(tp_id, symbol)
                            if tp_status.get('status') == 'closed':
                                exchange_exit_triggered = True
                                exchange_exit_reason = 'take_profit'
                                filled_order = tp_status
                                # Cancel orphan SL
                                if sl_id:
                                    try: self.exchange.exchange.cancel_order(sl_id, symbol)
                                    except: pass
                                    
                        # 3. If either filled on exchange, bypass software checks and record exit
                        if exchange_exit_triggered:
                            self.logger.info(f"⚡ EXCHANGE-SIDE EXIT DETECTED: {symbol} hit {exchange_exit_reason}")
                            try:
                                # Ensure both IDs are cleared in DB
                                self.trade_manager.db.update_trade_order_ids(position['trade_id'], sl_order_id=None, tp_order_id=None)
                            except Exception as e:
                                self.logger.warning(f"Failed to clear order IDs for {symbol}: {e}")
                                
                            exit_result = self.trade_manager.record_exit(
                                symbol=symbol,
                                trade_id=position['trade_id'],
                                reason=exchange_exit_reason,
                                current_price=filled_order.get('average') or filled_order.get('price') or current_price,
                                order_response=filled_order
                            )
                            
                            if exit_result and exit_result.get('verified'):
                                self.total_capital += exit_result.get('capital_return', 0)
                                is_win = exit_result['net_pnl'] > 0
                                self.risk_manager.record_trade_result(is_win, exit_result['net_pnl'])
                                if self.total_capital > self.peak_balance:
                                    self.peak_balance = self.total_capital
                                    self.risk_manager.update_peak_balance(self.total_capital)
                                    if hasattr(self.logger, 'db'):
                                        self.logger.db.set_setting('peak_balance', self.total_capital)
                            
                            import time
                            self.recent_liquidations[symbol] = time.time()
                            closed_positions.append(position_key)
                            continue # Skip the rest of the loop for this position
                            
                    except Exception as e:
                        self.logger.warning(f"⚠️ Failed to poll exchange orders for {symbol}: {e}. Falling back to software check.")

            # --- SOFTWARE EXIT CHECKS (Fallback) ---
            # Update Trailing Stop Ratchet BEFORE Software Check
            if getattr(self.config, 'TRAILING_TP_ENABLED', True) and getattr(self, 'trailing_stop_engine', None):
                self.trailing_stop_engine.update_position_ratchet(position, current_price)

            # Check for failed previous exits (PENDING_EXIT)
            is_pending_retry = position.get('status') == 'PENDING_EXIT'
            should_exit, reason = self.check_position_exit(position, current_price)

            if should_exit or is_pending_retry:
                if is_pending_retry:
                    self.logger.info(f"🔄 Retrying failed exit for {symbol}...")
                
                strategy_name = position['strategy']
                leverage = position.get('leverage', 1)

                # --- LIVE EXECUTION (Phase 14) ---
                close_order = None
                if self.mode == 'live':
                    try:
                        self.logger.system(f"🚀 [LIVE] Executing Market Close for {symbol} ({reason})")
                        close_order = self.exchange.close_position(symbol)
                    except Exception as e:
                        self.logger.error(f"🚨 LIVE EXIT FAILED for {symbol}: {e}")
                        # Mark as PENDING_EXIT in SQLite for infinite retry
                        if hasattr(self.logger, 'db'):
                            conn = self.logger.db._get_connection(self.logger.db.main_db)
                            try:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE trades SET status = 'PENDING_EXIT' WHERE trade_id = ?", (position.get('trade_id'),))
                                conn.commit()
                            finally:
                                conn.close()
                        position['status'] = 'PENDING_EXIT'
                        self.positions[position_key] = position
                        continue # Skip capital updates until successfully closed

                # --- UNIFIED EXIT FINALIZATION via TradeManager ---
                exit_result = self.trade_manager.record_exit(
                    symbol=symbol,
                    trade_id=position['trade_id'],
                    reason=reason,
                    current_price=current_price,
                    order_response=close_order
                )

                if exit_result and exit_result.get('verified'):
                    # Update Memory State & Capital
                    self.total_capital += exit_result.get('capital_return', 0)
                    self.logger.info(f"💰 CAPITAL RECOVERED: ${exit_result['capital_return']:.2f} (including Sync P&L)")

                    # Record result with risk manager
                    is_win = exit_result['net_pnl'] > 0
                    self.risk_manager.record_trade_result(is_win, exit_result['net_pnl'])

                    # Update peak balance and drawdown tracking
                    if self.total_capital > self.peak_balance:
                        self.peak_balance = self.total_capital
                        self.risk_manager.update_peak_balance(self.total_capital)
                        if hasattr(self.logger, 'db'):
                            self.logger.db.set_setting('peak_balance', self.total_capital)
                
                # Remove from memory loop
                import time
                self.recent_liquidations[symbol] = time.time()
                closed_positions.append(position_key)
                continue
                # (Legacy code removed)

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
                    trade_id=position.get('trade_id'),
                    total_capital=self.total_capital
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

            if mark_price and mark_price > 0:
                self.current_prices[symbol] = mark_price
                
                # Fireboard Isolation for Exits
                try:
                    self.update_trailing_stops(symbol, mark_price)
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
            self.logger.error(f"Failed to fetch market data for {symbol}")
            return

        current_price = df.iloc[-1]['close']
        self.current_prices[symbol] = current_price

        # Fallback check exits just in case global polling missed it or it wasn't open yet
        if not mark_price and not entry_only:
            try:
                self.update_trailing_stops(symbol, current_price)
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
                signal = strategy.generate_signal(df)

                if signal:
                    self.execute_paper_trade(signal, strategy.name, symbol)
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

    def _aggregate_hourly_report_data(self):
        """Aggregate hourly report data from activity_log.db and apex_hunter.db"""
        try:
            now = datetime.now()
            start_time = self.last_report_time
            
            report_data = {
                'futures': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'spot': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'arbitrage': {'total_analyses': 0, 'opportunities_found': 0, 'trades_executed': 0, 'total_rejections': 0}
            }

            # 1. Pull Scanning Data from activity_log.db
            try:
                import sqlite3, json
                conn = sqlite3.connect('data/activity_log.db')
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Query sweep summaries for the reporting period
                start_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("SELECT metadata FROM activity_log WHERE type = 'sweep_summary' AND timestamp >= ?", (start_str,))
                
                for row in cursor.fetchall():
                    meta = json.loads(row['metadata'])
                    # Aggregate Analysis Count
                    scanned = meta.get('symbols_scanned', 0)
                    report_data['futures']['total_analyses'] += scanned
                    
                    # Aggregate Rejection Count
                    rejections = meta.get('strategy_rejections', {})
                    for strat_rej in rejections.values():
                        for sym_list in strat_rej.values():
                            report_data['futures']['total_rejections'] += len(sym_list)
                
                conn.close()
            except Exception as e:
                self.logger.error(f"Error pulling scanning data from activity_log: {e}")

            # 2. Pull Trade Data from apex_hunter.db
            try:
                cursor = self.db.conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM trades WHERE entry_time >= ?", (start_time.isoformat(),))
                report_data['futures']['trades_opened'] = cursor.fetchone()[0]
                report_data['futures']['signals_generated'] = report_data['futures']['trades_opened'] 
            except Exception as e:
                self.logger.error(f"Error pulling trade data: {e}")

            return report_data

        except Exception as e:
            self.logger.error(f"Error aggregating hourly report data: {e}")
            return report_data

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
                    message=report_message.strip(),
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
                    message=report_message.strip(),
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
                    message=report_message.strip(),
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
    
    def _sync_open_trades(self):
        """
        Exchange-First Architecture: Startup Phase.
        1. Grabs Global Position State from Exchange (The absolute truth).
        2. Drops DB Ghost Trades that aren't on the exchange.
        3. ADOPTS Exchange trades that aren't in the DB.
        4. Hydrates synced memory.
        """
        try:
            # 1. Fetch Global Exchange State ONCE
            global_positions = []
            if self.mode == 'live' or getattr(self.config, 'FUTURES_STRICT_SYNC', False):
                try:
                    self.logger.info("🌐 Fetching Global Positions from Exchange for absolute sync...")
                    ex_positions = self.engine.exchange.get_positions()
                    global_positions = [p for p in ex_positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                except Exception as e:
                    self.logger.warning(f"⚠️ Exchange sync failed on startup: {e}. Falling back to DB state.")
                    # Keep empty list if failed so we don't accidentally close all DB trades, 
                    # but wait, if it fails we might close valid DB trades because list is empty!
                    global_positions = None # None means API failed, distinguish from [] which means 0 positions

            conn = self.logger.db._get_connection(self.logger.db.main_db)
            try:
                cursor = conn.cursor()
                
                # Fetch both OPEN and PENDING_EXIT trades
                cursor.execute("SELECT * FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
                db_trades = cursor.fetchall()
                
                global_symbols = {}
                if global_positions is not None:
                    # Map standard "BTC/USDT" and binance "BTCUSDT" formats for safety
                    global_symbols = {
                        p.get('symbol', '').replace('/', ''): p 
                        for p in global_positions
                    }
                    
                self.logger.info(f"🔄 Reconciling {len(db_trades)} DB trades against Global State...")
                
                # --- PHASE 2: GARBAGE COLLECTION (Drop Ghost Trades) ---
                for db_trade in db_trades:
                    symbol = db_trade['symbol']
                    clean_symbol = symbol.replace('/', '')
                    trade_id = db_trade['trade_id']
                    strategy_name = db_trade['strategy']
                    position_key = f"{strategy_name}:{symbol}"
                    
                    is_closed_on_exchange = False
                    if global_positions is not None:
                        if clean_symbol not in global_symbols:
                            is_closed_on_exchange = True

                    if is_closed_on_exchange:
                        self.logger.warning(f"👻 Garbage Collection: {symbol} ({strategy_name}) is missing from Exchange. Closing in local DB.")
                        exit_time = datetime.utcnow().isoformat()
                        cursor.execute("UPDATE trades SET status = 'CLOSED', exit_time = ?, reason = 'exchange_closed_offline' WHERE trade_id = ?",
                                     (exit_time, trade_id))
                        self.logger.info(f"✅ Synced {symbol} as CLOSED.")
                    else:
                        # HYDRATE into engine memory

                        import json
                        from datetime import datetime as dt
                        
                        # Convert SQLite Row to dict and prepare for engine
                        db_trade = dict(db_trade)
                        metadata = {}
                        try:
                            if db_trade['metadata']:
                                metadata = json.loads(db_trade['metadata'])
                        except: pass
                        
                        position = {
                            'trade_id': db_trade['trade_id'],
                            'symbol': db_trade['symbol'],
                            'side': db_trade['side'],
                            'entry_price': db_trade['entry_price'],
                            'entry_time': dt.fromisoformat(db_trade['entry_time']) if db_trade['entry_time'] else dt.now(),
                            'leverage': db_trade['leverage'],
                            'stop_loss': db_trade['stop_loss'],
                            'take_profit': db_trade['take_profit'],
                            'strategy': db_trade['strategy'],
                            'size': db_trade['size'] if 'size' in db_trade.keys() else metadata.get('size', 0),
                            'status': db_trade['status'],
                            # Trailing stop reconstruction
                            'highest_price': db_trade['highest_price'] or db_trade['entry_price'],
                            'lowest_price': db_trade['lowest_price'] or db_trade['entry_price'],
                            'trailing_stop_active': bool(db_trade['trailing_stop_active']),
                            'trailing_stop_price': db_trade['trailing_stop_price'],
                            'original_stop_loss': db_trade['stop_loss'],
                            # Phase 31: Restore Trailing TP state from persisted metadata
                            'trailing_tp_active': metadata.get('trailing_tp_active', False),
                            'trailing_tp_peak_price': metadata.get('trailing_tp_peak_price', None),
                            'trailing_tp_trough_price': metadata.get('trailing_tp_trough_price', None),
                            'trailing_tp_activation_price': metadata.get('trailing_tp_activation_price', None),
                            # Exchange-Side SL/TP persistence (Phase 15)
                            'sl_order_id': db_trade.get('sl_order_id'),
                            'tp_order_id': db_trade.get('tp_order_id')
                        }
                        
                        # Add back to active memory
                        self.engine.positions[position_key] = position
                        self.logger.info(f"🔌 HYDRATED position: {position_key} (ID: {trade_id[:8]}...)")

                # --- PHASE 3: ADOPTION (Import untracked Exchange positions) ---
                if global_positions is not None:
                    db_symbols = {d['symbol'].replace('/', '') for d in db_trades}
                    for g_pos in global_positions:
                        g_sym = g_pos.get('symbol', '').replace('/', '')
                        if not g_sym or g_sym in db_symbols:
                            continue
                            
                        self.logger.warning(f"🛸 ADOPTION: Untracked live position found on Exchange for {g_sym}! Adopting into Engine.")
                        try:
                            side = 'sell' if float(g_pos.get('contracts', 0) or 0) < 0 else 'buy'
                            entry_price = float(g_pos.get('entryPrice', 0))
                            contracts_size = abs(float(g_pos.get('contracts', 0)))
                            notional_value = contracts_size * entry_price
                            
                            # Automatically record it using trade manager 
                            # Convert to standard format with slash if possible (Binance symbol repair)
                            standard_symbol = g_pos.get('symbol')
                            if not '/' in standard_symbol:
                                standard_symbol = standard_symbol.replace('USDT', '/USDT')
                            
                            # Calculate 5% ROE Hard Stop for adopted positions
                            lev = int(g_pos.get('leverage', 1))
                            max_move = (5.0 / lev) / 100
                            if side == 'buy':
                                ad_sl = entry_price * (1 - max_move)
                            else:
                                ad_sl = entry_price * (1 + max_move)

                            adopted_pos = self.engine.trade_manager.record_entry(
                                symbol=standard_symbol,
                                strategy_name='MANUAL_ADOPT',
                                side=side,
                                size=notional_value,
                                leverage=lev,
                                stop_loss=ad_sl,
                                take_profit=None,
                                order_response=g_pos,
                                planned_price=entry_price
                            )
                            # Ensure tracking is mounted in memory
                            if adopted_pos:
                                self.engine.positions[f"MANUAL_ADOPT:{standard_symbol}"] = adopted_pos
                                self.logger.info(f"🧬 Successfully adopted {standard_symbol} into active tracking matrix.")
                        except Exception as e:
                            self.logger.error(f"Failed to adopt unmatched position {g_sym}: {e}", exc_info=True)
                        
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Failed to run Startup Hydration/Reconciliation: {e}")

    def _run_priority_exit_thread(self):
        """Continuous dedicated Risk Engine thread guarantees 0ms stops."""
        import time
        self.logger.info("🛡️ Priority Exit Sentinel Thread activated.")
        while self.running:
            try:
                # 1. Fetch Global Data once
                global_positions = None
                if self.mode == 'live' or getattr(self.config, 'FUTURES_STRICT_SYNC', False):
                    try:
                        ex_positions = self.engine.exchange.get_positions()
                        global_positions = [p for p in ex_positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                    except Exception as e:
                        # Log lightly to avoid WSS burst spam
                        pass

                # 2. Iterate Memory instantly
                active_symbols = list(set([p['symbol'] for p in self.engine.positions.values()]))
                for symbol in active_symbols:
                    if not self.running: break
                    try:
                        self.engine.run_cycle(symbol, global_positions=global_positions, exit_only=True)
                    except Exception as e:
                        self.logger.error(f"🚨 CRITICAL: Exit Fireboard failure for {symbol}", exc_info=True)
                        
            except Exception as e:
                self.logger.error(f"Priority Exit Thread Error: {e}", exc_info=True)
            
            # Sleep to strictly enforce Cooldown matrix and API constraints (2000ms delay)
            time.sleep(2)

    def run(self, interval=60):
        """Run the bot"""
        self.running = True

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

        # --- PARALLEL RISK ENGINE START ---
        self.sentinel_thread = threading.Thread(
            target=self._run_priority_exit_thread,
            name="PriorityExitSentinel",
            daemon=True
        )
        self.sentinel_thread.start()

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
                                    for strategy_name, reason in result['rejections'].items():
                                        if strategy_name not in sweep_stats['strategy_rejections']:
                                            sweep_stats['strategy_rejections'][strategy_name] = {}
                                        if reason not in sweep_stats['strategy_rejections'][strategy_name]:
                                            sweep_stats['strategy_rejections'][strategy_name][reason] = []
                                        sweep_stats['strategy_rejections'][strategy_name][reason].append(sym)
                                
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
                
                # Check if it's time to send hourly report
                self.engine._check_and_send_hourly_report()

                # Save current active positions for dashboard with live prices
                self.logger.save_active_positions(self.engine.positions, self.engine.current_prices)

                # Log status with high-precision price and per-position profit %
                total_pnl = self.engine.total_capital - getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)
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

        # Signal the ratchet monitor to stop cleanly before the loop closes
        if hasattr(self, 'engine') and hasattr(self.engine, 'profit_ratchet'):
            self.engine.profit_ratchet.stop_event.set()
            self.logger.info("🛡️ Ratchet monitor stop signal sent.")

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
