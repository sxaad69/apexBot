"""
CCXT Exchange Client
Unified exchange client using CCXT library for multi-exchange support
"""

import ccxt
from typing import Dict, Any, List, Optional
from .base_client import BaseExchangeClient


class CCXTExchangeClient(BaseExchangeClient):
    """
    Unified exchange client using CCXT
    Supports 100+ exchanges with a common interface
    """
    
    def __init__(self, config, logger, exchange_id: Optional[str] = None):
        """
        Initialize CCXT exchange client
        
        Args:
            config: Configuration object
            logger: Logger instance
            exchange_id: Exchange ID (e.g., 'binance', 'kucoin')
                        If None, uses config.EXCHANGE
        """
        self.config = config
        self.logger = logger
        self.exchange_id = exchange_id or config.EXCHANGE.lower()
        
        # Get API credentials for this exchange
        self.credentials = self._get_credentials()
        
        # Initialize CCXT exchange
        self.exchange = self._initialize_exchange()
        
        self.logger.system(
            f"CCXT client initialized: {self.exchange_id} "
            f"({config.EXCHANGE_ENVIRONMENT})"
        )
    
    def _get_credentials(self) -> Dict[str, str]:
        """Get API credentials for the exchange"""
        exchange_upper = self.exchange_id.upper()
        
        credentials = {
            'apiKey': getattr(self.config, f'{exchange_upper}_API_KEY', ''),
            'secret': getattr(self.config, f'{exchange_upper}_API_SECRET', ''),
        }
        
        # Some exchanges need passphrase
        if self.exchange_id in ['kucoin', 'okx']:
            passphrase = getattr(self.config, f'{exchange_upper}_API_PASSPHRASE', '')
            if passphrase:
                credentials['password'] = passphrase
        
        return credentials
    
    def _initialize_exchange(self):
        """Initialize the CCXT exchange instance"""
        try:
            # Get exchange class
            exchange_class = getattr(ccxt, self.exchange_id)
            
            # Base configuration
            exchange_config = {
                **self.credentials,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',  # Use futures by default
                }
            }
            
            # Set testnet/sandbox if configured
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet':
                exchange_config['sandbox'] = True
            
            # Initialize exchange
            exchange = exchange_class(exchange_config)
            
            # Load markets
            exchange.load_markets()
            
            return exchange
            
        except AttributeError:
            self.logger.error(f"Exchange '{self.exchange_id}' not supported by CCXT")
            raise ValueError(f"Unsupported exchange: {self.exchange_id}")
        except Exception as e:
            self.logger.error(f"Failed to initialize {self.exchange_id}: {e}", exc_info=True)
            raise
    
    def get_balance(self) -> Dict[str, Any]:
        """Get account balance"""
        try:
            balance = self.exchange.fetch_balance()
            self.logger.debug(f"Fetched balance from {self.exchange_id}")
            return balance
        except Exception as e:
            self.logger.error(f"Error fetching balance: {e}", exc_info=True)
            return {}
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open positions"""
        try:
            if symbol:
                positions = self.exchange.fetch_positions([symbol])
            else:
                positions = self.exchange.fetch_positions()
            
            # Filter out empty positions
            active_positions = [p for p in positions if float(p.get('contracts', 0)) > 0]
            
            self.logger.debug(f"Fetched {len(active_positions)} positions from {self.exchange_id}")
            return active_positions
        except Exception as e:
            self.logger.error(f"Error fetching positions: {e}", exc_info=True)
            return []
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get current ticker data"""
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            return ticker
        except Exception as e:
            self.logger.error(f"Error fetching ticker for {symbol}: {e}")
            return {}
    
    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Get order book"""
        try:
            orderbook = self.exchange.fetch_order_book(symbol, limit)
            return orderbook
        except Exception as e:
            self.logger.error(f"Error fetching orderbook for {symbol}: {e}")
            return {'bids': [], 'asks': []}
    
    def place_order(self, symbol: str, side: str, order_type: str,
                   amount: float, price: Optional[float] = None, **kwargs) -> Dict[str, Any]:
        """Place an order"""
        try:
            # CCXT unified order placement
            if order_type == 'market':
                order = self.exchange.create_market_order(symbol, side, amount, kwargs)
            elif order_type == 'limit':
                if price is None:
                    raise ValueError("Price required for limit orders")
                order = self.exchange.create_limit_order(symbol, side, amount, price, kwargs)
            else:
                raise ValueError(f"Unsupported order type: {order_type}")
            
            self.logger.trade_entry(
                symbol=symbol,
                side=side,
                size=amount,
                price=price or 0,
                leverage=kwargs.get('leverage', 1)
            )
            
            return order
        except Exception as e:
            self.logger.error(f"Error placing order: {e}", exc_info=True)
            return {}
    
    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Cancelled order {order_id} for {symbol}")
            return result
        except Exception as e:
            self.logger.error(f"Error cancelling order: {e}", exc_info=True)
            return {}
    
    def get_markets(self) -> Dict[str, Any]:
        """Get all available markets"""
        try:
            return self.exchange.markets
        except Exception as e:
            self.logger.error(f"Error fetching markets: {e}")
            return {}
    
    def get_trading_fees(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get trading fees"""
        try:
            if symbol:
                fees = self.exchange.fetch_trading_fee(symbol)
            else:
                fees = self.exchange.fetch_trading_fees()
            return fees
        except Exception as e:
            self.logger.error(f"Error fetching fees: {e}")
            return {}
    
    def close_position(self, symbol: str) -> Dict[str, Any]:
        """Close a position (futures-specific)"""
        try:
            # Get current position
            positions = self.get_positions(symbol)
            if not positions:
                self.logger.warning(f"No open position for {symbol}")
                return {}
            
            position = positions[0]
            side = 'sell' if position.get('side') == 'long' else 'buy'
            amount = abs(float(position.get('contracts', 0)))
            
            # Close with market order
            result = self.place_order(symbol, side, 'market', amount, reduceOnly=True)
            
            self.logger.info(f"Closed position for {symbol}")
            return result
        except Exception as e:
            self.logger.error(f"Error closing position: {e}", exc_info=True)
            return {}
