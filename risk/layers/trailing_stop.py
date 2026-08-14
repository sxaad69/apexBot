import json
from typing import Dict, Any, Optional
from datetime import datetime

class TrailingStopLayer:
    """
    Bot-Side Trailing Stop Calculator.
    
    This layer acts strictly as a calculator. Every 2 seconds, the Sentinel thread passes 
    active positions and current prices here. This engine calculates if a new profit peak 
    was reached and updates the stop_loss value in memory and the database.
    
    It does NOT execute market closes or poll the database directly. Execution is centralized 
    in the Sentinel thread.
    """
    
    def __init__(self, config, logger, sqlite_manager, exchange_client, engine=None, trade_manager=None):
        self.config = config
        self.logger = logger
        self.db = sqlite_manager
        self.exchange = exchange_client
        self.engine = engine
        self.trade_manager = trade_manager
        self.mode = getattr(config, 'TRADING_MODE', 'paper').lower()
        
        # Configuration - Activation distance and trailing distance
        self.ACTIVATION_PROFIT_PCT = getattr(config, 'TRAILING_STOP_ACTIVATION', 5.0)
        self.TRAIL_DISTANCE_PCT = getattr(config, 'TRAILING_STOP_DISTANCE', 3.0)

    def update_position_ratchet(self, position: Dict[str, Any], current_price: float):
        """
        Calculates trailing distance and pushes database/memory update if needed.
        Called directly by the PriorityExitSentinel thread in main.py.
        """
        try:
            # Exchange-managed positions: the exchange trails natively, this
            # bot-side ratchet must not cancel/replace exchange orders.
            if position.get('exchange_trailing'):
                return

            entry_price = position.get('entry_price')
            if not entry_price or entry_price == 0:
                return

            symbol = position['symbol']
            side = position['side']
            trade_id = position['trade_id']
            
            # Use trailing_stop_price if available, fallback to initial stop_loss
            current_stop = position.get('trailing_stop_price') or position.get('stop_loss')
            
            # Update Watermarks
            highest_price = position.get('highest_price') or entry_price
            lowest_price = position.get('lowest_price') or entry_price
            is_new_peak = False
            
            if side == 'buy' and current_price > highest_price:
                highest_price = current_price
                is_new_peak = True
            elif side == 'sell' and current_price < lowest_price:
                lowest_price = current_price
                is_new_peak = True

            # Calculate max profit reached so far
            if side == 'buy':
                profit_pct = ((highest_price - entry_price) / entry_price) * 100
                peak_price = highest_price
            else:
                profit_pct = ((entry_price - lowest_price) / entry_price) * 100
                peak_price = lowest_price

            # Activation and Distance from config
            activation_pct = self.ACTIVATION_PROFIT_PCT
            trail_dist_pct = self.TRAIL_DISTANCE_PCT

            meta = position.get('metadata', '{}')
            meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})

            # 1. Has it reached Activation threshold?
            if profit_pct < activation_pct:
                # Update watermarks in memory and DB so we track highest point accurately
                if is_new_peak:
                    if 'trailing_activation_time' in meta_dict:
                        del meta_dict['trailing_activation_time']
                    
                    self.db.update_trade_metadata(trade_id, {
                        'highest_price': highest_price,
                        'lowest_price': lowest_price,
                        'metadata': meta_dict
                    })
                    position['highest_price'] = highest_price
                    position['lowest_price'] = lowest_price
                    position['metadata'] = json.dumps(meta_dict)
                return

            # 2. Calculate new trailing stop floor (with Fee-Safety Floor)
            fee_floor_pct = 0.12 
            
            if side == 'buy':
                new_stop = peak_price * (1 - (trail_dist_pct / 100.0))
                # Ensure new stop never falls below a "Safe Breakeven" after fees
                safe_breakeven = entry_price * (1 + (fee_floor_pct / 100.0))
                new_stop = max(new_stop, safe_breakeven)
                
                # Ratchet only (never move stop down)
                if current_stop is None or new_stop > current_stop:
                    new_sl_id = self._move_stop_loss_on_exchange(position, new_stop)
                    
                    if new_sl_id and new_sl_id != "error":
                        meta_dict['exchange_sl_id'] = new_sl_id
                        position['sl_order_id'] = new_sl_id
                    
                    meta_dict['trailing_sl_history'] = meta_dict.get('trailing_sl_history', []) + [{
                        'price': new_stop,
                        'time': datetime.utcnow().isoformat()
                    }]
                        
                    # Update Database
                    self.db.update_trade_metadata(trade_id, {
                        'highest_price': highest_price,
                        'trailing_stop_price': new_stop,
                        'stop_loss': new_stop,
                        'trailing_stop_active': True,
                        'metadata': meta_dict
                    })
                    
                    # IMMEDIATELY Update Memory Dictionary (Passed by reference)
                    position['highest_price'] = highest_price
                    position['trailing_stop_price'] = new_stop
                    position['stop_loss'] = new_stop
                    position['trailing_stop_active'] = True
                    position['metadata'] = json.dumps(meta_dict)
                    
                    self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.4f} for {symbol} (Profit: {profit_pct:.2f}%)")
                    
            else:
                new_stop = peak_price * (1 + (trail_dist_pct / 100.0))
                safe_entry_floor = entry_price * (1 + (fee_floor_pct / 100.0))
                # Cap: stop must never go ABOVE entry+fees (the hard loss zone)
                new_stop = min(new_stop, entry_price)  
                
                # Ratchet only (never move stop DOWN for Shorts after ratcheting up)
                if current_stop is None or new_stop < current_stop:
                    new_sl_id = self._move_stop_loss_on_exchange(position, new_stop)
                    
                    if new_sl_id and new_sl_id != "error":
                        meta_dict['exchange_sl_id'] = new_sl_id
                        position['sl_order_id'] = new_sl_id
                    
                    meta_dict['trailing_sl_history'] = meta_dict.get('trailing_sl_history', []) + [{
                        'price': new_stop,
                        'time': datetime.utcnow().isoformat()
                    }]
                        
                    # Update Database
                    self.db.update_trade_metadata(trade_id, {
                        'lowest_price': lowest_price,
                        'trailing_stop_price': new_stop,
                        'stop_loss': new_stop,
                        'trailing_stop_active': True,
                        'metadata': meta_dict
                    })
                    
                    # IMMEDIATELY Update Memory Dictionary
                    position['lowest_price'] = lowest_price
                    position['trailing_stop_price'] = new_stop
                    position['stop_loss'] = new_stop
                    position['trailing_stop_active'] = True
                    position['metadata'] = json.dumps(meta_dict)
                    
                    self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.6f} for {symbol} (Profit: {profit_pct:.2f}%, Entry: {entry_price:.6f})")

        except Exception as e:
            self.logger.error(f"Error calculating trailing stop for {position.get('symbol', 'Unknown')}: {e}")

    def _move_stop_loss_on_exchange(self, trade, new_stop_price):
        """Actually sends API requests to Binance/Exchange to cancel the old stop and place a new one."""
        symbol = trade['symbol']
        side = trade['side']
        trade_id = trade['trade_id']
        
        try:
            if self.mode == 'paper':
                import uuid
                mock_order_id = f"mock_stop_{str(uuid.uuid4().hex)[:8]}"
                self.logger.debug(f"[Paper] Sent Trailing Stop to Exchange: {symbol} at {new_stop_price:.4f} (ID: {mock_order_id})")
                return mock_order_id
            else:
                if not getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                    return None

                if not self.exchange:
                    return None
                    
                meta_dict = json.loads(trade.get('metadata', "{}")) if isinstance(trade.get('metadata'), str) else (trade.get('metadata') or {})
                exchange_sl_id = trade.get('sl_order_id')
                
                # Cancel old SL
                if exchange_sl_id:
                    try:
                        self.exchange.exchange.cancel_order(exchange_sl_id, symbol)
                    except Exception as e:
                        try:
                            old_order = self.exchange.exchange.fetch_order(exchange_sl_id, symbol)
                            if old_order.get('status') == 'closed':
                                self.logger.info(f"🛡️ [RACE DETECTED] SL already filled for {symbol} during ratchet.")
                                tp_id = trade.get('tp_order_id')
                                if tp_id:
                                    try: self.exchange.exchange.cancel_order(tp_id, symbol)
                                    except: pass
                                return "error"
                        except: pass
                        self.logger.warning(f"Failed to cancel old stop loss {exchange_sl_id} for {symbol}: {e}")
                
                # Create New Stop Market Order
                stop_side = 'sell' if side == 'buy' else 'buy'
                size_qty = meta_dict.get('executed_qty', None)
                if not size_qty:
                    if trade.get('entry_price', 0) > 0:
                        size_qty = trade['size'] / trade['entry_price'] * trade.get('leverage', 1)
                    else:
                        size_qty = 0
                    
                try:
                    new_stop_price = float(self.exchange.exchange.price_to_precision(symbol, new_stop_price))
                    size_qty = float(self.exchange.exchange.amount_to_precision(symbol, size_qty))
                except Exception:
                    pass
                
                params = {'reduceOnly': True}
                
                new_order = self.exchange.exchange.create_stop_market_order(
                    symbol=symbol,
                    side=stop_side,
                    amount=size_qty,
                    triggerPrice=new_stop_price,
                    params=params
                )
                
                if new_order and 'id' in new_order:
                    new_id = new_order['id']
                    self.logger.debug(f"[Live] Updated Exchange Stop Loss for {symbol} to {new_stop_price} (ID: {new_id})")
                    self.db.update_trade_order_ids(trade_id, sl_order_id=new_id)
                    return new_id
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to move physical exchange stop for {symbol}: {e}")
            return "error"
