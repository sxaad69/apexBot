import ccxt
from typing import Dict, Any, Optional

class TrailingStopLayer:
    """
    Bot-Side Trailing Stop System with Crash Recovery.
    
    Instead of relying on rigid exchange OCO targets, this engine:
    1. Tracks the "Highest Watermark" price of an active trade.
    2. Calculates a dynamic trailing floor based on ATR or % distance.
    3. Issues cancellation/replacement API calls to lock in profit manually.
    """
    
    def __init__(self, config, logger, sqlite_manager, exchange_client, mode='paper'):
        self.config = config
        self.logger = logger
        self.db = sqlite_manager
        self.exchange = exchange_client
        self.mode = mode
        
        # Configuration - Activation distance and trailing distance
        # e.g. Start trailing once we are 1% in profit, and trail by 0.5%
        self.ACTIVATION_PROFIT_PCT = getattr(config, 'TRAILING_TP_ACTIVATION', 3.0)
        self.TRAIL_DISTANCE_PCT = getattr(config, 'TRAILING_TP_DISTANCE', 1.5)

    def process_open_trades(self, live_tickers: Dict[str, float]):
        """
        Loops through all OPEN trades in SQLite.
        If a trade reaches a new peak profit, it moves the Stop Loss up on the exchange.
        """
        try:
            conn = self.db._get_connection(self.db.main_db)
            cursor = conn.cursor()
            
            # Fetch all currently open trades from SQLite memory
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            open_trades = cursor.fetchall()
            
            for trade in open_trades:
                symbol = trade['symbol']
                if symbol not in live_tickers:
                    continue
                    
                current_price = live_tickers[symbol]
                
                # Update Highest Watermark
                highest_price = trade['highest_price'] or trade['entry_price']
                is_new_high = False
                
                if trade['side'] == 'buy' and current_price > highest_price:
                    highest_price = current_price
                    is_new_high = True
                elif trade['side'] == 'sell' and current_price < highest_price:
                    # For shorts, the "highest" is actually the lowest price (max profit)
                    highest_price = current_price
                    is_new_high = True
                
                # 1. Trailing Stop Ratchet Logic
                if is_new_high:
                    self._evaluate_and_update_stop(trade, highest_price, current_price, cursor)
                    
                # 2. Trigger Trailing Stop Exit Logic
                current_stop = trade['trailing_stop_price'] or trade['stop_loss']
                if current_stop:
                    if trade['side'] == 'buy' and current_price <= current_stop:
                        self.logger.system(f"🚨 Trailing Stop HIT for {symbol} (Long). Executing Market Close.")
                        self._trigger_close_position(trade, current_price, cursor)
                    elif trade['side'] == 'sell' and current_price >= current_stop:
                        self.logger.system(f"🚨 Trailing Stop HIT for {symbol} (Short). Executing Market Close.")
                        self._trigger_close_position(trade, current_price, cursor)
                    
            conn.commit()
            conn.close()
            
        except Exception as e:
            self.logger.error(f"Error processing trailing stops: {e}")

    def _trigger_close_position(self, trade, current_price, cursor):
        """Fires a market close via CCXT and marks the SQLite row closed."""
        symbol = trade['symbol']
        trade_id = trade['trade_id']
        try:
            # 1. Fire Exchange API Call
            if self.exchange:
                self.exchange.close_position(symbol)
                
            # 2. Update SQLite State
            exit_time = __import__('datetime').datetime.utcnow().isoformat()
            cursor.execute("UPDATE trades SET status = 'CLOSED', exit_price = ?, exit_time = ?, reason = 'trailing_stop' WHERE trade_id = ?",
                           (current_price, exit_time, trade_id))
            
        except Exception as e:
            self.logger.error(f"🚨 Failed to close position {symbol} during Trailing Stop hit! {e}")

    def _evaluate_and_update_stop(self, trade, highest_price, current_price, cursor):
        """Calculates trailing distance and pushes API update if needed."""
        entry_price = trade['entry_price']
        side = trade['side']
        symbol = trade['symbol']
        trade_id = trade['trade_id']
        current_stop = trade['trailing_stop_price'] or trade['stop_loss']
        
        # Calculate max profit reached so far
        if side == 'buy':
            profit_pct = ((highest_price - entry_price) / entry_price) * 100
        else:
            profit_pct = ((entry_price - highest_price) / entry_price) * 100
            
        # Has it reached Activation threshold?
        if profit_pct < self.ACTIVATION_PROFIT_PCT:
            # We must still simply update SQLite's watermark so we don't lose progress if AWS crashes
            cursor.execute("UPDATE trades SET highest_price = ? WHERE trade_id = ?", (highest_price, trade_id))
            return
            
        # Calculate new trailing stop floor
        if side == 'buy':
            new_stop = highest_price * (1 - (self.TRAIL_DISTANCE_PCT / 100.0))
            # Ratchet only (never move stop down)
            if current_stop is None or new_stop > current_stop:
                self._move_stop_loss_on_exchange(trade, new_stop)
                cursor.execute("UPDATE trades SET highest_price = ?, trailing_stop_price = ?, stop_loss = ? WHERE trade_id = ?", 
                               (highest_price, new_stop, new_stop, trade_id))
                self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.4f} for {symbol} (Profit: {profit_pct:.2f}%)")
        else:
            new_stop = highest_price * (1 + (self.TRAIL_DISTANCE_PCT / 100.0))
            # Ratchet only (never move stop up for Shorts)
            if current_stop is None or new_stop < current_stop:
                self._move_stop_loss_on_exchange(trade, new_stop)
                cursor.execute("UPDATE trades SET highest_price = ?, trailing_stop_price = ?, stop_loss = ? WHERE trade_id = ?", 
                               (highest_price, new_stop, new_stop, trade_id))
                self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.4f} for {symbol} (Profit: {profit_pct:.2f}%)")

    def _move_stop_loss_on_exchange(self, trade, new_stop_price):
        """Actually sends API requests to Binance to cancel the old stop and place a new one."""
        symbol = trade['symbol']
        side = trade['side']
        trade_id = trade['trade_id']
        
        try:
            if self.mode == 'paper':
                # Virtual physical sync: Provide a mock exchange order ID
                import uuid
                mock_order_id = f"mock_stop_{uuid.uuid4().hex[:8]}"
                self.logger.debug(f"[Paper] Sent Trailing Stop to Exchange: {symbol} at {new_stop_price:.4f} (ID: {mock_order_id})")
                
                # In paper trading, we just rely on bot-side triggers since no real exchange holds our orders.
                # However we simulate receiving a valid exchange ID.
                return mock_order_id
            else:
                # Live Trading Sync
                # 1. We must parse metadata to get the active stop loss order ID
                if not self.exchange:
                    return None
                    
                import json
                meta = trade.get('metadata')
                meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})
                exchange_sl_id = meta_dict.get('exchange_sl_id')
                
                # 2. Cancel Old Stop Loss order on exchange if one exists
                if exchange_sl_id:
                    try:
                        self.exchange.cancel_order(exchange_sl_id, symbol)
                    except Exception as e:
                        self.logger.warning(f"Failed to cancel old stop loss {exchange_sl_id} for {symbol}: {e}")
                
                # 3. Create New Stop Market Order
                stop_side = 'sell' if side == 'buy' else 'buy'
                # Position sizing might need to be drawn from exchange active position later, using 'close' amount logic
                # For now using stop market standard implementation via CCXT params
                params = {'stopPrice': new_stop_price, 'reduceOnly': True}
                
                # We do not pass an exact amount as reduceOnly usually handles closing the entire active size
                new_order = self.exchange.place_order(symbol, stop_side, 'market', amount=0, price=None, **params)
                
                if new_order and 'id' in new_order:
                    self.logger.debug(f"[Live] Updated Exchange Stop Loss for {symbol} to {new_stop_price:.4f} (ID: {new_order['id']})")
                    return new_order['id']
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to move physical exchange stop for {symbol}: {e}", exc_info=True)
            return None
