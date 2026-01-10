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
