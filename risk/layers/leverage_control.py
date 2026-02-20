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
        Evaluate and adjust leverage with Dynamic Scaling
        """
        current_drawdown = account_state.get('drawdown_percent', 0)
        confidence = trade_params.get('confidence', 0.5)
        
        # 1. Get adjusted maximum leverage based on drawdown (Layer 2)
        max_leverage = self.config.get_drawdown_adjusted_leverage(current_drawdown)
        
        # 2. Dynamic Leverage scaling based on confidence (Layer 8 integration)
        # Confidence 0.5 = 50% leverage, Confidence 1.0 = 100% leverage
        confidence_scaling = min(max(confidence, 0.1), 1.0)
        dynamic_max = max(1, int(max_leverage * confidence_scaling))
        
        requested_leverage = trade_params.get('leverage', self.config.MAX_LEVERAGE)
        
        if requested_leverage > dynamic_max:
            if dynamic_max == 0:
                self.logger.position_rejected(
                    symbol=trade_params.get('symbol', 'UNKNOWN'),
                    reason='Leverage not allowed',
                    layer='LeverageControl',
                    drawdown=f'{current_drawdown:.2f}%'
                )
                return None
            
            self.logger.info(
                f"[{trade_params.get('strategy', 'Risk')}] Leverage scaled from {requested_leverage}x to {dynamic_max}x (Confidence: {confidence:.2f})"
            )
            trade_params['leverage'] = dynamic_max
        else:
            trade_params['leverage'] = min(requested_leverage, dynamic_max)
        
        self.logger.debug(
            f"Leverage control approved: {trade_params['leverage']}x (max: {dynamic_max}x, conf: {confidence:.2f})"
        )
        
        return trade_params
