"""
Portfolio Loss Circuit Breaker
Halts new trades if aggregate portfolio net ROE drops below a threshold.
"""

from datetime import datetime, timedelta

class PortfolioCircuitBreaker:
    def __init__(self, config, db, logger):
        self.config = config
        self.db = db
        self.logger = logger
        
    def check_and_trigger(self, active_positions: dict, current_prices: dict):
        if not getattr(self.config, 'LOSS_CB_ENABLED', True):
            return
            
        if not active_positions:
            return
            
        total_margin = 0.0
        total_unrealized_pnl = 0.0
        
        for pos_key, pos in active_positions.items():
            symbol = pos['symbol']
            entry = pos['entry_price']
            size = pos.get('size', 0)
            leverage = pos.get('leverage', 1)
            side = pos['side']
            
            if size == 0:
                continue
                
            current_price = current_prices.get(symbol, entry)
            
            # Approximate margin
            margin = (entry * size) / leverage
            total_margin += margin
            
            # Unrealized PnL
            if side == 'buy':
                pnl = (current_price - entry) * size
            else:
                pnl = (entry - current_price) * size
                
            total_unrealized_pnl += pnl
            
        if total_margin <= 0:
            return
            
        # Deduct estimated 0.12% taker round-trip fees from PnL
        estimated_fees = (total_margin * leverage) * 0.0012
        net_pnl = total_unrealized_pnl - estimated_fees
        
        net_roe_pct = (net_pnl / total_margin) * 100
        
        loss_cb_pct = getattr(self.config, 'LOSS_CB_PCT', 10.0)
        
        if net_roe_pct <= -loss_cb_pct:
            cooldown_minutes = getattr(self.config, 'LOSS_CB_COOLDOWN_MINUTES', 30)
            cooldown_until = (datetime.utcnow() + timedelta(minutes=cooldown_minutes)).isoformat()
            
            # Log event
            self.logger.critical(f"🚨 PORTFOLIO CIRCUIT BREAKER TRIGGERED! Net ROE: {net_roe_pct:.2f}% (Limit: -{loss_cb_pct}%)")
            
            # Save to DB
            try:
                conn = self.db._get_connection(self.db.main_db)
                cursor = conn.cursor()
                cursor.execute(
                    '''INSERT INTO circuit_breaker_events 
                       (triggered_at, net_roe_pct, positions_closed, cooldown_minutes, cooldown_until)
                       VALUES (?, ?, ?, ?, ?)''',
                    (datetime.utcnow().isoformat(), net_roe_pct, len(active_positions), cooldown_minutes, cooldown_until)
                )
                
                # Save cooldown to settings table so it persists across restarts
                cursor.execute(
                    "INSERT OR REPLACE INTO settings (key, value, last_updated) VALUES (?, ?, ?)",
                    ('circuit_breaker_cooldown_until', cooldown_until, datetime.utcnow().isoformat())
                )
                
                conn.commit()
                conn.close()
            except Exception as e:
                self.logger.error(f"Failed to save circuit breaker event to DB: {e}")
                
    def is_in_cooldown(self) -> bool:
        if not getattr(self.config, 'LOSS_CB_ENABLED', True):
            return False
            
        try:
            conn = self.db._get_connection(self.db.main_db)
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = 'circuit_breaker_cooldown_until'")
            row = cursor.fetchone()
            conn.close()
            
            if row and row['value']:
                cooldown_until = datetime.fromisoformat(row['value'])
                if datetime.utcnow() < cooldown_until:
                    return True
        except Exception as e:
            self.logger.error(f"Failed to check circuit breaker cooldown: {e}")
            
        return False
