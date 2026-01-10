"""
Strategy A1: EMA Crossover (Production Version)
Simple EMA crossover with full safety filters for live trading
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class StrategyA1(BaseStrategy):
    """
    Production EMA Crossover Strategy
    Conservative settings for live trading
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A1: EMA Only")

        # Conservative EMAs for fewer but higher-quality signals
        self.ema_fast = 9
        self.ema_slow = 21

        self.logger.info(f"Strategy A1 (PRODUCTION) initialized: EMA {self.ema_fast}/{self.ema_slow}")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        return df

    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate signal on EMA crossover with PRODUCTION safety filters

        PRODUCTION SAFETY FILTERS:
        - Requires ADX > 25 (strong trend only)
        - Requires volume > 1.2x average (high liquidity)
        - Conservative confidence scoring
        """
        df = self.calculate_indicators(df)

        if len(df) < 25:
            return None

        # PRODUCTION: Full sideways market protection
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
            return None

        # PRODUCTION: Conservative confidence (lower for safety)
        confidence = 0.5

        # Bullish crossover
        if bullish_crossover:
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
