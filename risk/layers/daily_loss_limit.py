"""Layer 4: Daily Loss Limit"""
from typing import Dict, Any, Optional
from datetime import datetime, date

class DailyLossLimitLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.daily_pnl = 0.0
        # Use UTC date to match exchange timestamps
        self.current_date = datetime.utcnow().date()
    
    def reset_if_new_day(self):
        utc_today = datetime.utcnow().date()
        if utc_today != self.current_date:
            old_pnl = self.daily_pnl
            self.daily_pnl = 0.0
            self.current_date = utc_today
            self.logger.info(f"🔄 Daily loss limit RESET for new trading day (UTC). Previous day P&L: ${old_pnl:.2f}")
    
    def record_trade(self, pnl: float):
        self.reset_if_new_day()
        self.daily_pnl += pnl
        self.logger.info(f"📊 Daily P&L Update: ${self.daily_pnl:.2f} (Current Limit: {-(self.config.INITIAL_CAPITAL * self.config.MAX_DAILY_LOSS_PERCENT / 100):.2f})")
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.reset_if_new_day()
        
        # Calculate dynamic limit based on current/initial capital
        initial_capital = getattr(self.config, 'INITIAL_CAPITAL', 1000)
        max_loss = initial_capital * (getattr(self.config, 'MAX_DAILY_LOSS_PERCENT', 15) / 100)
        
        if self.daily_pnl <= -max_loss:
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Daily loss limit reached',
                layer='DailyLossLimit',
                daily_pnl=f'{self.daily_pnl:.2f}',
                limit=f'{-max_loss:.2f}'
            )
            return None
        
        return trade_params
