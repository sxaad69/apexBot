"""
Strategy A2: EMA + RSI (Balanced)
EMA crossover with RSI confirmation
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from .base_strategy import BaseStrategy


class StrategyA2(BaseStrategy):
    """
    EMA + RSI Strategy
    Slightly more selective than A1
    Generates ~30-60 trades per 180 days
    """
    
    def __init__(self, config, logger):
        super().__init__(config, logger, "A2: EMA+RSI")
        
        self.ema_fast = 9
        self.ema_slow = 21
        self.rsi_period = 14
        
        # Relaxed RSI bounds
        self.rsi_oversold = 35
        self.rsi_overbought = 65
        
        self.logger.info(f"Strategy A2 initialized: EMA {self.ema_fast}/{self.ema_slow}, RSI {self.rsi_period}")
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs and RSI"""
        df = df.copy()
        
        # EMAs
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        
        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        
        return df
    
    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate signal with RSI filter
        LONG: Bullish cross + RSI not overbought
        SHORT: Bearish cross + RSI not oversold

        WITH SIDEWAYS MARKET PROTECTION:
        - Requires ADX > 25 (strong trend)
        - Requires volume > 1.2x average
        """
        df = self.calculate_indicators(df)

        if len(df) < 25:
            return None

        # SIDEWAYS MARKET PROTECTION
        df = self.calculate_adx(df)

        # Check if market is trending (not sideways)
        if not self.is_trending_market(df, min_adx=25):
            return None  # Skip trade - sideways market

        # Check volume confirmation
        if not self.has_volume_confirmation(df, multiplier=1.2):
            return None  # Skip trade - low volume (fake move)

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # Crossovers
        bullish_cross = (current['ema_fast'] > current['ema_slow'] and
                        previous['ema_fast'] <= previous['ema_slow'])

        bearish_cross = (current['ema_fast'] < current['ema_slow'] and
                        previous['ema_fast'] >= previous['ema_slow'])

        # Increase confidence since we have trend + volume + RSI confirmation
        confidence = 0.75  # Was 0.65, now 0.75 with ADX/volume filters

        # LONG: Cross + RSI not overbought
        if bullish_cross and current['rsi'] < self.rsi_overbought:
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
                    'rsi': current['rsi'],
                    'adx': current['adx']
                }
            }

        # SHORT: Cross + RSI not oversold
        elif bearish_cross and current['rsi'] > self.rsi_oversold:
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
                    'rsi': current['rsi'],
                    'adx': current['adx']
                }
            }

        return None