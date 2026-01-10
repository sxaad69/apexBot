"""
Strategy A1: EMA Crossover (Active Trading)
Simple EMA crossover - trades frequently
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class StrategyA1(BaseStrategy):
    """
    Simple EMA Crossover Strategy
    Generates ~50-100 trades per 180 days
    """
    
    def __init__(self, config, logger):
        super().__init__(config, logger, "A1: EMA Only")
        
        # Shorter EMAs for more signals
        self.ema_fast = 9
        self.ema_slow = 21
        
        self.logger.info(f"Strategy A1 initialized: EMA {self.ema_fast}/{self.ema_slow}")
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        return df
    
    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate signal on EMA crossover
        LONG: Fast crosses above Slow
        SHORT: Fast crosses below Slow

        WITH SIDEWAYS MARKET PROTECTION:
        - Requires ADX > 25 (strong trend)
        - Requires volume > 1.2x average
        """
        df = self.calculate_indicators(df)

        if len(df) < 25:
            self.logger.debug(f"Insufficient data: {len(df)} candles < 25 required")
            return None

        # PRODUCTION: Strict sideways market protection
        df = self.calculate_adx(df)

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # Check if market is trending (PRODUCTION: strict ADX > 25)
        if not self.is_trending_market(df, min_adx=25):
            return None  # Skip trade - sideways market

        # PRODUCTION: Strict volume confirmation (1.2x average)
        if not self.has_volume_confirmation(df, multiplier=1.2):
            return None  # Skip trade - low volume (fake move)

        # Check for EMA crossover
        bullish_crossover = current['ema_fast'] > current['ema_slow'] and previous['ema_fast'] <= previous['ema_slow']
        bearish_crossover = current['ema_fast'] < current['ema_slow'] and previous['ema_fast'] >= previous['ema_slow']

        if not (bullish_crossover or bearish_crossover):
            self.logger.debug("No EMA crossover detected")
            return None

        # Increase confidence since we have trend + volume confirmation
        confidence = 0.6  # Was 0.5, now 0.6 with filters

        # Bullish crossover
        if bullish_crossover:
            self.logger.debug(f"EMA crossover detected: Fast ({current['ema_fast']:.2f}) crossed above Slow ({current['ema_slow']:.2f}) - BUY signal")
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

        # Bearish crossover
        elif bearish_crossover:
            self.logger.debug(f"EMA crossover detected: Fast ({current['ema_fast']:.2f}) crossed below Slow ({current['ema_slow']:.2f}) - SELL signal")
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
