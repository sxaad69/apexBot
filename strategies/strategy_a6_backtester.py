"""
Strategy A6 Backtester: Statistical Mean Reversion
A simulation-safe equivalent of 'Institutional Edge' designed to operate on OHLCV data.
(Because historical Level-2 orderbook states cannot be downloaded without Enterprise API tiers)
"""

import pandas as pd
from typing import Dict, Optional
import numpy as np

from .base_strategy import BaseStrategy
from .filters import get_strategy_filters

class StrategyA6Backtester(BaseStrategy):
    """
    Statistical Arbitrage / Mean Reversion
    - Detects moments where price mathematically decouples from its VWAP/SMA equilibrium.
    - Represents Institutional flow fading Retail panic/euphoria.
    - Triggers trades on > 2.5 Standard Deviation divergences (Z-Score).
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A6 (Backtest Mode): Statistical Reversion")

        # Core Institutional Metrics
        self.period = 100
        self.z_score_threshold = 2.5 
        
        # ATR multipliers for dynamic stops (Tight stop, reversion target)
        self.atr_sl_mult = 1.0
        self.atr_tp_mult = 3.0

        self.filters = get_strategy_filters(config)
        self.logger.info("Strategy A6 (Backtester Simulation) initialized.")

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate Z-Score divergence against the mean."""
        df = df.copy()
        
        # Calculate moving baseline
        df['sma'] = df['close'].rolling(window=self.period).mean()
        df['std'] = df['close'].rolling(window=self.period).std()
        
        # Calculate Z-Score
        df['z_score'] = (df['close'] - df['sma']) / df['std']
        
        df = self.calculate_atr(df)
        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Fade the retail herd when price mechanically disconnects from fair value.
        """
        if len(df) < self.period:
            return None

        df = self.calculate_indicators(df)
        
        current = df.iloc[-1]
        
        # 1. Base trend filtration (Ensure enough volume exists to mean-revert)
        should_trade, reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            return None

        # 2. Trigger Logic
        side = None
        z_score = current['z_score']
        
        if pd.isna(z_score):
            return None

        if z_score <= -self.z_score_threshold:
            side = 'buy'
            self.logger.debug(f"[{self.name}] {symbol} MASSIVE DIVERGENCE! Z-Score: {z_score:.2f}")
        elif z_score >= self.z_score_threshold:
            side = 'sell'
            self.logger.debug(f"[{self.name}] {symbol} MASSIVE EXHAUSTION! Z-Score: {z_score:.2f}")

        if not side:
            return None

        # Calculate Stops based on ATR
        current_price = current['close']
        atr = current['atr'] if not pd.isna(current['atr']) else current_price * 0.01

        if side == 'buy':
            stop_loss = current_price - (atr * self.atr_sl_mult)
            take_profit = current_price + (atr * self.atr_tp_mult)
        else:
            stop_loss = current_price + (atr * self.atr_sl_mult)
            take_profit = current_price - (atr * self.atr_tp_mult)

        # Max confidence on extreme outliers
        confidence = min(1.0, 0.70 + (abs(z_score) - 2.5) * 0.1)

        return {
            'side': side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'indicators': {'atr': atr}
        }
