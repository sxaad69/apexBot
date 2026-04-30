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
        
        # 2. Strategy-Specific Config Override (Med Priority)
        if target_sl_roe is None:
            strategy_tag = trade_params.get('strategy', '')
            if "A5" in strategy_tag or "A6" in strategy_tag:
                # Institutional strategies (A5/A6) use wider ROE stops for breathing room
                override_key = f"{strategy_tag.split(':')[0]}_STOP_LOSS_ROE"
                target_sl_roe = getattr(self.config, override_key, 15.0)
            else:
                target_sl_roe = getattr(self.config, 'GLOBAL_STOP_LOSS_ROE', 5.0)
                self.logger.debug(f"Applied Global {target_sl_roe}% ROE Stop Loss for Strategy: {strategy_tag}")

        # 3. Calculate Price Move (Unified Price-Based)
        final_sl_percent = abs(target_sl_roe)
        
        # 4. Final Safety Cutoff (ROE-Aware Limit)
        max_roe_drawdown = getattr(self.config, 'MAX_ROE_DRAWDOWN', 20.0)
        equity_aware_sl_percent = max_roe_drawdown / leverage
        
        # Use the tighter of the two for ultimate capital protection
        final_sl_percent = min(final_sl_percent, equity_aware_sl_percent)
        
        if side == 'buy':
            stop_loss_price = entry_price * (1 - final_sl_percent / 100)
        else:
            stop_loss_price = entry_price * (1 + final_sl_percent / 100)
        
        trade_params['stop_loss'] = stop_loss_price
        trade_params['stop_loss_percent'] = final_sl_percent
        trade_params['stop_loss_roe'] = target_sl_roe
        
        self.logger.debug(f"Stop Loss set: {final_sl_percent:.2f}% (Price: {stop_loss_price:.2f}) | Leverage: {leverage}x")
        
        return trade_params
