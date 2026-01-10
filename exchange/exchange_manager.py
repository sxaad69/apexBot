"""
Exchange Manager
Coordinates multiple exchange connections for arbitrage and multi-exchange trading
"""

from typing import Dict, List
from .ccxt_client import CCXTExchangeClient


class ExchangeManager:
    """
    Manages multiple exchange connections
    Used for arbitrage scanning and multi-exchange operations
    """
    
    def __init__(self, config, logger):
        """
        Initialize exchange manager
        
        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        self.exchanges: Dict[str, CCXTExchangeClient] = {}
        
        # Initialize primary exchange
        self.primary_exchange = self._init_primary_exchange()
        
        # Initialize arbitrage exchanges if enabled
        if config.ENABLE_ARBITRAGE_SCANNER:
            self._init_arbitrage_exchanges()
        
        self.logger.system(f"Exchange manager initialized with {len(self.exchanges)} exchanges")
    
    def _init_primary_exchange(self) -> CCXTExchangeClient:
        """Initialize the primary trading exchange"""
        exchange_id = self.config.EXCHANGE.lower()
        client = CCXTExchangeClient(self.config, self.logger, exchange_id)
        self.exchanges[exchange_id] = client
        return client
    
    def _init_arbitrage_exchanges(self):
        """Initialize exchanges for arbitrage scanning"""
        # Parse arbitrage exchanges from config
        arb_exchanges = [
            ex.strip().lower() 
            for ex in self.config.ARBITRAGE_EXCHANGES.split(',')
        ]
        
        for exchange_id in arb_exchanges:
            if exchange_id in self.exchanges:
                continue  # Already initialized
            
            try:
                client = CCXTExchangeClient(self.config, self.logger, exchange_id)
                self.exchanges[exchange_id] = client
                self.logger.info(f"Initialized arbitrage exchange: {exchange_id}")
            except Exception as e:
                self.logger.warning(
                    f"Failed to initialize {exchange_id}: {e}. "
                    f"Skipping this exchange for arbitrage."
                )
    
    def get_exchange(self, exchange_id: str) -> CCXTExchangeClient:
        """
        Get exchange client by ID
        
        Args:
            exchange_id: Exchange identifier
        
        Returns:
            Exchange client instance
        """
        return self.exchanges.get(exchange_id.lower())
    
    def get_all_exchanges(self) -> Dict[str, CCXTExchangeClient]:
        """Get all initialized exchanges"""
        return self.exchanges
    
    def get_arbitrage_exchanges(self) -> Dict[str, CCXTExchangeClient]:
        """Get exchanges available for arbitrage"""
        return self.exchanges
    
    def fetch_tickers_all_exchanges(self, symbol: str) -> Dict[str, Dict]:
        """
        Fetch ticker from all exchanges for a symbol
        
        Args:
            symbol: Trading pair symbol
        
        Returns:
            Dictionary mapping exchange_id to ticker data
        """
        tickers = {}
        
        for exchange_id, client in self.exchanges.items():
            try:
                ticker = client.get_ticker(symbol)
                if ticker:
                    tickers[exchange_id] = ticker
            except Exception as e:
                self.logger.debug(f"Failed to fetch {symbol} from {exchange_id}: {e}")
        
        return tickers
    
    def get_exchange_balance(self, exchange_id: str) -> Dict:
        """
        Get balance from specific exchange
        
        Args:
            exchange_id: Exchange identifier
        
        Returns:
            Balance information
        """
        client = self.get_exchange(exchange_id)
        if client:
            return client.get_balance()
        return {}
