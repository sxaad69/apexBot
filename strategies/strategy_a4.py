"""
Strategy A4: Multi-Timeframe Trend Following
Triple EMA alignment with ADX trend strength + MACD momentum
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA4(BaseStrategy):
    """
    Multi-Timeframe Trend Following Strategy
    - Triple EMA alignment (9/21/50) for trend direction
    - ADX > 25 for trend strength confirmation
    - MACD for momentum confirmation
    - Wider ATR stops for trend riding
    Generates ~20-40 trades per 180 days (highest quality)
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A4: Trend Following")

        # Triple EMA for trend alignment
        self.ema_fast = 9
        self.ema_slow = 21
        self.ema_trend = 50
        self.ema_major = 200  # Major trend filter

        # Trend confirmation requirements
        self.min_trend_candles = 3  # 3 of last 5 candles must confirm trend
        self.lookback_candles = 5

        # Wider stops for trend riding (let winners run)
        self.atr_sl_mult = 2.0   # Wider stop for trend trades
        self.atr_tp_mult = 4.0   # 2:1 R:R with room to run

        # Universal filters
        self.filters = get_strategy_filters(config)

        self.logger.info(f"Strategy A4 initialized: EMA {self.ema_fast}/{self.ema_slow}/{self.ema_trend}/{self.ema_major}")
        self.logger.info(f"Trend stops: SL={self.atr_sl_mult}x ATR, TP={self.atr_tp_mult}x ATR")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs, MACD, and ATR"""
        df = df.copy()

        # Multiple EMAs for trend alignment
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        df['ema_trend'] = df['close'].ewm(span=self.ema_trend, adjust=False).mean()
        df['ema_major'] = df['close'].ewm(span=self.ema_major, adjust=False).mean()

        # EMA alignment score (how aligned are the EMAs)
        df['ema_bullish_aligned'] = (
            (df['ema_fast'] > df['ema_slow']) &
            (df['ema_slow'] > df['ema_trend']) &
            (df['close'] > df['ema_trend'])
        )
        df['ema_bearish_aligned'] = (
            (df['ema_fast'] < df['ema_slow']) &
            (df['ema_slow'] < df['ema_trend']) &
            (df['close'] < df['ema_trend'])
        )

        # MACD for momentum
        df = self.calculate_macd(df)

        # ATR for stops
        df = self.calculate_atr(df)

        return df

    def check_trend_alignment(self, df: pd.DataFrame) -> tuple:
        """
        Check if EMAs are properly aligned for trend trading

        Returns: (is_aligned, direction, strength)
        """
        current = df.iloc[-1]
        recent = df.tail(self.lookback_candles)

        # Count aligned candles
        bullish_count = recent['ema_bullish_aligned'].sum()
        bearish_count = recent['ema_bearish_aligned'].sum()

        # Check major trend (200 EMA)
        above_major = current['close'] > current['ema_major']
        below_major = current['close'] < current['ema_major']

        # Determine alignment
        if bullish_count >= self.min_trend_candles:
            # Extra confidence if also above 200 EMA
            strength = 'strong' if above_major else 'moderate'
            return True, 'buy', strength

        elif bearish_count >= self.min_trend_candles:
            # Extra confidence if also below 200 EMA
            strength = 'strong' if below_major else 'moderate'
            return True, 'sell', strength

        return False, None, None

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Generate high-quality trend following signals

        Entry Conditions (ALL required):
        1. EMA crossover (9 crosses 21)
        2. EMAs aligned (9 > 21 > 50 for longs, opposite for shorts)
        3. MACD momentum confirmation
        4. ADX > 25 (strong trend)

        This is the most selective strategy - quality over quantity.
        """
        df = self.calculate_indicators(df)

        if len(df) < 210:  # Need 200+ for major EMA
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

        # Check for EMA crossover
        bullish_cross = (current['ema_fast'] > current['ema_slow'] and
                        previous['ema_fast'] <= previous['ema_slow'])
        bearish_cross = (current['ema_fast'] < current['ema_slow'] and
                        previous['ema_fast'] >= previous['ema_slow'])

        if not (bullish_cross or bearish_cross):
            return None

        cross_direction = 'buy' if bullish_cross else 'sell'

        # Check trend alignment
        is_aligned, trend_direction, trend_strength = self.check_trend_alignment(df)

        if not is_aligned:
            self.logger.debug(f"[{self.name}] {symbol}: Crossover but EMAs not aligned")
            return None

        if cross_direction != trend_direction:
            self.logger.debug(f"[{self.name}] {symbol}: Cross direction conflicts with trend")
            return None

        # MACD confirmation
        if not self.get_macd_confirmation(df, cross_direction):
            self.logger.debug(f"[{self.name}] {symbol}: No MACD confirmation")
            return None

        # Calculate wider stops for trend trading
        stop_loss, take_profit = self.get_dynamic_stops(df, cross_direction, self.atr_sl_mult, self.atr_tp_mult)

        # High confidence for fully aligned trend trades
        confidence = 0.90 if trend_strength == 'strong' else 0.80

        self.logger.debug(f"[{self.name}] {symbol}: {cross_direction.upper()} - {trend_strength} trend alignment")

        return {
            'symbol': symbol,
            'side': cross_direction,
            'entry_price': current['close'],
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'indicators': {
                'ema_fast': current['ema_fast'],
                'ema_slow': current['ema_slow'],
                'ema_trend': current['ema_trend'],
                'ema_major': current['ema_major'],
                'trend_strength': trend_strength,
                'macd': current['macd'],
                'macd_histogram': current['macd_histogram'],
                'atr': current['atr'],
                'adx': current['adx']
            }
        }
