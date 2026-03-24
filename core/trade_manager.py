import json
import logging
import uuid
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
                     order_response: Optional[Dict] = None, planned_price: float = 0):
        """
        Grounded Entry Recorder.
        Synchronizes the planned entry with the actual exchange fill.
        """
        # 1. Capture Actual Entry Price (Grounding)
        entry_price = planned_price
        if order_response and order_response.get('average'):
            entry_price = float(order_response['average'])
            self.logger.info(f"📊 [GROUNDED] Actual entry for {symbol}: {entry_price}")
        
        entry_time = datetime.now()
        trade_id = f"FUT-{uuid.uuid4().hex[:8].upper()}"
        
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
            'metadata': json.dumps(order_response) if order_response else "{}"
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

    def record_exit(self, symbol: str, trade_id: str, reason: str, 
                    current_price: float, order_response: Optional[Dict] = None):
        """
        Grounded Exit Recorder.
        Calculates true P&L based on actual fill and updates all systems.
        """
        # 1. Fetch trade from DB to get entry data
        trades = self.db.get_trades(status='OPEN')
        trade = next((t for t in trades if t['trade_id'] == trade_id), None)
        
        if not trade:
            self.logger.warning(f"Attempted to close unknown trade_id: {trade_id}")
            return None

        # 2. Capture Actual Exit Price (Grounding)
        exit_price = current_price
        if order_response and order_response.get('average'):
            exit_price = float(order_response['average'])
            self.logger.info(f"📊 [GROUNDED] Actual exit for {symbol}: {exit_price}")
        elif (not exit_price or exit_price == 0) and getattr(self.config, 'TRADING_MODE', 'paper') == 'live':
             # Try to fetch from history if it's a ghost trade
             try:
                 history = self.exchange.exchange.fetch_my_trades(symbol, limit=1)
                 if history:
                     exit_price = float(history[0]['price'])
                     self.logger.info(f"📊 [GHOST RECOVERY] Found fill price from history: {exit_price}")
             except: pass

        # 3. Calculate P&L accurately
        leverage = trade.get('leverage', 1)
        size = trade.get('size', 0)
        entry_price = trade.get('entry_price', 0)
        
        side_mult = 1 if trade['side'].lower() == 'buy' else -1
        pnl_percent = ((exit_price - entry_price) / entry_price) * side_mult if entry_price else 0
        leveraged_pnl_percent = pnl_percent * leverage
        gross_pnl_amount = size * leveraged_pnl_percent
        
        # Calculate exit fee
        fee_pct = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
        exit_fee = size * (1 + pnl_percent) * fee_pct 
        net_pnl_amount = gross_pnl_amount - exit_fee
        
        # 4. Update Database
        exit_time = datetime.utcnow().isoformat()
        conn = self.db._get_connection(self.db.main_db)
        cursor = conn.cursor()
        query = "UPDATE trades SET status = ?, exit_price = ?, exit_time = ?, reason = ?, pnl_amount = ?, pnl_percent = ? WHERE trade_id = ?"
        params = ('CLOSED', exit_price, exit_time, reason, net_pnl_amount, leveraged_pnl_percent * 100, trade_id)
        cursor.execute(query, params)
        conn.commit()
        conn.close()

        # 5. Logging & Final report
        self.logger.info(f"🏁 EXIT SYNC: {symbol} | Net P&L: ${net_pnl_amount:.2f} ({leveraged_pnl_percent*100:.1f}%) | Reason: {reason}")
        
        return {
            'net_pnl': net_pnl_amount,
            'exit_price': exit_price,
            'capital_return': size + net_pnl_amount,
            'leveraged_pnl_percent': leveraged_pnl_percent
        }
