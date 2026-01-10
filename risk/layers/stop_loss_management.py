"""Layer 3: Stop Loss Management"""
from typing import Dict, Any, Optional

class StopLossManagementLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        entry_price = trade_params.get('entry_price', 0)
        side = trade_params.get('side', 'buy')
        stop_loss_percent = self.config.STOP_LOSS_PERCENT
        
        if side == 'buy':
            stop_loss_price = entry_price * (1 - stop_loss_percent / 100)
        else:
            stop_loss_price = entry_price * (1 + stop_loss_percent / 100)
        
        trade_params['stop_loss'] = stop_loss_price
        trade_params['stop_loss_percent'] = stop_loss_percent
        
        return trade_params
