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
    
    def __init__(self, config, logger, db_manager=None, profit_ratchet=None):
        self.config = config
        self.logger = logger
        self.db = db_manager
        self.profit_ratchet = profit_ratchet
        
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
        
        # --- LAYER 0: GLOBAL PROFIT RATCHET GUARD ---
        if self._is_ratchet_locked():
            self.logger.warning(f"🚫 Risk evaluation: BLOCKED by Portfolio Profit Ratchet (Liquidation or Cooldown active)")
            if self.db:
                self.db.log_rejection({
                    'symbol': symbol,
                    'strategy': trade_params.get('strategy', 'Unknown'),
                    'reason': "BLOCKED_BY_RATCHET_COOLDOWN",
                    'layer': "GlobalRatchet"
                })
            return None

        # Pass trade through each layer sequentially
        approved_params = trade_params.copy()
        confidence = trade_params.get('confidence', 0.0)

        for layer in self.layers:
            layer_name = layer.__class__.__name__

            result = layer.evaluate(approved_params, account_state)

            if result is None:
                # Trade rejected by this layer
                reason = "Blocked by risk component"
                
                self.logger.warning(f"Risk evaluation: {layer_name} rejected {symbol} - trade blocked")
                
                # Log to forensic DB
                if self.db:
                    rejection_data = {
                        'symbol': symbol,
                        'strategy': trade_params.get('strategy', 'Unknown'),
                        'side': trade_params.get('side', 'buy'),
                        'entry_price': trade_params.get('entry_price', 0.0),
                        'reason': f"REJECTED_{layer_name.upper()}",
                        'layer': layer_name,
                        'confidence': trade_params.get('confidence', 0.0),
                        'metadata': {
                            'account_state': {
                                'total_balance': account_state.get('total_balance'),
                                'drawdown_percent': account_state.get('drawdown_percent'),
                                'open_positions_count': account_state.get('open_positions_count')
                            },
                            'trade_params': trade_params
                        }
                    }
                    self.db.log_rejection(rejection_data)
                
                return None

            else:
                self.logger.debug(f"Risk evaluation: {layer_name} approved {symbol}")
                approved_params = result
                # Ensure confidence persists if layer returns a fresh dict
                if 'confidence' not in approved_params:
                    approved_params['confidence'] = confidence

        # All layers approved
        approved_params['confidence'] = confidence
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

    def _is_ratchet_locked(self) -> bool:
        """Checks if the bot is currently in a profit-ratchet cooldown or liquidation state.
        
        When profit_ratchet is injected, its is_locked() already handles both the
        active-liquidation flag AND the DB cooldown check — no need to double-read DB.
        The DB-only fallback path handles bot restarts when no live ratchet is present.
        """
        # Path 1: Live instance available (covers active liquidation + DB cooldown in one call)
        if self.profit_ratchet is not None:
            return self.profit_ratchet.is_locked()

        # Path 2: No live instance — fall back to DB (e.g. in scripts, forensic tools, after restart)
        if not self.db:
            return False
            
        try:
            from datetime import datetime
            cooldown_val = self.db.get_setting('portfolio_ratchet_cooldown_until')
            if cooldown_val:
                until = datetime.fromisoformat(cooldown_val)
                if datetime.utcnow() < until:
                    return True
        except:
            pass
        return False
