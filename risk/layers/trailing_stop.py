import ccxt
import json
from typing import Dict, Any, Optional

class TrailingStopLayer:
    """
    Bot-Side Trailing Stop System with Crash Recovery.
    
    Instead of relying on rigid exchange OCO targets, this engine:
    1. Tracks the "Highest Watermark" price of an active trade.
    2. Calculates a dynamic trailing floor based on ATR or % distance.
    3. Issues cancellation/replacement API calls to lock in profit manually.
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
        # e.g. Start trailing once we are 1% in profit, and trail by 0.5%
        self.ACTIVATION_PROFIT_PCT = getattr(config, 'TRAILING_TP_ACTIVATION', 3.0)
        self.TRAIL_DISTANCE_PCT = getattr(config, 'TRAILING_TP_DISTANCE', 1.5)

    def process_open_trades(self, live_tickers: Dict[str, float]):
        """
        Loops through all OPEN trades in SQLite.
        If a trade reaches a new peak profit, it moves the Stop Loss up on the exchange.
        """
        try:
            import sqlite3
            conn = self.db._get_connection(self.db.main_db)
            # Durable Core: Ensure we can index columns by name
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # Fetch all currently open trades from SQLite
            cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
            open_trades = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            for trade_dict in open_trades:
                # --- FIX: Isolate each trade in its own try/except so one bad record
                # --- never prevents SL checks from running on subsequent trades.
                try:
                    symbol = trade_dict['symbol']
                    # self.logger.debug(f"[TrailingStopLayer] Evaluating {symbol}...")
                    if symbol not in live_tickers:
                        # try fuzzy match for colon suffix
                        fuzzy_symbol = symbol.split(':')[0] if ':' in symbol else symbol
                        if fuzzy_symbol in live_tickers:
                            current_price = live_tickers[fuzzy_symbol]
                        else:
                            # self.logger.debug(f"[TrailingStopLayer] {symbol} not in live_tickers")
                            continue
                    else:
                        current_price = live_tickers[symbol]
                    
                    # --- FIX: Guard against malformed records with entry_price = 0
                    entry_price = trade_dict.get('entry_price')
                    if not entry_price or entry_price == 0:
                        self.logger.warning(f"⚠️ Skipping {symbol} (trade {trade_dict.get('trade_id')[:8]}) — entry_price is 0/None. Possible zombie record.")
                        continue
                        
                    # Update Watermarks
                    highest_price = trade_dict.get('highest_price') or entry_price
                    lowest_price = trade_dict.get('lowest_price') or entry_price
                    is_new_peak = False
                    
                    if trade_dict['side'] == 'buy' and current_price > highest_price:
                        highest_price = current_price
                        is_new_peak = True
                    elif trade_dict['side'] == 'sell' and current_price < lowest_price:
                        lowest_price = current_price
                        is_new_peak = True
                    
                    # 1. Trailing Stop Ratchet Logic (Phase 61: Always evaluate for activation if not active)
                    if is_new_peak or not trade_dict.get('trailing_stop_price'):
                        self._evaluate_and_update_stop(trade_dict, highest_price, lowest_price, current_price)
                        
                    # 2. Trigger Trailing Stop Exit Logic
                    current_stop = trade_dict.get('trailing_stop_price') or trade_dict.get('stop_loss')
                    if current_stop:
                        if trade_dict['side'] == 'buy' and current_price <= current_stop:
                            self.logger.system(f"🚨 Trailing Stop HIT for {symbol} (Long). Executing Market Close.")
                            self._trigger_close_position(trade_dict, current_price)
                        elif trade_dict['side'] == 'sell' and current_price >= current_stop:
                            self.logger.system(f"🚨 Trailing Stop HIT for {symbol} (Short). Executing Market Close.")
                            self._trigger_close_position(trade_dict, current_price)

                except Exception as trade_err:
                    symbol_name = trade_dict.get('symbol', 'Unknown')
                    self.logger.error(f"🚨 Error evaluating SL for {symbol_name}: {trade_err}")
            
        except Exception as e:
            self.logger.error(f"Error processing trailing stops: {e}")

    def _trigger_close_position(self, trade, current_price):
        """Fires a market close via CCXT and marks the SQLite row closed."""
        symbol = trade['symbol']
        trade_id = trade['trade_id']
        try:
            # 0. Cancel orphan TP order on exchange first (before market close) to prevent re-open
            if self.exchange and self.mode == 'live' and getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                tp_id = trade.get('tp_order_id')
                if tp_id:
                    try:
                        self.exchange.exchange.cancel_order(tp_id, symbol)
                        self.logger.info(f"🛡️ Cancelled orphan TP order {tp_id} for {symbol} (trailing SL hit)")
                    except Exception as tp_cancel_e:
                        self.logger.warning(f"⚠️ Could not cancel orphan TP for {symbol}: {tp_cancel_e}")

            # 1. Fire Exchange API Call (Only in LIVE mode)
            close_order = None
            if self.exchange and self.mode == 'live':
                close_order = self.exchange.close_position(symbol)
            else:
                self.logger.debug(f"[Paper] Simulated Close for {symbol}")
                
            # 2. UNIFIED EXIT FINALIZATION via TradeManager (with Zero-Position Verification)
            if self.trade_manager:
                exit_result = self.trade_manager.record_exit(
                    symbol=symbol,
                    trade_id=trade_id,
                    reason='trailing_stop',
                    current_price=current_price,
                    order_response=close_order
                )
                
                # 3. If verified closed, update memory engine capital
                if exit_result and exit_result.get('verified') and self.engine:
                    position_key = f"{trade['strategy']}:{symbol}"
                    if position_key in self.engine.positions:
                        self.engine.total_capital += exit_result.get('capital_return', 0)
                        del self.engine.positions[position_key]
                        self.logger.info(f"💰 CAPITAL RECOVERED: ${exit_result['capital_return']:.2f}")
            else:
                # Fallback for paper mode (In final versions, this should always go through self.trade_manager.record_exit)
                pass
            
        except Exception as e:
            self.logger.error(f"🚨 Failed to close position {symbol} during Trailing Stop hit! {e}")

    def _evaluate_and_update_stop(self, trade, highest_price, lowest_price, current_price):
        """Calculates trailing distance and pushes API update if needed."""
        entry_price = trade['entry_price']
        side = trade['side']
        symbol = trade['symbol']
        trade_id = trade['trade_id']
        current_stop = trade['trailing_stop_price'] or trade['stop_loss']
        
        # Guard: should never reach here with entry_price=0, but belt-and-suspenders
        if not entry_price or entry_price == 0:
            return
        
        # Calculate max profit reached so far
        if side == 'buy':
            profit_pct = ((highest_price - entry_price) / entry_price) * 100
            peak_price = highest_price
        else:
            profit_pct = ((entry_price - lowest_price) / entry_price) * 100
            peak_price = lowest_price
            
        # 1. Determine Dynamic Activation & Distance (Phase 53: Leverage-Aware)
        leverage = trade.get('leverage', 1)
        target_roe = getattr(self.config, 'TRAILING_TARGET_ROE', 6.0)
        capture_roe = getattr(self.config, 'TRAILING_CAPTURE_ROE', 2.5)
        
        # Scale to Price Move %: Threshold = ROE / Leverage
        activation_pct = target_roe / max(1.0, float(leverage or 1))
        trail_dist_pct = capture_roe / max(1.0, float(leverage or 1))
        
        # Fallback to static config if extremely low leverage or missing
        activation_pct = min(activation_pct, self.ACTIVATION_PROFIT_PCT)
        trail_dist_pct = min(trail_dist_pct, self.TRAIL_DISTANCE_PCT)

        import json
        from datetime import datetime
        meta = trade.get('metadata', '{}')
        meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})

        # Has it reached Activation threshold?
        if profit_pct < activation_pct:
            # We must still simply update SQLite's watermark so we don't lose progress
            if 'trailing_activation_time' in meta_dict:
                del meta_dict['trailing_activation_time']
            
            self.db.update_trade_metadata(trade_id, {
                'highest_price': highest_price,
                'lowest_price': lowest_price,
                'metadata': meta_dict
            })
            return
            
        # --- DIRTY TICK FILTER (3-Second Sustained Profit Check) ---
        current_time = datetime.utcnow().timestamp()
        if 'trailing_activation_time' not in meta_dict:
            meta_dict['trailing_activation_time'] = current_time
            self.db.update_trade_metadata(trade_id, {
                'highest_price': highest_price,
                'lowest_price': lowest_price,
                'metadata': meta_dict
            })
            return
            
        if current_time - meta_dict['trailing_activation_time'] < 3.0:
            # Still within the 3-second verification window. Ignore for now.
            return
            
        # 2. Calculate new trailing stop floor (with Fee-Safety Floor)
        # Round-trip Taker fee is ~0.1% notional. Stop must be 0.12% above entry to be truly "profitable"
        fee_floor_pct = 0.12 
        
        if side == 'buy':
            new_stop = peak_price * (1 - (trail_dist_pct / 100.0))
            # Ensure new stop never falls below a "Safe Breakeven" after fees
            safe_breakeven = entry_price * (1 + (fee_floor_pct / 100.0))
            new_stop = max(new_stop, safe_breakeven)
            
            # Ratchet only (never move stop down)
            if current_stop is None or new_stop > current_stop:
                new_sl_id = self._move_stop_loss_on_exchange(trade, new_stop)
                
                import json
                from datetime import datetime
                meta = trade['metadata']
                meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})
                if new_sl_id and new_sl_id != "error":
                    meta_dict['exchange_sl_id'] = new_sl_id
                
                meta_dict['trailing_sl_history'] = meta_dict.get('trailing_sl_history', []) + [{
                    'price': new_stop,
                    'time': datetime.utcnow().isoformat()
                }]
                    
                self.db.update_trade_metadata(trade_id, {
                    'highest_price': highest_price,
                    'trailing_stop_price': new_stop,
                    'stop_loss': new_stop,
                    'metadata': meta_dict
                })
                self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.4f} for {symbol} (Profit: {profit_pct:.2f}%)")
        else:
            new_stop = peak_price * (1 + (trail_dist_pct / 100.0))
            # For a SHORT in profit (price well below entry), the trailing stop should:
            # 1. Chase price DOWN (stop moves down as price falls)
            # 2. Never go ABOVE entry (that would mean we're giving back ALL profits)
            # The safe floor prevents the stop from drifting above entry into loss zone
            # entry_price * (1 + fee_floor) = the price above entry where we start losing (including fees)
            safe_entry_floor = entry_price * (1 + (fee_floor_pct / 100.0))
            # Cap: stop must never go ABOVE entry+fees (the hard loss zone)
            # new_stop for SHORT is above current price but below entry — min caps only if calculation drifts above entry
            new_stop = min(new_stop, entry_price)  # Hard cap: never exit above entry (that's a guaranteed loss)
            
            # Ratchet only (never move stop DOWN for Shorts after ratcheting up)
            # For shorts: "up" in stop price = less profit preserved; "down" = more profit locked
            if current_stop is None or new_stop < current_stop:
                new_sl_id = self._move_stop_loss_on_exchange(trade, new_stop)
                
                import json
                from datetime import datetime
                meta = trade['metadata']
                meta_dict = json.loads(meta) if isinstance(meta, str) else (meta or {})
                
                meta_dict['trailing_sl_history'] = meta_dict.get('trailing_sl_history', []) + [{
                    'price': new_stop,
                    'time': datetime.utcnow().isoformat()
                }]
                    
                self.db.update_trade_metadata(trade_id, {
                    'lowest_price': lowest_price,
                    'trailing_stop_price': new_stop,
                    'stop_loss': new_stop,
                    'metadata': meta_dict
                })
                self.logger.system(f"Trailing Stop RATCHETED to {new_stop:.6f} for {symbol} (Profit: {profit_pct:.2f}%, Entry: {entry_price:.6f})")


    def _move_stop_loss_on_exchange(self, trade, new_stop_price):
        """Actually sends API requests to Binance to cancel the old stop and place a new one."""
        symbol = trade['symbol']
        side = trade['side']
        trade_id = trade['trade_id']
        
        try:
            if self.mode == 'paper':
                # Virtual physical sync: Provide a mock exchange order ID
                import uuid
                mock_order_id = f"mock_stop_{str(uuid.uuid4().hex)[:8]}"
                self.logger.debug(f"[Paper] Sent Trailing Stop to Exchange: {symbol} at {new_stop_price:.4f} (ID: {mock_order_id})")
                
                # In paper trading, we just rely on bot-side triggers since no real exchange holds our orders.
                # However we simulate receiving a valid exchange ID.
                return mock_order_id
            else:
                # Live Trading Sync
                # 1. Verify feature is enabled
                if not getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                    return None

                if not self.exchange:
                    return None
                    
                import json
                trade_dict = dict(trade)
                meta_dict = json.loads(trade_dict.get('metadata', "{}")) if isinstance(trade_dict.get('metadata'), str) else (trade_dict.get('metadata') or {})
                
                # Retrieve current SL order ID from database column
                exchange_sl_id = trade_dict.get('sl_order_id')
                
                # 2. Add Cancel-then-Verify Logic for Old Order
                if exchange_sl_id:
                    try:
                        self.exchange.exchange.cancel_order(exchange_sl_id, symbol)
                    except Exception as e:
                        # Cancel failed. Check if Binance already filled the SL order
                        try:
                            old_order = self.exchange.exchange.fetch_order(exchange_sl_id, symbol)
                            if old_order.get('status') == 'closed':
                                self.logger.info(f"🛡️ [RACE DETECTED] SL already filled for {symbol} during ratchet. Recording exit.")
                                
                                # Clean up orphan TP
                                tp_id = trade_dict.get('tp_order_id')
                                if tp_id:
                                    try: self.exchange.exchange.cancel_order(tp_id, symbol)
                                    except: pass
                                    
                                # Using dynamic import since risk layer doesn't naturally have TradeManager
                                # Alternative: pass the TradeManager down or assume main wrapper handles the exit eventually
                                # Since we return 'error', the loop skips update and main.py polling will catch this anyway.
                                return "error"
                        except: pass
                        self.logger.warning(f"Failed to cancel old stop loss {exchange_sl_id} for {symbol}: {e}")
                
                # 3. Create New Stop Market Order
                stop_side = 'sell' if side == 'buy' else 'buy'
                
                # Fetch original size
                size_qty = meta_dict.get('executed_qty', None)
                if not size_qty:
                    # Fallback approximation just in case
                    if trade_dict.get('entry_price', 0) > 0:
                        size_qty = trade_dict['size'] / trade_dict['entry_price'] * trade_dict.get('leverage', 1)
                    else:
                        size_qty = 0
                    
                # Format precision cleanly to prevent InvalidOrder limits from Exchange checks natively
                try:
                    new_stop_price = float(self.exchange.exchange.price_to_precision(symbol, new_stop_price))
                    # Fallback formatting size if purely an approximation
                    size_qty = float(self.exchange.exchange.amount_to_precision(symbol, size_qty))
                except Exception:
                    pass
                
                params = {'reduceOnly': True}
                
                new_order = self.exchange.exchange.create_stop_market_order(
                    symbol=symbol,
                    side=stop_side,
                    amount=size_qty,
                    stopPrice=new_stop_price,
                    params=params
                )
                
                if new_order and 'id' in new_order:
                    new_id = new_order['id']
                    self.logger.debug(f"[Live] Updated Exchange Stop Loss for {symbol} to {new_stop_price} (ID: {new_id})")
                    # Save the new ID to DB column
                    self.db.update_trade_order_ids(trade_id, sl_order_id=new_id)
                    
                    # Update strictly in-memory state of the trade wrapper dictionary reference 
                    trade['sl_order_id'] = new_id
                    return new_id
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to move physical exchange stop for {symbol}: {e}", exc_info=True)
            return "error"
