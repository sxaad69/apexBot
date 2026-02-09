"""
Universal Strategy Filters Module
Centralized ADX and volume filtering for all strategies
Environment-controlled for testing vs production
"""

from typing import TYPE_CHECKING, Tuple
if TYPE_CHECKING:
    from config.config import Config


class StrategyFilters:
    """Universal filters used by all strategies"""

    def __init__(self, config):
        self.config = config

        # Load filter settings from environment
        self.testing_mode = getattr(config, 'TESTING_MODE', False)

        # ADX Settings
        self.adx_min = getattr(config, 'TESTING_ADX_MIN', 0) if self.testing_mode else 25
        self.force_trades = getattr(config, 'FORCE_TRADES', False)

        # Volume Settings
        self.volume_multiplier = getattr(config, 'TESTING_VOLUME_MULT', 0.1) if self.testing_mode else 1.2

        # Additional filters
        self.min_volume_usdt = getattr(config, 'MIN_VOLUME_USDT', 10000)
        self.max_volatility_percent = getattr(config, 'MAX_VOLATILITY_PERCENT', 5.0)

    def should_trade_symbol(self, df, symbol: str, strategy_name: str) -> Tuple[bool, str]:
        """
        Universal pre-trade filter check
        Returns: (should_trade, reason)
        """
        import pandas as pd

        # 1. ADX Trend Filter
        if not self._check_adx_filter(df):
            return False, f"ADX < {self.adx_min} (weak trend)"

        # 2. Volume Filter
        if not self._check_volume_filter(df):
            return False, f"Volume < {self.volume_multiplier}x average"

        # 3. Minimum Volume USD
        if not self._check_minimum_volume(df):
            return False, f"Volume < ${self.min_volume_usdt} USDT"

        # 4. Volatility Filter (optional)
        if not self._check_volatility_filter(df):
            return False, f"Volatility > {self.max_volatility_percent}%"

        return True, "All filters passed"

    def _check_adx_filter(self, df) -> bool:
        """Check if ADX indicates trending market"""
        # Skip filter if ADX min is 0 or FORCE_TRADES is on
        if self.adx_min <= 0 or self.force_trades:
            return True
            
        if 'adx' not in df.columns:
            return False
        return df.iloc[-1]['adx'] >= self.adx_min

    def _check_volume_filter(self, df) -> bool:
        """Check if volume meets requirements"""
        if 'volume' not in df.columns:
            return False

        current_volume = df.iloc[-1]['volume']
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]

        return current_volume >= (avg_volume * self.volume_multiplier)

    def _check_minimum_volume(self, df) -> bool:
        """Check minimum USD volume"""
        if 'volume' not in df.columns or 'close' not in df.columns:
            return False

        current_volume = df.iloc[-1]['volume']
        current_price = df.iloc[-1]['close']

        volume_usdt = current_volume * current_price
        return volume_usdt >= self.min_volume_usdt

    def _check_volatility_filter(self, df) -> bool:
        """Check if volatility is within limits"""
        if len(df) < 20:
            return True  # Not enough data

        returns = df['close'].pct_change()
        volatility = returns.std() * 100  # Convert to percentage

        return volatility <= self.max_volatility_percent

    def get_filter_status(self) -> dict:
        """Get current filter settings for logging"""
        return {
            'testing_mode': self.testing_mode,
            'adx_minimum': self.adx_min,
            'volume_multiplier': self.volume_multiplier,
            'min_volume_usdt': self.min_volume_usdt,
            'max_volatility_percent': self.max_volatility_percent
        }


# Global instance for all strategies
_filters_instance = None

def get_strategy_filters(config) -> StrategyFilters:
    """Get singleton filters instance"""
    global _filters_instance
    if _filters_instance is None:
        _filters_instance = StrategyFilters(config)
    return _filters_instance
