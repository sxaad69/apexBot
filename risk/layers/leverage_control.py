"""
Layer 2: Leverage Control
Enforces maximum leverage limits and adjusts based on market conditions
"""

from typing import Dict, Any, Optional


class LeverageControlLayer:
    """
    Layer 2: Leverage Control
    Prevents excessive leverage and adjusts based on account drawdown
    """
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate and adjust leverage
        
        Args:
            trade_params: Trade parameters with position_size
            account_state: Current account state
        
        Returns:
            Approved trade params with leverage, or None if rejected
        """
        current_drawdown = account_state.get('drawdown_percent', 0)
        
        # Get adjusted maximum leverage based on drawdown
        max_leverage = self.config.get_drawdown_adjusted_leverage(current_drawdown)
        
        requested_leverage = trade_params.get('leverage', self.config.MAX_LEVERAGE)
        
        if requested_leverage > max_leverage:
            if max_leverage == 0:
                self.logger.position_rejected(
                    symbol=trade_params.get('symbol', 'UNKNOWN'),
                    reason='Leverage not allowed due to high drawdown',
                    layer='LeverageControl',
                    drawdown=f'{current_drawdown:.2f}%'
                )
                return None
            
            self.logger.warning(
                f"Leverage reduced from {requested_leverage}x to {max_leverage}x due to drawdown"
            )
            trade_params['leverage'] = max_leverage
        else:
            trade_params['leverage'] = min(requested_leverage, max_leverage)
        
        self.logger.debug(
            f"Leverage control approved: {trade_params['leverage']}x (max: {max_leverage}x)"
        )
        
        return trade_params
