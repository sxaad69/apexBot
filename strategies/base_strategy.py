"""
Base Strategy Class
Abstract base for all trading strategies
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional, List
import pandas as pd


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies
    All strategies must implement these methods
    """
    
    def __init__(self, config, logger, name: str):
        """
        Initialize strategy
        
        Args:
            config: Configuration object
            logger: Logger instance
            name: Strategy name
        """
        self.config = config
        self.logger = logger
        self.name = name
        
        # Performance tracking
        self.trades = []
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
    
    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Optional[Dict]:
        """
        Generate trading signal from market data
        
        Args:
            df: DataFrame with OHLCV data
        
        Returns:
            Signal dict or None
            {
                'symbol': str,
                'side': 'buy' or 'sell',
                'entry_price': float,
                'stop_loss': float,
                'take_profit': float,
                'position_size': float,
                'confidence': float (0-1)
            }
        """
        pass
    
    @abstractmethod
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Calculate technical indicators
        
        Args:
            df: DataFrame with OHLCV data
        
        Returns:
            DataFrame with added indicator columns
        """
        pass
    
    def record_trade(self, trade: Dict):
        """Record a completed trade"""
        self.trades.append(trade)
        
        if trade.get('pnl', 0) > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        self.total_pnl += trade.get('pnl', 0)
    
    def get_performance(self) -> Dict:
        """Get strategy performance metrics"""
        total_trades = self.wins + self.losses
        win_rate = (self.wins / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'name': self.name,
            'total_trades': total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'avg_pnl_per_trade': self.total_pnl / total_trades if total_trades > 0 else 0
        }
    
    def reset_stats(self):
        """Reset performance statistics"""
        self.trades = []
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0

    def calculate_adx(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        Calculate ADX (Average Directional Index) for trend strength

        ADX Values:
        - < 20: Weak/no trend (sideways market) - DON'T TRADE
        - 20-25: Emerging trend - BE CAUTIOUS
        - 25-50: Strong trend - GOOD TO TRADE
        - > 50: Very strong trend - EXCELLENT

        Args:
            df: DataFrame with high, low, close columns
            period: ADX calculation period (default 14)

        Returns:
            DataFrame with ADX column added
        """
        df = df.copy()

        # Calculate True Range (TR)
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = abs(df['high'] - df['close'].shift(1))
        df['l-pc'] = abs(df['low'] - df['close'].shift(1))
        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)

        # Calculate Directional Movement
        df['h-ph'] = df['high'] - df['high'].shift(1)
        df['pl-l'] = df['low'].shift(1) - df['low']

        df['+dm'] = df.apply(lambda x: x['h-ph'] if x['h-ph'] > x['pl-l'] and x['h-ph'] > 0 else 0, axis=1)
        df['-dm'] = df.apply(lambda x: x['pl-l'] if x['pl-l'] > x['h-ph'] and x['pl-l'] > 0 else 0, axis=1)

        # Smooth with EMA
        df['tr_smooth'] = df['tr'].ewm(span=period, adjust=False).mean()
        df['+dm_smooth'] = df['+dm'].ewm(span=period, adjust=False).mean()
        df['-dm_smooth'] = df['-dm'].ewm(span=period, adjust=False).mean()

        # Calculate +DI and -DI
        df['+di'] = 100 * (df['+dm_smooth'] / df['tr_smooth'])
        df['-di'] = 100 * (df['-dm_smooth'] / df['tr_smooth'])

        # Calculate DX and ADX
        df['dx'] = 100 * abs(df['+di'] - df['-di']) / (df['+di'] + df['-di'])
        df['adx'] = df['dx'].ewm(span=period, adjust=False).mean()

        # Clean up temporary columns
        df.drop(['h-l', 'h-pc', 'l-pc', 'tr', 'h-ph', 'pl-l', '+dm', '-dm',
                'tr_smooth', '+dm_smooth', '-dm_smooth', '+di', '-di', 'dx'], axis=1, inplace=True)

        return df

    def is_trending_market(self, df: pd.DataFrame, min_adx: float = 25) -> bool:
        """
        Check if market is trending (not sideways)

        Args:
            df: DataFrame with ADX calculated
            min_adx: Minimum ADX value for trending (default 25)

        Returns:
            True if trending, False if sideways
        """
        if 'adx' not in df.columns:
            df = self.calculate_adx(df)

        current_adx = df.iloc[-1]['adx']

        # ADX > 25 = strong trend, good to trade
        # ADX < 25 = weak/sideways, avoid trading
        return current_adx >= min_adx

    def has_volume_confirmation(self, df: pd.DataFrame, multiplier: float = 1.2) -> bool:
        """
        Check if current volume confirms the move

        High volume = real move
        Low volume = fake move (often sideways chop)

        Args:
            df: DataFrame with volume column
            multiplier: Current volume must be this multiple of average (default 1.2)

        Returns:
            True if volume is sufficient, False otherwise
        """
        if len(df) < 20:
            return False

        current_volume = df.iloc[-1]['volume']
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]

        # Current volume should be at least 1.2x average
        return current_volume >= (avg_volume * multiplier)

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        Calculate ATR (Average True Range) for dynamic stop loss/take profit

        Args:
            df: DataFrame with high, low, close columns
            period: ATR calculation period (default 14)

        Returns:
            DataFrame with ATR column added
        """
        df = df.copy()

        # Calculate True Range
        df['tr1'] = df['high'] - df['low']
        df['tr2'] = abs(df['high'] - df['close'].shift(1))
        df['tr3'] = abs(df['low'] - df['close'].shift(1))
        df['true_range'] = df[['tr1', 'tr2', 'tr3']].max(axis=1)

        # Calculate ATR using EMA
        df['atr'] = df['true_range'].ewm(span=period, adjust=False).mean()

        # Clean up temporary columns
        df.drop(['tr1', 'tr2', 'tr3', 'true_range'], axis=1, inplace=True)

        return df

    def calculate_macd(self, df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """
        Calculate MACD (Moving Average Convergence Divergence)

        Args:
            df: DataFrame with close column
            fast: Fast EMA period (default 12)
            slow: Slow EMA period (default 26)
            signal: Signal line period (default 9)

        Returns:
            DataFrame with MACD columns added
        """
        df = df.copy()

        df['macd_fast'] = df['close'].ewm(span=fast, adjust=False).mean()
        df['macd_slow'] = df['close'].ewm(span=slow, adjust=False).mean()
        df['macd'] = df['macd_fast'] - df['macd_slow']
        df['macd_signal'] = df['macd'].ewm(span=signal, adjust=False).mean()
        df['macd_histogram'] = df['macd'] - df['macd_signal']

        # Clean up temporary columns
        df.drop(['macd_fast', 'macd_slow'], axis=1, inplace=True)

        return df

    def get_dynamic_stops(self, df: pd.DataFrame, side: str, atr_sl_mult: float = 1.5, atr_tp_mult: float = 3.0) -> tuple:
        """
        Calculate dynamic stop loss and take profit based on ATR

        Args:
            df: DataFrame with ATR calculated
            side: 'buy' or 'sell'
            atr_sl_mult: ATR multiplier for stop loss (default 1.5)
            atr_tp_mult: ATR multiplier for take profit (default 3.0)

        Returns:
            (stop_loss, take_profit) tuple
        """
        if 'atr' not in df.columns:
            df = self.calculate_atr(df)

        current_price = df.iloc[-1]['close']
        current_atr = df.iloc[-1]['atr']

        if side == 'buy':
            stop_loss = current_price - (current_atr * atr_sl_mult)
            take_profit = current_price + (current_atr * atr_tp_mult)
        else:  # sell
            stop_loss = current_price + (current_atr * atr_sl_mult)
            take_profit = current_price - (current_atr * atr_tp_mult)

        return stop_loss, take_profit

    def get_macd_confirmation(self, df: pd.DataFrame, side: str) -> bool:
        """
        Check if MACD confirms the signal direction

        Args:
            df: DataFrame with MACD calculated
            side: 'buy' or 'sell'

        Returns:
            True if MACD confirms, False otherwise
        """
        if 'macd' not in df.columns:
            df = self.calculate_macd(df)

        current = df.iloc[-1]
        previous = df.iloc[-2]

        if side == 'buy':
            # MACD bullish: histogram turning positive or MACD above signal
            return (current['macd_histogram'] > 0 or
                    (current['macd_histogram'] > previous['macd_histogram']))
        else:  # sell
            # MACD bearish: histogram turning negative or MACD below signal
            return (current['macd_histogram'] < 0 or
                    (current['macd_histogram'] < previous['macd_histogram']))
