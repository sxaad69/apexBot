"""Layer 5: Maximum Drawdown"""
from typing import Dict, Any, Optional

class MaximumDrawdownLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.peak_balance = config.INITIAL_CAPITAL
    
    def update_peak(self, current_balance: float):
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        current_balance = account_state.get('total_balance', self.config.INITIAL_CAPITAL)
        self.update_peak(current_balance)
        
        drawdown = ((self.peak_balance - current_balance) / self.peak_balance) * 100
        
        # Implementation of "The Signal Gate" (Phase 14)
        if hasattr(self.config, 'TIERED_RISK_ENABLED') and self.config.TIERED_RISK_ENABLED:
            confidence = trade_params.get('confidence', 0.5)
            is_elite = confidence >= self.config.ELITE_CONFIDENCE_LEVEL
            
            # Tier 1: Hard Halt (e.g. 70%)
            if drawdown >= self.config.ELITE_SIGNAL_THRESHOLD:
                self.logger.position_rejected(
                    symbol=trade_params.get('symbol', 'UNKNOWN'),
                    reason='Maximum drawdown exceeded (Hard Halt)',
                    layer='MaximumDrawdown',
                    current_drawdown=f'{drawdown:.2f}%',
                    max_allowed=f'{self.config.ELITE_SIGNAL_THRESHOLD}%'
                )
                return None
            
            # Tier 2: Normal Signal Gate (e.g. 50%)
            if drawdown >= self.config.NORMAL_SIGNAL_THRESHOLD and not is_elite:
                self.logger.position_rejected(
                    symbol=trade_params.get('symbol', 'UNKNOWN'),
                    reason='Preserving capital for Elite signals (Normal Gate)',
                    layer='MaximumDrawdown',
                    current_drawdown=f'{drawdown:.2f}%',
                    confidence=f'{confidence:.2f}'
                )
                return None
        
        else:
            # Legacy/Fallback Mode
            if drawdown >= self.config.MAX_DRAWDOWN_PERCENT:
                self.logger.position_rejected(
                    symbol=trade_params.get('symbol', 'UNKNOWN'),
                    reason='Maximum drawdown exceeded',
                    layer='MaximumDrawdown',
                    current_drawdown=f'{drawdown:.2f}%',
                    max_allowed=f'{self.config.MAX_DRAWDOWN_PERCENT}%'
                )
                return None
        
        return trade_params
