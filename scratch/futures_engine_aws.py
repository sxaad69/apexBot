#!/usr/bin/env python3
"""
Futures Trading Engine - Coordinates signal processing and order execution for futures.
Supports both Paper (Virtual) and Live (API) capital modes.
"""

import threading
import json
import time
from datetime import datetime, timedelta
import pandas as pd

from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5, StrategyA6
from risk import RiskManager
from risk.layers.portfolio_profit_ratchet import PortfolioProfitRatchet
from core.trade_manager import TradeManager

class FuturesTradingEngine:
    """
    Futures Trading Engine - Simulates or executes trading with live market data.
    Supports both Paper (Virtual) and Live (API) capital modes.
    """

    def __init__(self, config, logger, telegram, mode='paper', report_manager=None):
        self.config = config
        self.logger = logger
        self.telegram = telegram
        self.mode = mode
        self.report_manager = report_manager
        
        # Parallel Execution Architecture properties
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

        # Strategies are now explicitly controlled by individual flags in config.py
        # No automatic fallback to prevent accidental mass-enablement
        
        # Inject exchange client into strategies for microstructure analysis (A5)
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

    def _cleanup_cooldown_matrix(self):
        """Phase 40: Clean up the cooldown matrix to prevent memory bloat."""
        import time
        try:
            cooldown_minutes = getattr(self.config, 'FUTURES_SYMBOL_COOLDOWN_MINUTES', 15)
            current_time = time.time()
            
            # Identify expired entries
            expired_symbols = [
                symbol for symbol, last_time in self.recent_liquidations.items()
                if (current_time - last_time) / 60 > cooldown_minutes
            ]
            
            # Remove them
            for symbol in expired_symbols:
                del self.recent_liquidations[symbol]
            
            if expired_symbols:
                self.logger.debug(f"🧹 Cleaned up {len(expired_symbols)} expired entries from cooldown matrix.")
        except Exception as e:
            self.logger.warning(f"Failed to cleanup cooldown matrix: {e}")


    def check_position_exit(self, position, current_price):
        """Check if position should be exited"""
        sl = position.get('stop_loss')
        tp = position.get('take_profit')
        
        if position['side'] == 'buy':
            if sl is not None and current_price <= sl:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
            elif tp is not None and current_price >= tp:
                return True, 'take_profit'
        else:  # sell (SHORT)
            if sl is not None and current_price >= sl:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
            elif tp is not None and current_price <= tp:
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

    def execute_trade(self, signal, strategy_name, symbol):
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
                
                # --- [PHASE 15: WORKING LIVE ENTRY] ---
                if self.mode == 'live' and self.exchange:
                    try:
                        self.logger.info(f"🚀 LIVE ENTRY: Placing order for {symbol}...")
                        
                        # Use the working method from the bloated main.py
                        quantity = (size * leverage) / entry_price
                        try:
                            quantity = float(self.exchange.exchange.amount_to_precision(symbol, quantity))
                        except: pass

                        # THE WORKING CALL FROM APRIL 24TH
                        order = self.exchange.exchange.create_order(
                            symbol=symbol,
                            type='MARKET',
                            side=approved_params['side'].lower(),
                            amount=quantity
                        )
                        
                        if order and order.get('id'):
                            self.logger.info(f"✅ LIVE ENTRY SUCCESS: {order.get('id')} ({quantity} {symbol}) at {entry_price}")
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
                        else:
                            self.logger.error(f"❌ ORDER REJECTED: Binance returned success but no Order ID.")
                            return

                    except Exception as e:
                        self.logger.error(f"🚨 BINANCE ORDER ERROR for {symbol}: {e}")
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
                    self.execute_trade(signal, strategy.name, symbol)
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
