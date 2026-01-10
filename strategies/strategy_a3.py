"""
Strategy A3: Fast EMA (Scalping)
Faster EMAs for more frequent signals
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class StrategyA3(BaseStrategy):
    """
    Fast EMA Scalping Strategy
    Shortest EMAs = Most trades
    Generates ~100-150 trades per 180 days
    """
    
    def __init__(self, config, logger):
        super().__init__(config, logger, "A3: Fast Scalp")
        
        # Very short EMAs for scalping
        self.ema_fast = 5
        self.ema_slow = 13
        
        self.logger.info(f"Strategy A3 initialized: Fast EMA {self.ema_fast}/{self.ema_slow}")
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate fast EMAs"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        return df
    
    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate signals on fast EMA crosses
        More signals = More opportunities

        WITH SIDEWAYS MARKET PROTECTION:
        - Requires ADX > 20 (lower threshold for scalping)
        - Requires volume > 1.5x average (higher threshold for scalping)
        - CRITICAL: Fast scalp needs this to avoid chop!
        """
        df = self.calculate_indicators(df)

        if len(df) < 15:
            return None

        # SIDEWAYS MARKET PROTECTION (CRITICAL for scalping!)
        df = self.calculate_adx(df)

        # Lower ADX threshold (20 vs 25) since we're scalping
        # But HIGHER volume requirement (1.5x vs 1.2x)
        if not self.is_trending_market(df, min_adx=20):
            return None  # Skip trade - sideways market

        # Stricter volume requirement for scalping
        if not self.has_volume_confirmation(df, multiplier=1.5):
            return None  # Skip trade - low volume = whipsaw risk

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # Increase confidence with filters (was 0.4, now 0.55)
        confidence = 0.55

        # Bullish cross
        if current['ema_fast'] > current['ema_slow'] and previous['ema_fast'] <= previous['ema_slow']:
            return {
                'symbol': 'BTC/USDT',
                'side': 'buy',
                'entry_price': current['close'],
                'stop_loss': current['close'] * (1 - self.config.FUTURES_STOP_LOSS_PERCENT / 100),
                'take_profit': current['close'] * (1 + self.config.FUTURES_TAKE_PROFIT_PERCENT / 100),
                'confidence': confidence,
                'strategy': self.name,
                'indicators': {
                    'ema_fast': current['ema_fast'],
                    'ema_slow': current['ema_slow'],
                    'adx': current['adx']
                }
            }

        # Bearish cross
        elif current['ema_fast'] < current['ema_slow'] and previous['ema_fast'] >= previous['ema_slow']:
            return {
                'symbol': 'BTC/USDT',
                'side': 'sell',
                'entry_price': current['close'],
                'stop_loss': current['close'] * (1 + self.config.FUTURES_STOP_LOSS_PERCENT / 100),
                'take_profit': current['close'] * (1 - self.config.FUTURES_TAKE_PROFIT_PERCENT / 100),
                'confidence': confidence,
                'strategy': self.name,
                'indicators': {
                    'ema_fast': current['ema_fast'],
                    'ema_slow': current['ema_slow'],
                    'adx': current['adx']
                }
            }

        return None