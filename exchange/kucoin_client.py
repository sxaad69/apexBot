"""
KuCoin Futures API Client
Handles authentication, request signing, and API communication
"""

import time
import hmac
import hashlib
import base64
import json
from typing import Dict, Any, Optional
from urllib.parse import urlencode


class KuCoinClient:
    """
    KuCoin Futures API Client
    Implements HMAC-SHA256 authentication and API communication
    """
    
    def __init__(self, config, logger):
        """
        Initialize KuCoin client
        
        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        self.api_key = config.KUCOIN_API_KEY
        self.secret_key = config.KUCOIN_SECRET_KEY
        self.passphrase = config.KUCOIN_PASSPHRASE
        self.base_url = config.KUCOIN_BASE_URL
        
        # Encrypted passphrase for KuCoin v2 API
        self.encrypted_passphrase = self._encrypt_passphrase()
        
        self.logger.system(f"KuCoin client initialized: {config.KUCOIN_ENVIRONMENT} environment")
    
    def _encrypt_passphrase(self) -> str:
        """
        Encrypt passphrase using HMAC-SHA256
        Required for KuCoin API v2
        
        Returns:
            Base64 encoded encrypted passphrase
        """
        return base64.b64encode(
            hmac.new(
                self.secret_key.encode('utf-8'),
                self.passphrase.encode('utf-8'),
                hashlib.sha256
            ).digest()
        ).decode('utf-8')
    
    def _generate_signature(self, timestamp: str, method: str, endpoint: str, body: str = '') -> str:
        """
        Generate HMAC-SHA256 signature for KuCoin API request
        
        Args:
            timestamp: Unix timestamp in milliseconds
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint path
            body: Request body (for POST requests)
        
        Returns:
            Base64 encoded signature
        """
        # Create the string to sign
        str_to_sign = timestamp + method + endpoint + body
        
        # Generate signature
        signature = hmac.new(
            self.secret_key.encode('utf-8'),
            str_to_sign.encode('utf-8'),
            hashlib.sha256
        ).digest()
        
        return base64.b64encode(signature).decode('utf-8')
    
    def _get_headers(self, method: str, endpoint: str, body: str = '') -> Dict[str, str]:
        """
        Generate authentication headers for KuCoin API request
        
        Args:
            method: HTTP method
            endpoint: API endpoint
            body: Request body
        
        Returns:
            Dictionary of headers
        """
        timestamp = str(int(time.time() * 1000))
        signature = self._generate_signature(timestamp, method, endpoint, body)
        
        return {
            'KC-API-KEY': self.api_key,
            'KC-API-SIGN': signature,
            'KC-API-TIMESTAMP': timestamp,
            'KC-API-PASSPHRASE': self.encrypted_passphrase,
            'KC-API-KEY-VERSION': '2',
            'Content-Type': 'application/json'
        }
    
    def build_endpoint(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        Build full endpoint URL with query parameters
        
        Args:
            path: API path (e.g., '/api/v1/positions')
            params: Query parameters
        
        Returns:
            Full endpoint string
        """
        endpoint = path
        if params:
            endpoint += '?' + urlencode(params)
        return endpoint
    
    def get_account_overview(self) -> Dict[str, Any]:
        """
        Get account overview including balance and margin info
        
        Returns:
            Account overview data
        """
        endpoint = '/api/v1/account-overview'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Get current ticker data for a symbol
        
        Args:
            symbol: Trading symbol (e.g., 'BTCUSDT')
        
        Returns:
            Ticker data
        """
        endpoint = f'/api/v1/ticker?symbol={symbol}'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def get_orderbook(self, symbol: str, depth: int = 20) -> Dict[str, Any]:
        """
        Get order book data
        
        Args:
            symbol: Trading symbol
            depth: Order book depth (default 20)
        
        Returns:
            Order book data
        """
        endpoint = f'/api/v1/level2/depth{depth}?symbol={symbol}'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def get_positions(self) -> Dict[str, Any]:
        """
        Get all open positions
        
        Returns:
            Positions data
        """
        endpoint = '/api/v1/positions'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def get_position(self, symbol: str) -> Dict[str, Any]:
        """
        Get position for specific symbol
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Position data
        """
        endpoint = f'/api/v1/position?symbol={symbol}'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def place_order(self, symbol: str, side: str, leverage: int, size: int,
                   order_type: str = 'market', price: Optional[float] = None,
                   stop_loss: Optional[float] = None, take_profit: Optional[float] = None) -> Dict[str, Any]:
        """
        Place a futures order
        
        Args:
            symbol: Trading symbol
            side: 'buy' or 'sell'
            leverage: Leverage multiplier
            size: Position size (contracts)
            order_type: 'market' or 'limit'
            price: Limit price (required for limit orders)
            stop_loss: Stop loss price (optional)
            take_profit: Take profit price (optional)
        
        Returns:
            Order placement request data
        """
        endpoint = '/api/v1/orders'
        
        body = {
            'clientOid': f"apex_{int(time.time() * 1000)}",
            'symbol': symbol,
            'side': side,
            'leverage': str(leverage),
            'type': order_type,
            'size': size
        }
        
        if order_type == 'limit' and price:
            body['price'] = str(price)
        
        if stop_loss:
            body['stopLoss'] = str(stop_loss)
        
        if take_profit:
            body['takeProfit'] = str(take_profit)
        
        body_str = json.dumps(body)
        
        return {
            'method': 'POST',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('POST', endpoint, body_str),
            'body': body,
            'endpoint': endpoint
        }
    
    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Cancel an order
        
        Args:
            order_id: Order ID to cancel
        
        Returns:
            Cancellation request data
        """
        endpoint = f'/api/v1/orders/{order_id}'
        return {
            'method': 'DELETE',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('DELETE', endpoint),
            'endpoint': endpoint
        }
    
    def close_position(self, symbol: str) -> Dict[str, Any]:
        """
        Close a position at market price
        
        Args:
            symbol: Symbol to close
        
        Returns:
            Close position request data
        """
        endpoint = '/api/v1/position/close'
        body = {'symbol': symbol}
        body_str = json.dumps(body)
        
        return {
            'method': 'POST',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('POST', endpoint, body_str),
            'body': body,
            'endpoint': endpoint
        }
    
    def get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        Get current and predicted funding rate
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Funding rate data
        """
        endpoint = f'/api/v1/funding-rate/{symbol}/current'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
    
    def get_risk_limit(self, symbol: str) -> Dict[str, Any]:
        """
        Get risk limit for symbol
        
        Args:
            symbol: Trading symbol
        
        Returns:
            Risk limit data
        """
        endpoint = f'/api/v1/contracts/risk-limit/{symbol}'
        return {
            'method': 'GET',
            'url': f"{self.base_url}{endpoint}",
            'headers': self._get_headers('GET', endpoint),
            'endpoint': endpoint
        }
