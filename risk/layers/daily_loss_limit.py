"""Layer 4: Daily Loss Limit"""
from typing import Dict, Any, Optional
from datetime import datetime, date

class DailyLossLimitLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.daily_pnl = 0.0
        self.current_date = date.today()
    
    def reset_if_new_day(self):
        if date.today() != self.current_date:
            self.daily_pnl = 0.0
            self.current_date = date.today()
            self.logger.system("Daily loss limit reset for new trading day")
    
    def record_trade(self, pnl: float):
        self.reset_if_new_day()
        self.daily_pnl += pnl
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.reset_if_new_day()
        
        initial_capital = self.config.INITIAL_CAPITAL
        max_loss = initial_capital * (self.config.MAX_DAILY_LOSS_PERCENT / 100)
        
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
