"""Layer 11: Capital Preservation"""
from typing import Dict, Any, Optional

class CapitalPreservationLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.min_capital_threshold = config.INITIAL_CAPITAL * 0.1  # 10% of initial
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_balance = account_state.get('total_balance', self.config.INITIAL_CAPITAL)
        
        if current_balance <= self.min_capital_threshold:
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Below minimum capital threshold',
                layer='CapitalPreservation',
                current_balance=f'{current_balance:.2f}',
                threshold=f'{self.min_capital_threshold:.2f}'
            )
            return None
        
        return trade_params
