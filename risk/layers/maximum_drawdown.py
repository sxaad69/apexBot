"""Layer 5: Maximum Drawdown"""
from typing import Dict, Any, Optional

class MaximumDrawdownLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.peak_balance = config.INITIAL_CAPITAL
    
    def update_peak(self, current_balance: float):
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_balance = account_state.get('total_balance', self.config.INITIAL_CAPITAL)
        self.update_peak(current_balance)
        
        drawdown = ((self.peak_balance - current_balance) / self.peak_balance) * 100
        
        if drawdown >= self.config.MAX_DRAWDOWN_PERCENT:
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Maximum drawdown exceeded',
                layer='MaximumDrawdown',
                current_drawdown=f'{drawdown:.2f}%',
                max_allowed=f'{self.config.MAX_DRAWDOWN_PERCENT}%'
            )
            self.logger.risk_layer_triggered(
                layer='MaximumDrawdown',
                reason=f'Drawdown {drawdown:.2f}% exceeds limit',
                action='Trading halted'
            )
            return None
        
        return trade_params
