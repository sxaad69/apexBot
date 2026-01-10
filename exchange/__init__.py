"""
Exchange Module
Multi-exchange integration using CCXT unified API
"""

from .base_client import BaseExchangeClient
from .ccxt_client import CCXTExchangeClient
from .exchange_manager import ExchangeManager
from .api_manager import APIManager

# Legacy KuCoin client (kept for reference, not actively used)
# from .kucoin_client import KuCoinClient

__all__ = [
    'BaseExchangeClient',
    'CCXTExchangeClient', 
    'ExchangeManager',
    'APIManager'
]
