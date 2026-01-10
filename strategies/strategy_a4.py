"""
Strategy A4: EMA + Trend Filter (Selective)
Trades with trend - fewer but higher quality
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class StrategyA4(BaseStrategy):
    """
    EMA with Trend Filter
    Only trades in direction of trend (with 5-candle lookback)
    Generates ~30-45 trades per 180 days (highest quality)
    """
    
    def __init__(self, config, logger):
        super().__init__(config, logger, "A4: Trend Filter")
        
        self.ema_fast = 9
        self.ema_slow = 21
        self.ema_trend = 50
        
        self.logger.info(f"Strategy A4 initialized: EMA {self.ema_fast}/{self.ema_slow}/{self.ema_trend}")
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        df['ema_trend'] = df['close'].ewm(span=self.ema_trend, adjust=False).mean()
        return df
    
    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate signal with trend filter
        LONG: Bullish cross + Price above trend EMA (recent confirmation)
        SHORT: Bearish cross + Price below trend EMA (recent confirmation)

        WITH SIDEWAYS MARKET PROTECTION:
        - Requires ADX > 30 (strongest trend requirement)
        - Requires volume > 1.2x average
        - Already has EMA50 trend filter (3 of 5 candles)
        - MOST SELECTIVE STRATEGY
        """
        df = self.calculate_indicators(df)

        if len(df) < 55:
            return None

        # SIDEWAYS MARKET PROTECTION
        df = self.calculate_adx(df)

        # Highest ADX threshold (30) - only trade STRONG trends
        if not self.is_trending_market(df, min_adx=30):
            return None  # Skip trade - not strong enough trend

        # Volume confirmation
        if not self.has_volume_confirmation(df, multiplier=1.2):
            return None  # Skip trade - low volume

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # Crossovers
        bullish_cross = (current['ema_fast'] > current['ema_slow'] and
                        previous['ema_fast'] <= previous['ema_slow'])

        bearish_cross = (current['ema_fast'] < current['ema_slow'] and
                        previous['ema_fast'] >= previous['ema_slow'])

        # Trend confirmation: Check last 5 candles (more flexible)
        # Uptrend if at least 3 of last 5 candles are above EMA50
        recent_data = df.tail(5)
        candles_above_trend = (recent_data['close'] > recent_data['ema_trend']).sum()
        candles_below_trend = (recent_data['close'] < recent_data['ema_trend']).sum()

        uptrend = candles_above_trend >= 3
        downtrend = candles_below_trend >= 3

        # Increase confidence with triple confirmation (EMA50 + ADX + Volume)
        confidence = 0.85  # Was 0.75, now 0.85 - highest quality!

        # LONG: Cross + Uptrend
        if bullish_cross and uptrend:
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
                    'ema_trend': current['ema_trend'],
                    'adx': current['adx']
                }
            }

        # SHORT: Cross + Downtrend
        elif bearish_cross and downtrend:
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
                    'ema_trend': current['ema_trend'],
                    'adx': current['adx']
                }
            }

        return None