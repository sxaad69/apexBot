"""
Strategy A1: EMA Crossover with MACD Confirmation
EMA crossover confirmed by MACD momentum + ATR-based dynamic stops
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA1(BaseStrategy):
    """
    EMA Crossover Strategy with MACD Confirmation
    - EMA 9/21 crossover for entry signals
    - MACD histogram for momentum confirmation
    - ATR-based dynamic stop loss and take profit
    Generates ~40-80 trades per 180 days (higher quality)
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A1: EMA+MACD")

        # EMA settings
        self.ema_fast = 9
        self.ema_slow = 21

        # ATR multipliers for dynamic stops
        self.atr_sl_mult = 1.5  # Stop loss = 1.5x ATR
        self.atr_tp_mult = 3.0  # Take profit = 3x ATR (2:1 R:R)

        # Universal filters for all strategies
        self.filters = get_strategy_filters(config)

        self.logger.info(f"Strategy A1 initialized: EMA {self.ema_fast}/{self.ema_slow} + MACD confirmation")
        self.logger.info(f"Dynamic stops: SL={self.atr_sl_mult}x ATR, TP={self.atr_tp_mult}x ATR")
        self.logger.info(f"Filter settings: {self.filters.get_filter_status()}")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate EMAs, MACD, and ATR"""
        df = df.copy()
        df['ema_fast'] = df['close'].ewm(span=self.ema_fast, adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=self.ema_slow, adjust=False).mean()
        df['ema_major'] = df['close'].ewm(span=200, adjust=False).mean()
        df = self.calculate_macd(df)
        df = self.calculate_atr(df)
        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Generate signal on EMA crossover with MACD confirmation

        Entry Conditions:
        - LONG: Fast EMA crosses above Slow EMA + MACD histogram positive/rising
        - SHORT: Fast EMA crosses below Slow EMA + MACD histogram negative/falling

        Filters:
        - ADX filter for trend strength
        - Volume filter for move confirmation
        - ATR-based dynamic stop loss and take profit
        """
        df = self.calculate_indicators(df)

        if len(df) < 30:
            self.logger.debug(f"Insufficient data: {len(df)} candles < 30 required")
            return self.set_rejection("INSUFFICIENT_DATA")

        # Calculate ADX for filters
        df = self.calculate_adx(df)

        current = df.iloc[-1]
        previous = df.iloc[-2]

        # 1. Universal filters (Volume, etc.)
        should_trade, reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            self.log_strategy_skip(symbol, f"UNIVERSAL_FILTER_{reason.upper()}", {"filter_reason": reason})
            return None

        # 2. Strict ADX Trend Filter (Specialized for A1 to reduce chop losses)
        # Even in TESTING_MODE, we require at least ADX 25 for this crossover strategy
        adx_val = df['adx'].iloc[-1] if 'adx' in df.columns else 0
        if adx_val < 30:
            self.log_strategy_skip(symbol, "ADX_LOW", {"adx": adx_val})
            return None

        # 3. Check for EMA crossover (must happen before trend guard — we need 'side')
        bullish_crossover = current['ema_fast'] > current['ema_slow'] and previous['ema_fast'] <= previous['ema_slow']
        bearish_crossover = current['ema_fast'] < current['ema_slow'] and previous['ema_fast'] >= previous['ema_slow']

        if not (bullish_crossover or bearish_crossover):
            return self.set_rejection("NO_CROSSOVER")

        # Determine signal direction
        side = 'buy' if bullish_crossover else 'sell'

        # 4. Global Trend Guard (Major Trend Filter)
        # Longs only allowed when price > 200 EMA, Shorts only when price < 200 EMA
        if side == 'buy' and current['close'] < current['ema_major']:
            self.log_strategy_skip(symbol, "EMA200_GUARD_LONG", {"price": current['close'], "ema200": current['ema_major']})
            return None
        if side == 'sell' and current['close'] > current['ema_major']:
            self.log_strategy_skip(symbol, "EMA200_GUARD_SHORT", {"price": current['close'], "ema200": current['ema_major']})
            return None

        # MACD confirmation - must agree with crossover direction
        if not self.get_macd_confirmation(df, side):
            self.logger.debug(f"[{self.name}] {symbol}: EMA crossover but no MACD confirmation")
            return self.set_rejection("NO_MACD_CONFIRMATION")

        # Calculate dynamic stops based on ATR
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)

        # Confidence based on MACD strength
        macd_strength = abs(current['macd_histogram']) / current['close'] * 1000  # Normalize
        confidence = min(0.6 + (macd_strength * 0.1), 0.85)  # 0.6 to 0.85

        self.logger.debug(f"[{self.name}] {symbol}: {side.upper()} signal - EMA cross + MACD confirmed")

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
                'macd': current['macd'],
                'macd_signal': current['macd_signal'],
                'macd_histogram': current['macd_histogram'],
                'atr': current['atr'],
                'adx': current['adx']
            }
        }
