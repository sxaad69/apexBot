"""Layer 3: Stop Loss Management"""
from typing import Dict, Any, Optional

class StopLossManagementLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        entry_price = trade_params.get('entry_price', 0)
        side = trade_params.get('side', 'buy')
        leverage = trade_params.get('leverage', 1)
        
        # 1. Base SL from config (Historical fixed %)
        config_sl_percent = getattr(self.config, 'FUTURES_STOP_LOSS_PERCENT', 2.0)
        
        # 2. Equity-Aware SL (Fixed Risk on Balance)
        # Risk_Price % = Max_Equity_Loss % / Leverage
        max_equity_risk = getattr(self.config, 'MAX_EQUITY_RISK_PERCENT', 3.0)
        equity_aware_sl_percent = max_equity_risk / leverage
        
        # 3. Use the more conservative (smaller) stop loss move
        final_sl_percent = min(config_sl_percent, equity_aware_sl_percent)
        
        if side == 'buy':
            stop_loss_price = entry_price * (1 - final_sl_percent / 100)
        else:
            stop_loss_price = entry_price * (1 + final_sl_percent / 100)
        
        trade_params['stop_loss'] = stop_loss_price
        trade_params['stop_loss_percent'] = final_sl_percent
        trade_params['equity_risk_capped'] = final_sl_percent < config_sl_percent
        
        self.logger.debug(f"Stop Loss set: {final_sl_percent:.2f}% (Price: {stop_loss_price:.2f}) | Leverage: {leverage}x")
        
        return trade_params
