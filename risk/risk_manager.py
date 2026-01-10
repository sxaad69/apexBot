"""
Risk Manager
Coordinates all 11 risk management layers
"""

from typing import Dict, Any, Optional
from .layers import *


class RiskManager:
    """
    Central Risk Manager
    Evaluates trades through all 11 risk layers sequentially
    """
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        
        # Initialize all layers in order
        self.layers = [
            PositionSizingLayer(config, logger),           # Layer 1
            LeverageControlLayer(config, logger),          # Layer 2
            StopLossManagementLayer(config, logger),       # Layer 3
            DailyLossLimitLayer(config, logger),           # Layer 4
            MaximumDrawdownLayer(config, logger),          # Layer 5
            CorrelationRiskLayer(config, logger),          # Layer 6
            VolatilityAdjustmentLayer(config, logger),     # Layer 7
            LiquidityCheckLayer(config, logger),           # Layer 8
            RateLimitLayer(config, logger),                # Layer 9
            CircuitBreakerLayer(config, logger),           # Layer 10
            CapitalPreservationLayer(config, logger)       # Layer 11
        ]
        
        self.logger.system("Risk Manager initialized with 11 layers")
    
    def evaluate_trade(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate trade through all risk layers

        Args:
            trade_params: Proposed trade parameters
            account_state: Current account state

        Returns:
            Approved trade parameters or None if rejected
        """

        symbol = trade_params.get('symbol', 'Unknown')

        # Pass trade through each layer sequentially
        approved_params = trade_params.copy()

        for layer in self.layers:
            layer_name = layer.__class__.__name__
            self.logger.debug(f"Risk evaluation: {layer_name} evaluating {symbol}")

            result = layer.evaluate(approved_params, account_state)

            if result is None:
                # Trade rejected by this layer
                self.logger.warning(f"Risk evaluation: {layer_name} rejected {symbol} - trade blocked")
                return None
            else:
                self.logger.debug(f"Risk evaluation: {layer_name} approved {symbol}")
                approved_params = result

        # All layers approved
        self.logger.debug(f"Risk evaluation: Trade approved through all {len(self.layers)} risk layers for {symbol}")
        return approved_params
    
    def record_trade_result(self, is_win: bool, pnl: float):
        """Record trade result for layers that track history"""
        # Update daily loss limit
        self.layers[3].record_trade(pnl)  # DailyLossLimitLayer
        
        # Update circuit breaker
        self.layers[9].record_trade_result(is_win)  # CircuitBreakerLayer
    
    def record_critical_failure(self, reason: str):
        """Record a critical failure that should trigger circuit breaker"""
        self.layers[9].record_critical_failure(reason)  # CircuitBreakerLayer
    
    def is_trading_halted(self) -> bool:
        """Check if trading is halted by circuit breaker"""
        return self.layers[9].is_halted()
    
    def update_peak_balance(self, current_balance: float):
        """Update peak balance for drawdown calculation"""
        self.layers[4].update_peak(current_balance)  # MaximumDrawdownLayer
