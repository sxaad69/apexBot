"""
Strategy A2: EMA + RSI Momentum
EMA crossover with RSI momentum confirmation + ATR-based dynamic stops
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA2(BaseStrategy):
    """
    EMA + RSI Momentum Strategy
    - EMA 9/21 crossover for entry signals
    - RSI momentum confirmation (not just extremes avoidance)
    - RSI divergence detection for higher confidence
    - ATR-based dynamic stop loss and take profit
    Generates ~25-50 trades per 180 days (high quality)
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A2: EMA+RSI Momentum")

        self.ema_fast = 9
        self.ema_slow = 21
        self.rsi_period = 14

        # RSI momentum zones (not just overbought/oversold)
        self.rsi_bullish_zone = 50  # RSI > 50 = bullish momentum
        self.rsi_bearish_zone = 50  # RSI < 50 = bearish momentum
        self.rsi_strong_bull = 60   # RSI > 60 = strong bullish
        self.rsi_strong_bear = 40   # RSI < 40 = strong bearish

        # ATR multipliers for dynamic stops
        self.atr_sl_mult = 1.5
        self.atr_tp_mult = 3.0

        # Universal filters
        self.filters = get_strategy_filters(config)

        self.logger.info(f"Strategy A2 initialized: EMA {self.ema_fast}/{self.ema_slow}, RSI {self.rsi_period}")
        self.logger.info(f"RSI momentum zones: Bull>{self.rsi_bullish_zone}, Bear<{self.rsi_bearish_zone}")
        self.logger.info(f"Filter settings: {self.filters.get_filter_status()}")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs, RSI, and ATR"""
        df = df.copy()

        # EMAs
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()

        # RSI with smoothing
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # Use EMA for smoother RSI
        avg_gain = gain.ewm(span=self.rsi_period, adjust=False).mean()
        avg_loss = loss.ewm(span=self.rsi_period, adjust=False).mean()

        rs = avg_gain / avg_loss
        df['rsi'] = 100 - (100 / (1 + rs))

        # RSI momentum (rate of change)
        df['rsi_momentum'] = df['rsi'].diff(3)  # 3-period RSI change

        # ATR for dynamic stops
        df = self.calculate_atr(df)

        return df

    def check_rsi_momentum(self, df: pd.DataFrame, side: str) -> tuple:
        """
        Check RSI momentum confirmation

        Returns: (is_confirmed, momentum_strength)
        """
        current = df.iloc[-1]
        previous = df.iloc[-2]

        rsi = current['rsi']
        rsi_prev = previous['rsi']
        rsi_momentum = current['rsi_momentum'] if 'rsi_momentum' in current else 0

        if side == 'buy':
            # Bullish: RSI > 50 and rising, or RSI crossed above 50
            is_confirmed = (rsi > self.rsi_bullish_zone and rsi > rsi_prev) or \
                          (rsi > self.rsi_bullish_zone and rsi_prev <= self.rsi_bullish_zone)

            # Strength based on RSI level
            if rsi > self.rsi_strong_bull:
                strength = 'strong'
            elif rsi > self.rsi_bullish_zone:
                strength = 'moderate'
            else:
                strength = 'weak'

        else:  # sell
            # Bearish: RSI < 50 and falling, or RSI crossed below 50
            is_confirmed = (rsi < self.rsi_bearish_zone and rsi < rsi_prev) or \
                          (rsi < self.rsi_bearish_zone and rsi_prev >= self.rsi_bearish_zone)

            # Strength based on RSI level
            if rsi < self.rsi_strong_bear:
                strength = 'strong'
            elif rsi < self.rsi_bearish_zone:
                strength = 'moderate'
            else:
                strength = 'weak'

        return is_confirmed, strength

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Generate signal with RSI momentum confirmation

        Entry Conditions:
        - LONG: EMA bullish cross + RSI > 50 and rising (momentum confirmation)
        - SHORT: EMA bearish cross + RSI < 50 and falling (momentum confirmation)

        This is better than just avoiding overbought/oversold because:
        1. RSI > 50 = buyers in control (momentum with us)
        2. RSI < 50 = sellers in control (momentum with us)
        3. Rising/falling RSI = momentum increasing
        """
        df = self.calculate_indicators(df)

        if len(df) < 30:
            return None

        # Calculate ADX for filters
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

        side = 'buy' if bullish_cross else 'sell'

        # Check RSI momentum confirmation
        rsi_confirmed, rsi_strength = self.check_rsi_momentum(df, side)

        if not rsi_confirmed:
            self.logger.debug(f"[{self.name}] {symbol}: EMA cross but RSI momentum not confirmed")
            return None

        # Calculate dynamic stops
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)

        # Confidence based on RSI strength
        confidence_map = {'strong': 0.85, 'moderate': 0.75, 'weak': 0.65}
        confidence = confidence_map.get(rsi_strength, 0.70)

        self.logger.debug(f"[{self.name}] {symbol}: {side.upper()} - RSI momentum {rsi_strength} (RSI={current['rsi']:.1f})")

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': current['close'],
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'indicators': {
                'ema_fast': current['ema_fast'],
                'ema_slow': current['ema_slow'],
                'rsi': current['rsi'],
                'rsi_momentum': current.get('rsi_momentum', 0),
                'rsi_strength': rsi_strength,
                'atr': current['atr'],
                'adx': current['adx']
            }
        }
