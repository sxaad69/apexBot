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
        
        # 1. Strategy-Specific Override (High Priority)
        target_sl_roe = trade_params.get('target_sl_roe')
        
        # 2. Global ROE Fallback (Master Safety Net)
        if target_sl_roe is None:
            target_sl_roe = getattr(self.config, 'GLOBAL_STOP_LOSS_ROE', 5.0)
            self.logger.debug(f"Applied Global 5% ROE Stop Loss for Strategy: {trade_params.get('strategy', 'Unknown')}")

        # 3. Calculate Price Move based on Leverage
        final_sl_percent = abs(target_sl_roe) / leverage
        
        # 4. Final Safety Cutoff (Equity-Aware Limit)
        max_equity_risk = getattr(self.config, 'MAX_EQUITY_RISK_PERCENT', 3.0)
        equity_aware_sl_percent = max_equity_risk / leverage
        
        # Use the tighter of the two for ultimate capital protection
        final_sl_percent = min(final_sl_percent, equity_aware_sl_percent)
        
        if side == 'buy':
            stop_loss_price = entry_price * (1 - final_sl_percent / 100)
        else:
            stop_loss_price = entry_price * (1 + final_sl_percent / 100)
        
        trade_params['stop_loss'] = stop_loss_price
        trade_params['stop_loss_percent'] = final_sl_percent
        
        self.logger.debug(f"Stop Loss set: {final_sl_percent:.2f}% (Price: {stop_loss_price:.2f}) | Leverage: {leverage}x")
        
        return trade_params
