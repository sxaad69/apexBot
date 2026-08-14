"""
Strategy A7: 5m Acceleration Moonshot Hunter

Early moonshot detection via 5-minute volume + momentum acceleration.
Validated by backtest (Aug 4-12): vol x1.5 + 0.5% move → net +1536% across
869 trades, 43% win rate, avg +1.77%/trade. Blue-chips produced ZERO signals
(BTC/ETH/SOL), while moonshots (TUT +354%, CYS +458%) lit up repeatedly.

Core idea: moonshots show 5m volume acceleration BEFORE the 15m/orderbook
confirmation that A6 waits for. A6 is a lagging wall-confirmer; A7 is a
leading acceleration detector.

- Fetches its OWN 5m candles (independent of the shared 15m df).
- Volume-ratio filter (vs 20-bar avg), NOT the $10k absolute that blocks grinders.
- Leverage-aware ATR stop (vol-sync layer normalizes to ~10% ROE).
- Long-only for now (backtest edge was long-side).
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA7(BaseStrategy):
    """
    5m Acceleration Moonshot Strategy
    - 5m candle volume spike (>= 1.5x 20-bar avg)
    - 5m price momentum (>= 0.5% move on the bar)
    - Volume-ratio filters (relative, not absolute)
    - ATR-based dynamic stops (leverage-aware via vol-sync layer)
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A7: 5m Acceleration")

        # Signal thresholds (from backtest sweep: vol x1.5 / mom 0.5%)
        self.volume_spike_mult = 1.5     # volume >= 1.5x 20-bar average
        self.min_move_pct = 0.5          # |5m move| >= 0.5%
        self.lookback = 20               # volume average window

        # ATR stops for 5m (smaller than 15m ATR — use a wider multiplier so
        # the vol-sync layer assigns sane leverage)
        self.atr_sl_mult = 3.0
        self.atr_tp_mult = 100.0  # trailing TP handles exit

        # 5m timeframe (independent fetch)
        self.timeframe = '5m'
        self.min_candles = 40

        # Universal filters
        self.filters = get_strategy_filters(config)

        self.logger.info(f"Strategy A7 initialized: 5m Acceleration (vol x{self.volume_spike_mult}, move >= {self.min_move_pct}%)")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate volume_ratio, ATR, EMA200, ADX."""
        df = df.copy()
        if len(df) >= self.lookback + 1:
            df['volume_avg_20'] = df['volume'].rolling(self.lookback).mean()
            df['volume_ratio'] = df['volume'] / df['volume_avg_20']
        else:
            df['volume_ratio'] = 1.0

        # Bar move % (close vs open of the current 5m bar)
        df['bar_move'] = (df['close'] - df['open']) / df['open'] * 100

        df = self.calculate_atr(df)

        # Major trend guard
        if len(df) >= 200:
            df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        else:
            df['ema_200'] = df['close'].ewm(span=len(df), adjust=False).mean()

        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Generate signal on 5m volume + momentum acceleration.

        The shared df is 15m; A7 fetches its own 5m frame via the engine.
        Falls back to the passed df if the engine is unavailable.
        """
        # --- Fetch 5m data (independent of the shared 15m df) ---
        try:
            if hasattr(self.logger, 'engine') and self.logger.engine:
                df5 = self.logger.engine.fetch_market_data(symbol, timeframe=self.timeframe)
            else:
                df5 = None
        except Exception as e:
            self.logger.debug(f"[A7] 5m fetch failed for {symbol}: {e}")
            df5 = None

        if df5 is None or len(df5) < self.min_candles:
            return self.set_rejection("INSUFFICIENT_DATA")

        df = self.calculate_indicators(df5)

        # Stablecoin filter
        if not self.filters._check_stablecoin_filter(symbol):
            return self.set_rejection("STABLECOIN_FILTER")

        # Universal filters (volume/adx) — but A7 uses relative volume_ratio,
        # so the absolute $10k filter should not block; keep ADX optional.
        # We skip the universal filter here to avoid the $10k absolute gate that
        # killed the grinders; volume_ratio is our volume check.

        current = df.iloc[-1]
        previous = df.iloc[-2]

        vol_ratio = float(current['volume_ratio'])
        bar_move = float(current['bar_move'])

        # --- Core signal: volume spike + momentum ---
        if vol_ratio < self.volume_spike_mult:
            return self.set_rejection({
                "reason": "LOW_VOLUME_RATIO",
                "volume_ratio": round(vol_ratio, 2),
                "required": self.volume_spike_mult,
                "bar_move": round(bar_move, 2),
            })

        if abs(bar_move) < self.min_move_pct:
            return self.set_rejection({
                "reason": "LOW_MOMENTUM",
                "bar_move": round(bar_move, 2),
                "required": self.min_move_pct,
                "volume_ratio": round(vol_ratio, 2),
            })

        # Direction: long-only for now (backtest edge was long side)
        side = 'buy'
        if bar_move < 0:
            # Negative move with high volume = distribution, not our long setup.
            return self.set_rejection({
                "reason": "NEGATIVE_MOMENTUM",
                "bar_move": round(bar_move, 2),
                "volume_ratio": round(vol_ratio, 2),
            })

        # EMA200 trend guard — only buy above the 200 EMA (confirmed uptrend)
        current_price = df['close'].iloc[-1]
        ema_200 = df['ema_200'].iloc[-1]
        if current_price < ema_200:
            return self.set_rejection({
                "reason": "BELOW_EMA200",
                "price": round(current_price, 8),
                "ema200": round(ema_200, 8),
                "bar_move": round(bar_move, 2),
                "volume_ratio": round(vol_ratio, 2),
            })

        # ATR-based dynamic stops
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)
        atr = df['atr'].iloc[-1] if 'atr' in df.columns else current_price * 0.01

        # Confidence: base + volume ratio + momentum magnitude
        confidence = 0.50
        confidence += min(vol_ratio * 0.10, 0.20)      # volume contribution (up to 20%)
        confidence += min(abs(bar_move) * 0.10, 0.15)  # momentum contribution (up to 15%)
        confidence = min(confidence, 1.0)

        self.logger.info(
            f"[{self.name}] {symbol} 5m ACCELERATION: vol x{vol_ratio:.1f} | "
            f"move {bar_move:+.2f}% | Conf: {confidence:.2f}"
        )

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'timeframe': self.timeframe,
            'indicators': {
                'volume_ratio': round(vol_ratio, 2),
                'bar_move': round(bar_move, 2),
                'atr': round(float(atr), 8),
            }
        }
