import json
import logging
import uuid
import time
from datetime import datetime
from typing import Dict, Any, Optional

class TradeManager:
    """
    Centralized manager for trade execution and synchronization.
    Ensures that all entries and exits (bot, sync-script, or exchange) 
    are recorded with grounded exchange data.
    """
    def __init__(self, config, db, exchange, logger):
        self.config = config
        self.db = db
        self.exchange = exchange
        self.logger = logger
        self.active_positions = {} 

    def record_entry(self, symbol: str, strategy_name: str, side: str, size: float, 
                     leverage: int, stop_loss: float, take_profit: float, 
                     order_response: Optional[Dict] = None, planned_price: float = 0,
                     skip_verification: bool = False):
        """
        Grounded Entry Recorder with Post-Entry Verification.
        Only marks trade as OPEN if exchange confirms position is > 0.
        """
        # 1. MANDATORY VERIFICATION: Check exchange (unless paper or skip)
        is_live = getattr(self.config, 'TRADING_MODE', 'paper') == 'live'
        if is_live and not skip_verification:
            try:
                time.sleep(1) # Wait for fill
                positions = self.exchange.get_positions()
                pos = next((p for p in positions if p['symbol'] == symbol), None)
                actual_size = abs(float(pos.get('contracts', 0) if pos else 0))
                
                if actual_size == 0:
                    self.logger.error(f"🚨 [ENTRY VERIFICATION FAILED] {symbol} has 0 contracts on exchange! NOT recording trade in DB.")
                    return {'verified': False, 'size': 0}
                self.logger.info(f"✅ [ENTRY VERIFIED] {symbol} confirmed OPEN on exchange with {actual_size} contracts.")
            except Exception as e:
                self.logger.error(f"🚨 [ENTRY VERIFICATION ERROR] {symbol}: {e}. Aborting DB record.")
                return {'verified': False, 'error': str(e)}

        # 2. Capture Actual Entry Price (Grounding)
        entry_price = planned_price
        if order_response and order_response.get('average'):
            entry_price = float(order_response['average'])
            self.logger.info(f"📊 [GROUNDED] Actual entry for {symbol}: {entry_price}")
        
        entry_time = datetime.now()
        trade_id = f"FUT-{str(uuid.uuid4().hex)[:8].upper()}"
        
        # Calculate fees (Approximate if not in response)
        fee_pct = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
        entry_fee = size * fee_pct

        # 2. Prepare Position Object
        position = {
            'trade_id': trade_id,
            'entry_time': entry_time.isoformat(),
            'entry_price': entry_price,
            'side': side,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'size': size,
            'entry_fee': entry_fee,
            'leverage': leverage,
            'strategy': strategy_name,
            'symbol': symbol,
            'status': 'OPEN',
            'trailing_stop_active': False,
            'highest_price': entry_price,
            'lowest_price': entry_price,
            'metadata': json.dumps(order_response) if order_response else "{}",
            'exchange_order_id': order_response.get('id') if order_response else None
        }

        # 3. Persist to SQLite
        try:
            self.db.record_trade(position)
            self.logger.trade_entry(
                symbol=symbol, side=side, size=size, price=entry_price,
                leverage=leverage, strategy=strategy_name, trade_id=trade_id,
                stop_loss=stop_loss, take_profit=take_profit, metadata=order_response
            )
        except Exception as e:
            self.logger.error(f"Failed to persist trade to DB: {e}")
            
        return position

    def update_trade_params(self, trade_id: str, updates: Dict[str, Any]) -> bool:
        """
        Pushes live parameter updates (like Trailing TP or SL) to the database.
        """
        try:
            success = self.db.update_trade_metadata(trade_id, updates)
            if success:
                self.logger.info(f"🔄 Database updated for {trade_id}: {list(updates.keys())}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to update trade params in DB: {e}")
            return False

    def record_exit(self, symbol: str, trade_id: str, reason: str, 
                    current_price: float, order_response: Optional[Dict] = None,
                    skip_verification: bool = False):
        """
        Grounded Exit Recorder with Zero-Position Verification.
        Only marks trade as CLOSED if exchange confirms position is 0.
        """
        # 1. Fetch trade from DB to get entry data
        trades = self.db.get_trades(status='OPEN')
        trade = next((t for t in trades if t['trade_id'] == trade_id), None)
        
        if not trade:
            # Check if it was already closed (safety for double-calls)
            recent = self.db.get_trades(status='CLOSED')
            if any(t['trade_id'] == trade_id for t in recent):
                self.logger.info(f"ℹ️ Trade {trade_id} already marked as CLOSED. Skipping.")
                return {'verified': True}
            self.logger.warning(f"Attempted to close unknown trade_id: {trade_id}")
            return None

        # 2. MANDATORY VERIFICATION: Check exchange size (unless paper or skip)
        is_live = getattr(self.config, 'TRADING_MODE', 'paper') == 'live'
        if is_live and not skip_verification:
            try:
                # Small delay to allow exchange to process
                time.sleep(1)
                positions = self.exchange.get_positions()
                pos = next((p for p in positions if p['symbol'] == symbol), None)
                size = abs(float(pos.get('contracts', 0) if pos else 0))
                
                if size > 0:
                    self.logger.error(f"🚨 [VERIFICATION FAILED] {symbol} still has {size} contracts open! NOT marking as CLOSED in DB.")
                    return {'verified': False, 'size': size}
                self.logger.info(f"✅ [VERIFIED] {symbol} position is confirmed ZERO on exchange.")
            except Exception as e:
                self.logger.error(f"🚨 [VERIFICATION ERROR] Could not verify {symbol} status: {e}. Keeping trade OPEN.")
                return {'verified': False, 'error': str(e)}

        # 3. Capture Actual Exit Price (Grounding)
        exit_price = current_price
        if order_response and order_response.get('average'):
            exit_price = float(order_response['average'])
            self.logger.info(f"📊 [GROUNDED] Actual exit for {symbol}: {exit_price}")
        elif (not exit_price or exit_price == 0) and is_live:
             try:
                 history = self.exchange.exchange.fetch_my_trades(symbol, limit=1)
                 if history:
                     exit_price = float(history[0]['price'])
                     self.logger.info(f"📊 [GHOST RECOVERY] Found fill price from history: {exit_price}")
             except: pass

        # 4. Calculate P&L accurately
        leverage = trade.get('leverage', 1)
        trade_size = trade.get('size', 0)
        entry_price = trade.get('entry_price', 0)
        
        side_mult = 1 if trade['side'].lower() == 'buy' else -1
        pnl_percent = ((exit_price - entry_price) / entry_price) * side_mult if entry_price else 0
        leveraged_pnl_percent = pnl_percent * leverage
        gross_pnl_amount = trade_size * leveraged_pnl_percent
        
        # Calculate exit fee
        fee_pct = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
        exit_fee = trade_size * (1 + pnl_percent) * fee_pct 
        net_pnl_amount = gross_pnl_amount - exit_fee
        
        # 5. Update Database
        exit_time = datetime.utcnow().isoformat()
        try:
            conn = self.db._get_connection(self.db.main_db)
            try:
                cursor = conn.cursor()
                query = "UPDATE trades SET status = ?, exit_price = ?, exit_time = ?, reason = ?, pnl_amount = ?, pnl_percent = ? WHERE trade_id = ?"
                params = ('CLOSED', exit_price, exit_time, reason, net_pnl_amount, leveraged_pnl_percent * 100, trade_id)
                cursor.execute(query, params)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Failed to update trade exit in DB: {e}")

        # 6. Logging & Final report
        self.logger.info(f"🏁 EXIT SYNC: {symbol} | Net P&L: ${net_pnl_amount:.2f} ({leveraged_pnl_percent*100:.1f}%) | Reason: {reason}")
        
        return {
            'verified': True,
            'net_pnl': net_pnl_amount,
            'exit_price': exit_price,
            'capital_return': trade_size + net_pnl_amount,
            'leveraged_pnl_percent': leveraged_pnl_percent
        }
