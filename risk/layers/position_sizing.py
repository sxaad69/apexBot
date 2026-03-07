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

        # 1. Determine base size
        # Prefer 'size' from trade_params (calculated by engine based on confidence/reserves)
        # Fall back to base percentage if size is not provided
        if 'size' in trade_params:
            base_size = trade_params['size']
        else:
            base_size = (available_capital * base_position_percent / 100)

        # 2. Adjust for drawdown reduction (67% -> 33% -> 0%)
        # Note: Floor logic below will round this back up to 10.0 if balance allows
        calculated_size = base_size * drawdown_multiplier

        # 3. Implementation of "Position Size Floor" (Phase 14)
        # Keeps trades at MIN_POSITION_SIZE even during drawdown size reductions
        if calculated_size < self.config.MIN_POSITION_SIZE:
            position_size = self.config.MIN_POSITION_SIZE
            self.logger.info(
                f"Sizing Floor Active: {trade_params.get('symbol')} "
                f"rounding up to {position_size:.2f} (Calc: {calculated_size:.2f})"
            )
        else:
            position_size = calculated_size

        # 4. Safety Check: Never risk more than 50% of available capital on a single trade
        if position_size > (available_capital * 0.5):
             self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Position size exceeds 50% of capital (Account too small)',
                layer='PositionSizing',
                calculated_size=f'{position_size:.2f}',
                available=f'{available_capital:.2f}'
            )
             return None
        
        position_size = min(position_size, self.config.MAX_POSITION_SIZE)
        
        # Calculate actual risk percentage for logging and tracking
        adjusted_percent = (position_size / available_capital * 100) if available_capital > 0 else 0
        
        # Update trade parameters
        trade_params['position_size'] = position_size
        trade_params['risk_percent'] = adjusted_percent
        
        self.logger.debug(
            f"Position sizing approved: {position_size:.2f} USDT ({adjusted_percent:.1f}% of capital)"
        )
        
        return trade_params
