"""
Strategy A3: Momentum Scalping
Fast EMA scalping with Bollinger Band squeeze detection + volume spike confirmation
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA3(BaseStrategy):
    """
    Momentum Scalping Strategy
    - Fast EMA 5/13 for quick entries
    - Bollinger Band squeeze detection (volatility breakout)
    - Volume spike confirmation (1.5x average)
    - Tighter ATR-based stops for scalping
    Generates ~60-100 trades per 180 days (filtered quality)
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A3: Momentum Scalp")

        # Fast EMAs for scalping
        self.ema_fast = 5
        self.ema_slow = 13

        # Bollinger Band settings
        self.bb_period = 20
        self.bb_std = 2.0

        # Volume spike threshold
        self.volume_spike_mult = 1.5

        # Tighter stops for scalping (1:2 R:R)
        self.atr_sl_mult = 1.0   # Tight stop
        self.atr_tp_mult = 2.0   # 2:1 reward:risk

        # Universal filters
        self.filters = get_strategy_filters(config)

        self.logger.info(f"Strategy A3 initialized: Fast EMA {self.ema_fast}/{self.ema_slow}")
        self.logger.info(f"Bollinger Bands: {self.bb_period} period, {self.bb_std} std")
        self.logger.info(f"Scalp stops: SL={self.atr_sl_mult}x ATR, TP={self.atr_tp_mult}x ATR")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate fast EMAs, Bollinger Bands, and ATR"""
        df = df.copy()

        # Fast EMAs
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()

        # Bollinger Bands
        df['bb_middle'] = df['close'].rolling(self.bb_period).mean()
        df['bb_std'] = df['close'].rolling(self.bb_period).std()
        df['bb_upper'] = df['bb_middle'] + (df['bb_std'] * self.bb_std)
        df['bb_lower'] = df['bb_middle'] - (df['bb_std'] * self.bb_std)

        # Bollinger Band Width (squeeze indicator)
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_middle'] * 100

        # Average BB width for squeeze detection
        df['bb_width_avg'] = df['bb_width'].rolling(20).mean()

        # Volume analysis
        df['volume_avg'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_avg']

        # ATR for stops
        df = self.calculate_atr(df)

        return df

    def detect_squeeze_breakout(self, df: pd.DataFrame) -> tuple:
        """
        Detect Bollinger Band squeeze and breakout

        Squeeze = Low volatility (BB width below average)
        Breakout = Price breaking out of squeeze with volume

        Returns: (is_squeeze_breakout, direction)
        """
        if len(df) < 25:
            return False, None

        current = df.iloc[-1]
        prev_5 = df.iloc[-6:-1]  # Previous 5 candles

        # Check if we were in a squeeze (low volatility)
        was_squeezed = (prev_5['bb_width'] < prev_5['bb_width_avg']).any()

        # Current width expanding (breakout starting)
        width_expanding = current['bb_width'] > df.iloc[-2]['bb_width']

        # Price breaking out
        breaking_upper = current['close'] > current['bb_upper']
        breaking_lower = current['close'] < current['bb_lower']

        # Volume confirmation
        volume_spike = current['volume_ratio'] >= self.volume_spike_mult

        if was_squeezed and width_expanding and volume_spike:
            if breaking_upper:
                return True, 'buy'
            elif breaking_lower:
                return True, 'sell'

        return False, None

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Generate scalping signals with multiple confirmations

        Entry Conditions (need 2 of 3):
        1. Fast EMA crossover
        2. Bollinger Band squeeze breakout
        3. Volume spike (1.5x average)

        This reduces false signals in choppy markets.
        """
        df = self.calculate_indicators(df)

        if len(df) < 25:
            return None

        # Calculate ADX
        df = self.calculate_adx(df)

        # Check universal filters
        should_trade, reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            self.logger.debug(f"[{self.name}] {symbol} FILTERED: {reason}")
            return None

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # Signal confirmations
        confirmations = 0
        signal_direction = None

        # 1. EMA crossover
        bullish_cross = current['ema_fast'] > current['ema_slow'] and previous['ema_fast'] <= previous['ema_slow']
        bearish_cross = current['ema_fast'] < current['ema_slow'] and previous['ema_fast'] >= previous['ema_slow']

        if bullish_cross:
            confirmations += 1
            signal_direction = 'buy'
        elif bearish_cross:
            confirmations += 1
            signal_direction = 'sell'

        # 2. BB squeeze breakout
        is_breakout, breakout_dir = self.detect_squeeze_breakout(df)
        if is_breakout:
            confirmations += 1
            if signal_direction is None:
                signal_direction = breakout_dir
            elif signal_direction != breakout_dir:
                # Conflicting signals - skip
                return None

        # 3. Volume spike
        if current['volume_ratio'] >= self.volume_spike_mult:
            confirmations += 1

        # Need at least 2 confirmations
        if confirmations < 2 or signal_direction is None:
            return None

        # Calculate tight scalping stops
        stop_loss, take_profit = self.get_dynamic_stops(df, signal_direction, self.atr_sl_mult, self.atr_tp_mult)

        # Confidence based on confirmations
        confidence = 0.5 + (confirmations * 0.15)  # 0.65 to 0.95

        self.logger.debug(f"[{self.name}] {symbol}: {signal_direction.upper()} - {confirmations} confirmations")

        return {
            'symbol': symbol,
            'side': signal_direction,
            'entry_price': current['close'],
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'indicators': {
                'ema_fast': current['ema_fast'],
                'ema_slow': current['ema_slow'],
                'bb_width': current['bb_width'],
                'bb_width_avg': current['bb_width_avg'],
                'volume_ratio': current['volume_ratio'],
                'confirmations': confirmations,
                'atr': current['atr'],
                'adx': current['adx']
            }
        }
