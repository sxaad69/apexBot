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
        Trend-Continuation Signal (Redesigned from strict crossover-only).

        Entry Conditions:
        - LONG: EMA 9 > 21 > 50, price > EMA-200, MACD bullish, ADX > 25
        - SHORT: EMA 9 < 21 < 50, price < EMA-200, MACD bearish, ADX > 30
        
        This fires on trend-continuation, not just the moment of crossover,
        dramatically increasing trade frequency while maintaining quality.
        """
        df = self.calculate_indicators(df)

        if len(df) < 210:  # Need 200+ for major EMA
            return self.set_rejection("INSUFFICIENT_DATA")

        # Calculate ADX
        df = self.calculate_adx(df)

        # Check universal filters
        should_trade, reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            self.log_strategy_skip(symbol, f"UNIVERSAL_FILTER_{reason.upper()}", {"filter_reason": reason})
            return None

        current = df.iloc[-1]
        adx_val = current['adx'] if 'adx' in df.columns else 0

        # Determine current alignment direction
        bullish_aligned = (
            current['ema_fast'] > current['ema_slow'] and
            current['ema_slow'] > current['ema_trend'] and
            current['close'] > current['ema_trend']
        )
        bearish_aligned = (
            current['ema_fast'] < current['ema_slow'] and
            current['ema_slow'] < current['ema_trend'] and
            current['close'] < current['ema_trend']
        )

        if not (bullish_aligned or bearish_aligned):
            return self.set_rejection("NO_EMA_ALIGNMENT")

        # Determine direction and apply directional ADX/strength filters
        if bullish_aligned:
            side = 'buy'
            # Longs: ADX > 25, price > EMA-200, moderate trend ok
            if adx_val < 25:
                self.log_strategy_skip(symbol, "ADX_LOW", {"adx": round(adx_val, 2), "required": 25})
                return None
            if current['close'] < current['ema_major']:
                self.log_strategy_skip(symbol, "EMA200_GUARD_LONG", {})
                return None
        else:
            side = 'sell'
            # Shorts: ADX > 30, price < EMA-200, strong trend only
            if adx_val < 30:
                self.log_strategy_skip(symbol, "ADX_LOW_SHORT", {"adx": round(adx_val, 2), "required": 30})
                return None
            if current['close'] > current['ema_major']:
                self.log_strategy_skip(symbol, "EMA200_GUARD_SHORT", {})
                return None

        # MACD confirmation
        if not self.get_macd_confirmation(df, side):
            return self.set_rejection("NO_MACD_CONFIRMATION")

        # Calculate wider stops for trend trading
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)

        # Dynamic confidence based on ADX strength
        if adx_val >= 50:
            confidence = 0.90
        elif adx_val >= 40:
            confidence = 0.80
        else:
            confidence = 0.72

        # Bonus for strong alignment above 200 EMA
        above_major = current['close'] > current['ema_major']
        below_major = current['close'] < current['ema_major']
        if (side == 'buy' and above_major) or (side == 'sell' and below_major):
            confidence = min(confidence + 0.05, 0.95)

        self.logger.debug(f"[{self.name}] {symbol}: {side.upper()} — ADX {adx_val:.1f} → Confidence {confidence:.2f}")

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
                'ema_trend': current['ema_trend'],
                'ema_major': current['ema_major'],
                'macd': current['macd'],
                'macd_histogram': current['macd_histogram'],
                'atr': current['atr'],
                'adx': adx_val
            }
        }
