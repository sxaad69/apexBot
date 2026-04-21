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
            # Get exchange class (Map 'kucoin' to 'kucoinfutures' for proper API support)
            effective_id = self.exchange_id
            if effective_id == 'kucoin':
                effective_id = 'kucoinfutures'
            
            exchange_class = getattr(ccxt, effective_id)
            
            # Base configuration
            exchange_config = {
                **self.credentials,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',  # Use futures by default
                    'warnOnFetchOpenOrdersWithoutSymbol': False,
                }
            }
            
            # Set testnet/sandbox if configured
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet':
                exchange_config['sandbox'] = True  # Sandbox for others and Binance using standard CCXT init
            
            # Initialize exchange
            exchange = exchange_class(exchange_config)
            
            # Binance explicit demo trading enable (handles different ccxt versions safely)
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet' and self.exchange_id == 'binance':
                try:
                    # Native general CCXT method
                    exchange.set_sandbox_mode(True)
                except AttributeError:
                    try:
                        exchange.enable_demo_trading(True)
                    except AttributeError:
                        self.logger.warning("Could not set Binance sandbox mode dynamically. Ensure ccxt is up to date.")
                
                # Binance Testnet API keys are futures-only, fetching SPOT currencies crashes it
                exchange.options['fetchCurrencies'] = False
            
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
            active_positions = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
            
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
    
    def set_margin_mode(self, symbol: str, margin_mode: str = 'ISOLATED'):
        """
        Set margin mode for a symbol (e.g., 'ISOLATED', 'CROSS')
        Binance specific implementation via CCXT
        """
        try:
            # Normalize mode to uppercase for Binance
            mode = margin_mode.upper()
            
            # Use CCXT unified method if available
            if hasattr(self.exchange, 'set_margin_mode'):
                self.exchange.set_margin_mode(mode, symbol)
                self.logger.info(f"Set margin mode to {mode} for {symbol}")
            else:
                self.logger.debug(f"Exchange {self.exchange_id} does not support set_margin_mode via CCXT")
        except Exception as e:
            # Binance throws error if already set to that mode - we ignore and log as debug
            if "No need to change margin type" in str(e):
                self.logger.debug(f"Margin mode for {symbol} already set to {margin_mode}")
            else:
                self.logger.warning(f"Could not set margin mode for {symbol}: {e}")

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
            
            # self.logger.trade_entry(...) - REMOVED (Redundant with TradeManager)
            
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
            
            # Clean up any lingering Stop Loss / Take profit orders (Important!)
            try:
                self.exchange.cancel_all_orders(symbol)
                self.logger.info(f"Cleared lingering physical orders for {symbol}")
            except Exception as clean_e:
                self.logger.warning(f"Failed to clean up orders after closing {symbol}: {clean_e}")
            
            self.logger.info(f"Closed position for {symbol}")
            return result
        except Exception as e:
            self.logger.error(f"Error closing position: {e}", exc_info=True)
            return {}
