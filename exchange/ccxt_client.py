"""
CCXT Exchange Client
Unified exchange client using CCXT library for multi-exchange support
"""

import ccxt
from typing import Dict, Any, List, Optional
from .base_client import BaseExchangeClient
from .rate_limiter import RateLimiter


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

        # --- RATE-LIMIT FIX (Task 1.2): Global token-bucket safety net ---
        # Caps total REST weight/min regardless of code path. Binance futures
        # limit is 2,400 weight/min; we budget 1,500 for headroom.
        max_weight = getattr(config, 'RATE_LIMIT_MAX_WEIGHT_PER_MIN', 1500)
        self.rate_limiter = RateLimiter(max_weight_per_min=max_weight, logger=logger)
        
        self.logger.system(
            f"CCXT client initialized: {self.exchange_id} "
            f"({config.EXCHANGE_ENVIRONMENT}) | Rate limiter: {max_weight}/min"
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
            # NOTE: For Binance, sandbox mode is deprecated for futures (ccxt >= 4.5).
            #       We use demo trading instead via enable_demo_trading().
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet' and self.exchange_id != 'binance':
                exchange_config['sandbox'] = True  # Sandbox for non-Binance exchanges (e.g. KuCoin)
            
            # Initialize exchange
            exchange = exchange_class(exchange_config)
            
            # Binance explicit demo trading enable (handles different ccxt versions safely)
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet' and self.exchange_id == 'binance':
                try:
                    # Native general CCXT method - switches to demo-fapi.binance.com endpoints
                    exchange.enable_demo_trading(True)
                except AttributeError:
                    try:
                        exchange.set_sandbox_mode(True)
                    except AttributeError:
                        self.logger.warning("Could not set Binance demo trading mode dynamically. Ensure ccxt is up to date.")
                
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
        """Get account balance (rate-limited + TTL-cached 60s).

        RATE-LIMIT FIX (Task 1.4): Called every sweep (~45s) in live mode.
        Balance doesn't change that fast, so cache for 60s to cut REST calls.
        """
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'BALANCE_CACHE_TTL', 60.0)
        if (now - getattr(self, '_balance_cache_ts', 0)) < ttl and getattr(self, '_balance_cache', None) is not None:
            return self._balance_cache
        try:
            if not self.rate_limiter.acquire(weight=1, timeout=5):
                # On limiter timeout, fall back to stale cache if available
                if getattr(self, '_balance_cache', None) is not None:
                    return self._balance_cache
                return {}
            balance = self.exchange.fetch_balance()
            self._balance_cache = balance
            self._balance_cache_ts = now
            self.logger.debug(f"Fetched balance from {self.exchange_id}")
            return balance
        except Exception as e:
            # On error, fall back to stale cache if available
            if getattr(self, '_balance_cache', None) is not None:
                self.logger.warning(f"Rate-limit / fetch error for balance: {e}. Using cached balance.")
                return self._balance_cache
            self.logger.error(f"Error fetching balance: {e}", exc_info=True)
            return {}
            
    def get_canonical_symbol(self, symbol: str) -> str:
        """
        Get the canonical symbol mapping from CCXT (e.g. 'BTC/USDT' -> 'BTC/USDT:USDT').
        Used for exact string matching against CCXT position and order objects.
        """
        try:
            market = self.exchange.market(symbol)
            return market.get('symbol', symbol)
        except Exception as e:
            self.logger.debug(f"Could not find canonical symbol for {symbol}: {e}")
            return symbol
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open positions (TTL-cached to avoid hammering Binance REST).

        RATE-LIMIT FIX: The PriorityExitSentinel thread calls this every 0.5s,
        which was a major contributor to the -1003 IP bans (2004+ occurrences).
        Positions don't change that fast, so we cache for a short TTL.
        """
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'POSITIONS_CACHE_TTL', 5.0)  # 5s default

        def _filter_by_symbol(positions_list):
            if not symbol:
                return positions_list
            return [p for p in positions_list if p.get('symbol') == symbol]

        cache_hit = (now - getattr(self, '_positions_cache_ts', 0)) < ttl and getattr(self, '_positions_cache', None) is not None
        if cache_hit:
            return _filter_by_symbol(self._positions_cache)
        if not self.rate_limiter.acquire(weight=5, timeout=5):
            # On limiter timeout, fall back to stale cache if available
            if getattr(self, '_positions_cache', None) is not None:
                return _filter_by_symbol(self._positions_cache)
            return []
        try:
            if symbol:
                positions = self.exchange.fetch_positions([symbol])
            else:
                positions = self.exchange.fetch_positions()
            
            # Filter out empty positions
            active_positions = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
            
            self._positions_cache = active_positions
            self._positions_cache_ts = now
            self.logger.debug(f"Fetched {len(active_positions)} positions from {self.exchange_id}")
            return _filter_by_symbol(active_positions)
        except Exception as e:
            # On error, fall back to stale cache if available
            if getattr(self, '_positions_cache', None) is not None:
                self.logger.warning(f"Rate-limit / fetch error for positions: {e}. Using cached positions.")
                return _filter_by_symbol(self._positions_cache)
            self.logger.error(f"Error fetching positions: {e}", exc_info=True)
            return []
    
    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get current ticker data (TTL-cached to avoid hammering Binance REST)."""
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'TICKER_CACHE_TTL', 5.0)  # 5s default
        cache_key = f"ticker_{symbol}"
        if (now - getattr(self, '_ticker_cache_ts', {}).get(cache_key, 0)) < ttl and cache_key in getattr(self, '_ticker_cache', {}):
            return self._ticker_cache[cache_key]
        if not self.rate_limiter.acquire(weight=2, timeout=5):
            # On limiter timeout, fall back to stale cache if available
            if hasattr(self, '_ticker_cache') and cache_key in self._ticker_cache:
                return self._ticker_cache[cache_key]
            return {}
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if not hasattr(self, '_ticker_cache'):
                self._ticker_cache = {}
                self._ticker_cache_ts = {}
            self._ticker_cache[cache_key] = ticker
            self._ticker_cache_ts[cache_key] = now
            return ticker
        except Exception as e:
            # On error, fall back to stale cache if available
            if hasattr(self, '_ticker_cache') and cache_key in self._ticker_cache:
                self.logger.warning(f"Rate-limit / fetch error for ticker {symbol}: {e}. Using cached ticker.")
                return self._ticker_cache[cache_key]
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
                order = self.exchange.create_market_order(symbol, side, amount, params=kwargs)
            elif order_type == 'limit':
                if price is None:
                    raise ValueError("Price required for limit orders")
                order = self.exchange.create_limit_order(symbol, side, amount, price, params=kwargs)
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
