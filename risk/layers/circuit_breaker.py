"""
Layer 10: Circuit Breaker
Emergency shutdown on abnormal conditions
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class CircuitBreakerLayer:
    """
    Layer 10: Emergency Circuit Breaker
    Triggers emergency halt on critical failures or market conditions
    """
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.consecutive_losses = 0
        self.halt_until = None
        self.last_trade_time = None
    
    def record_trade_result(self, is_win: bool):
        """Record trade result for consecutive loss tracking"""
        if is_win:
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            
            if self.consecutive_losses >= self.config.CONSECUTIVE_LOSSES_THRESHOLD:
                self._trigger_halt("Consecutive losses threshold reached")
    
    def record_critical_failure(self, reason: str):
        """Record a critical failure and trigger halt"""
        if self.config.TRADE_FAILURE_HALT_HOURS > 0:
            self._trigger_halt(f"Critical failure: {reason}")
    
    def _trigger_halt(self, reason: str):
        """Trigger trading halt"""
        if self.config.TRADE_FAILURE_HALT_HOURS > 0:
            self.halt_until = datetime.now() + timedelta(hours=self.config.TRADE_FAILURE_HALT_HOURS)
            self.logger.risk_layer_triggered(
                layer='CircuitBreaker',
                reason=reason,
                action=f'Trading halted for {self.config.TRADE_FAILURE_HALT_HOURS} hours',
                halt_until=self.halt_until.isoformat()
            )
            self.logger.critical(f"🚨 CIRCUIT BREAKER ACTIVATED: {reason}")
    
    def is_halted(self) -> bool:
        """Check if trading is currently halted"""
        if self.halt_until is None:
            return False
        
        if datetime.now() < self.halt_until:
            return True
        else:
            # Halt period expired
            self.halt_until = None
            self.consecutive_losses = 0
            self.logger.system("Circuit breaker halt period expired, trading resumed")
            return False
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Evaluate if trading should be allowed"""
        
        if not self.config.ENABLE_CIRCUIT_BREAKER:
            return trade_params
        
        # Check if currently halted
        if self.is_halted():
            time_remaining = (self.halt_until - datetime.now()).total_seconds() / 3600
            self.logger.position_rejected(
                symbol=trade_params.get('symbol', 'UNKNOWN'),
                reason='Circuit breaker active',
                layer='CircuitBreaker',
                hours_remaining=f'{time_remaining:.1f}h'
            )
            return None
        
        # Check for flash crash conditions
        current_price = trade_params.get('current_price', 0)
        entry_price = trade_params.get('entry_price', current_price)
        
        if current_price > 0 and entry_price > 0:
            price_change = ((current_price - entry_price) / entry_price) * 100
            
            if price_change <= self.config.FLASH_CRASH_THRESHOLD:
                self._trigger_halt(f"Flash crash detected: {price_change:.2f}% drop")
                return None
        
        return trade_params
