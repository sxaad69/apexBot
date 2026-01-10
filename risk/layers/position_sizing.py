"""
Layer 1: Position Sizing
Calculates appropriate position size based on capital and risk parameters
"""

from typing import Dict, Any, Optional


class PositionSizingLayer:
    """
    Layer 1: Position Sizing
    Determines appropriate position size based on available capital,
    desired leverage, and current account state
    """
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate and calculate position size
        
        Args:
            trade_params: Trade parameters (symbol, side, etc.)
            account_state: Current account state (balance, drawdown, etc.)
        
        Returns:
            Approved trade params with position size, or None if rejected
        """
        available_capital = account_state.get('available_balance', 0)
        current_drawdown = account_state.get('drawdown_percent', 0)
        
        # Get base position size percentage
        base_position_percent = self.config.POSITION_SIZE_PERCENT
        
        # Adjust for drawdown
        drawdown_multiplier = self.config.get_drawdown_adjusted_position_size(current_drawdown)
        
        if drawdown_multiplier == 0:
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Maximum drawdown reached',
                layer='PositionSizing',
                current_drawdown=f'{current_drawdown:.2f}%'
            )
            return None
        
        # Calculate position size
        adjusted_percent = base_position_percent * drawdown_multiplier
        position_size = (available_capital * adjusted_percent / 100)
        
        # Apply min/max limits
        if position_size < self.config.MIN_POSITION_SIZE:
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Position size below minimum',
                layer='PositionSizing',
                calculated_size=f'{position_size:.2f}',
                minimum=f'{self.config.MIN_POSITION_SIZE:.2f}'
            )
            return None
        
        position_size = min(position_size, self.config.MAX_POSITION_SIZE)
        
        # Update trade parameters
        trade_params['position_size'] = position_size
        trade_params['risk_percent'] = adjusted_percent
        
        self.logger.debug(
            f"Position sizing approved: {position_size:.2f} USDT ({adjusted_percent:.1f}% of capital)"
        )
        
        return trade_params
