"""
Base Exchange Client
Abstract base class defining interface for all exchange implementations
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class BaseExchangeClient(ABC):
    """
    Abstract base class for exchange clients
    Defines common interface that all exchange implementations must follow
    """
    
    @abstractmethod
    def __init__(self, config, logger):
        """Initialize exchange client"""
        pass
    
    @abstractmethod
    def get_balance(self) -> Dict[str, Any]:
        """
        Get account balance
        
        Returns:
            Dictionary with balance information
        """
        pass
    
    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Get open positions
        
        Args:
            symbol: Optional symbol filter
        
        Returns:
            List of position dictionaries
        """
        pass
    
    @abstractmethod
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get current ticker/price data
        
        Args:
            symbol: Trading symbol (e.g., 'BTC/USDT')
        
        Returns:
            Ticker data dictionary
        """
        pass
    
    @abstractmethod
    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """
        Get order book
        
        Args:
            symbol: Trading symbol
            limit: Depth limit
        
        Returns:
            Order book data
        """
        pass
    
    @abstractmethod
    def place_order(self, symbol: str, side: str, order_type: str,
                   amount: float, price: Optional[float] = None, **kwargs) -> Dict[str, Any]:
        """
        Place an order
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            order_type: 'market' or 'limit'
            amount: Order amount
            price: Limit price (for limit orders)
            **kwargs: Additional exchange-specific parameters
        
        Returns:
            Order result dictionary
        """
        pass
    
    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """
        Cancel an order
        
        Args:
            order_id: Order ID to cancel
            symbol: Trading symbol
        
        Returns:
            Cancellation result
        """
        pass
    
    @abstractmethod
    def get_markets(self) -> Dict[str, Any]:
        """
        Get all available markets
        
        Returns:
            Markets information
        """
        pass
    
    @abstractmethod
    def get_trading_fees(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """
        Get trading fees
        
        Args:
            symbol: Optional symbol for specific fees
        
        Returns:
            Fee information
        """
        pass
