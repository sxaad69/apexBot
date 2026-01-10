"""
Configuration Management
Loads and validates environment variables and configuration settings
"""

import os
from typing import List, Optional
from dotenv import load_dotenv


class Config:
    """
    Centralized configuration management for Apex Hunter V14
    Loads settings from environment variables with validation
    """
    
    def __init__(self, env_file: Optional[str] = None):
        """
        Initialize configuration from environment variables
        
        Args:
            env_file: Path to .env file (optional)
        """
        # Load environment variables
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()
        
        self._load_configuration()
        self._validate_configuration()
    
    def _load_configuration(self):
        """Load all configuration values from environment variables"""
        
        # ===== Exchange Configuration (Multi-Exchange CCXT) =====
        self.EXCHANGE = os.getenv('EXCHANGE', 'binance').lower()
        self.EXCHANGE_ENVIRONMENT = os.getenv('EXCHANGE_ENVIRONMENT', 'testnet')
        
        # Futures and Spot exchanges
        self.FUTURES_EXCHANGE = os.getenv('FUTURES_EXCHANGE', 'kucoin').lower()
        self.SPOT_EXCHANGE = os.getenv('SPOT_EXCHANGE', 'binance').lower()
        
        # Arbitrage mode
        self.ARBITRAGE_MODE = os.getenv('ARBITRAGE_MODE', 'select').lower()
        
        # Legacy KuCoin-specific (backward compatibility)
        # Exchange environment is now set globally via EXCHANGE_ENVIRONMENT
        # Legacy KuCoin settings maintained for backwards compatibility
        self.KUCOIN_ENVIRONMENT = self.EXCHANGE_ENVIRONMENT
        if self.KUCOIN_ENVIRONMENT == 'testnet':
            self.KUCOIN_BASE_URL = 'https://api-sandbox-futures.kucoin.com'
        else:
            self.KUCOIN_BASE_URL = 'https://api-futures.kucoin.com'
        
        # Exchange API Credentials (Multiple Exchanges)
        self.BINANCE_API_KEY = os.getenv('BINANCE_API_KEY', '')
        self.BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET', '')
        
        self.KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY', '')
        self.KUCOIN_API_SECRET = os.getenv('KUCOIN_API_SECRET', '')
        self.KUCOIN_API_PASSPHRASE = os.getenv('KUCOIN_API_PASSPHRASE', '')
        
        self.BYBIT_API_KEY = os.getenv('BYBIT_API_KEY', '')
        self.BYBIT_API_SECRET = os.getenv('BYBIT_API_SECRET', '')
        
        self.OKX_API_KEY = os.getenv('OKX_API_KEY', '')
        self.OKX_API_SECRET = os.getenv('OKX_API_SECRET', '')
        self.OKX_API_PASSPHRASE = os.getenv('OKX_API_PASSPHRASE', '')
        
        self.GATE_API_KEY = os.getenv('GATE_API_KEY', '')
        self.GATE_API_SECRET = os.getenv('GATE_API_SECRET', '')
        
        # ===== Master Trading Controls (Triple Safety Lock) =====
        self.FUTURES_TRADING_ENABLED = os.getenv('FUTURES_TRADING_ENABLED', 'no').lower()
        self.SPOT_TRADING_ENABLED = os.getenv('SPOT_TRADING_ENABLED', 'no').lower()
        self.ARBITRAGE_TRADING_ENABLED = os.getenv('ARBITRAGE_TRADING_ENABLED', 'no').lower()
        
        # ===== Futures Trading Configuration =====
        self.FUTURES_VIRTUAL_CAPITAL = float(os.getenv('FUTURES_VIRTUAL_CAPITAL', '100'))
        self.FUTURES_POSITION_SIZE_PERCENT = float(os.getenv('FUTURES_POSITION_SIZE_PERCENT', '10'))
        self.FUTURES_MAX_LEVERAGE = int(os.getenv('FUTURES_MAX_LEVERAGE', '10'))
        self.FUTURES_STOP_LOSS_PERCENT = float(os.getenv('FUTURES_STOP_LOSS_PERCENT', '2'))
        self.FUTURES_TAKE_PROFIT_PERCENT = float(os.getenv('FUTURES_TAKE_PROFIT_PERCENT', '4'))
        self.FUTURES_MAX_DAILY_LOSS_PERCENT = float(os.getenv('FUTURES_MAX_DAILY_LOSS_PERCENT', '5'))
        self.FUTURES_MAX_DRAWDOWN_PERCENT = float(os.getenv('FUTURES_MAX_DRAWDOWN_PERCENT', '15'))
        self.FUTURES_MAX_OPEN_POSITIONS = int(os.getenv('FUTURES_MAX_OPEN_POSITIONS', '5'))
        futures_pairs_str = os.getenv('FUTURES_PAIRS', 'BTC/USDT,ETH/USDT,SOL/USDT')
        # Support 'auto' mode or comma-separated list
        if futures_pairs_str.lower() == 'auto':
            self.FUTURES_PAIRS = 'auto'
        else:
            self.FUTURES_PAIRS = [pair.strip() for pair in futures_pairs_str.split(',')]

        # Auto-scan configuration
        self.FUTURES_AUTO_TOP_N = int(os.getenv('FUTURES_AUTO_TOP_N', '30'))
        self.FUTURES_AUTO_MIN_VOLUME = float(os.getenv('FUTURES_AUTO_MIN_VOLUME', '1000000'))

        # ===== Spot Trading Configuration =====
        self.ENABLE_SPOT_LOGGER = self._str_to_bool(os.getenv('ENABLE_SPOT_LOGGER', 'true'))
        self.SPOT_VIRTUAL_CAPITAL = float(os.getenv('SPOT_VIRTUAL_CAPITAL', '100'))
        self.SPOT_POSITION_SIZE_PERCENT = float(os.getenv('SPOT_POSITION_SIZE_PERCENT', '10'))
        self.SPOT_STOP_LOSS_PERCENT = float(os.getenv('SPOT_STOP_LOSS_PERCENT', '2'))
        self.SPOT_TAKE_PROFIT_PERCENT = float(os.getenv('SPOT_TAKE_PROFIT_PERCENT', '4'))
        self.SPOT_USE_FULL_RISK_SYSTEM = self._str_to_bool(os.getenv('SPOT_USE_FULL_RISK_SYSTEM', 'true'))
        self.SPOT_MAX_DAILY_LOSS_PERCENT = float(os.getenv('SPOT_MAX_DAILY_LOSS_PERCENT', '5'))
        self.SPOT_MAX_DRAWDOWN_PERCENT = float(os.getenv('SPOT_MAX_DRAWDOWN_PERCENT', '15'))
        spot_pairs_str = os.getenv('SPOT_PAIRS', 'BTC/USDT,ETH/USDT,SOL/USDT')
        self.SPOT_PAIRS = [pair.strip() for pair in spot_pairs_str.split(',')]
        self.SPOT_TELEGRAM_NOTIFICATIONS = self._str_to_bool(os.getenv('SPOT_TELEGRAM_NOTIFICATIONS', 'true'))
        self.SPOT_LOG_SIGNALS = self._str_to_bool(os.getenv('SPOT_LOG_SIGNALS', 'true'))
        self.SPOT_DAILY_SUMMARY = self._str_to_bool(os.getenv('SPOT_DAILY_SUMMARY', 'true'))
        self.SPOT_DAILY_SUMMARY_TIME = os.getenv('SPOT_DAILY_SUMMARY_TIME', '00:00')
        
        # ===== Arbitrage Scanner Configuration =====
        self.ENABLE_ARBITRAGE_SCANNER = self._str_to_bool(os.getenv('ENABLE_ARBITRAGE_SCANNER', 'true'))
        self.ARBITRAGE_VIRTUAL_CAPITAL = float(os.getenv('ARBITRAGE_VIRTUAL_CAPITAL', '100'))
        self.ARBITRAGE_SIMPLE = self._str_to_bool(os.getenv('ARBITRAGE_SIMPLE', 'true'))
        self.ARBITRAGE_TRIANGULAR = self._str_to_bool(os.getenv('ARBITRAGE_TRIANGULAR', 'true'))
        self.ARBITRAGE_CROSS_TRIANGULAR = self._str_to_bool(os.getenv('ARBITRAGE_CROSS_TRIANGULAR', 'false'))
        self.ARBITRAGE_MIN_PROFIT_PERCENT = float(os.getenv('ARBITRAGE_MIN_PROFIT_PERCENT', '2.0'))
        self.ARBITRAGE_CHECK_INTERVAL = int(os.getenv('ARBITRAGE_CHECK_INTERVAL', '30'))
        self.ARBITRAGE_INCLUDE_ALL_FEES = self._str_to_bool(os.getenv('ARBITRAGE_INCLUDE_ALL_FEES', 'true'))
        self.ARBITRAGE_MIN_VOLUME = float(os.getenv('ARBITRAGE_MIN_VOLUME', '10000'))
        self.ARBITRAGE_EXCHANGES = os.getenv('ARBITRAGE_EXCHANGES', 'binance,kucoin,bybit,okx,gate,huobi,kraken,coinbase,bitget,mexc')
        arb_pairs_str = os.getenv('ARBITRAGE_PAIRS', 'BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT')
        self.ARBITRAGE_PAIRS = [pair.strip() for pair in arb_pairs_str.split(',')]
        self.ARBITRAGE_TELEGRAM_NOTIFICATIONS = self._str_to_bool(os.getenv('ARBITRAGE_TELEGRAM_NOTIFICATIONS', 'true'))
        self.ARBITRAGE_LOG_TOP_N_PER_HOUR = int(os.getenv('ARBITRAGE_LOG_TOP_N_PER_HOUR', '5'))
        self.ARBITRAGE_DAILY_SUMMARY = self._str_to_bool(os.getenv('ARBITRAGE_DAILY_SUMMARY', 'true'))
        self.ARBITRAGE_DAILY_SUMMARY_TIME = os.getenv('ARBITRAGE_DAILY_SUMMARY_TIME', '00:00')
        
        # ===== Legacy Configuration (Backwards Compatibility) =====
        self.TRADING_MODE = os.getenv('TRADING_MODE', 'simulation')
        self.INITIAL_CAPITAL = float(os.getenv('INITIAL_CAPITAL', '20'))
        self.BASE_CURRENCY = os.getenv('BASE_CURRENCY', 'USDT')
        trading_pairs_str = os.getenv('TRADING_PAIRS', 'BTCUSDT,ETHUSDT,SOLUSDT')
        self.TRADING_PAIRS = [pair.strip() for pair in trading_pairs_str.split(',')]
        
        # ===== Position Sizing Configuration =====
        self.POSITION_SIZE_PERCENT = float(os.getenv('POSITION_SIZE_PERCENT', '10'))
        self.MAX_LEVERAGE = int(os.getenv('MAX_LEVERAGE', '10'))
        self.MIN_POSITION_SIZE = float(os.getenv('MIN_POSITION_SIZE', '1'))
        self.MAX_POSITION_SIZE = float(os.getenv('MAX_POSITION_SIZE', '1000'))
        self.MAX_OPEN_POSITIONS = int(os.getenv('MAX_OPEN_POSITIONS', '5'))
        
        # ===== Risk Management Configuration =====
        self.STOP_LOSS_PERCENT = float(os.getenv('STOP_LOSS_PERCENT', '2'))
        self.TRAILING_STOP_ACTIVATION = float(os.getenv('TRAILING_STOP_ACTIVATION', '5'))
        self.TRAILING_STOP_DISTANCE = float(os.getenv('TRAILING_STOP_DISTANCE', '2'))
        self.MAX_DAILY_LOSS_PERCENT = float(os.getenv('MAX_DAILY_LOSS_PERCENT', '5'))
        self.MAX_DRAWDOWN_PERCENT = float(os.getenv('MAX_DRAWDOWN_PERCENT', '15'))
        self.CORRELATION_THRESHOLD = float(os.getenv('CORRELATION_THRESHOLD', '0.7'))
        self.VOLATILITY_LOOKBACK_PERIODS = int(os.getenv('VOLATILITY_LOOKBACK_PERIODS', '20'))
        self.MIN_LIQUIDITY_DEPTH = float(os.getenv('MIN_LIQUIDITY_DEPTH', '10000'))
        
        # ===== Circuit Breaker Configuration =====
        self.ENABLE_CIRCUIT_BREAKER = self._str_to_bool(os.getenv('ENABLE_CIRCUIT_BREAKER', 'true'))
        self.TRADE_FAILURE_HALT_HOURS = int(os.getenv('TRADE_FAILURE_HALT_HOURS', '48'))
        self.CONSECUTIVE_LOSSES_THRESHOLD = int(os.getenv('CONSECUTIVE_LOSSES_THRESHOLD', '5'))
        self.FLASH_CRASH_THRESHOLD = float(os.getenv('FLASH_CRASH_THRESHOLD', '-10'))
        
        # ===== Telegram Integration (3 Separate Bots) =====
        # Futures Bot
        self.TELEGRAM_FUTURES_ENABLED = self._str_to_bool(os.getenv('TELEGRAM_FUTURES_ENABLED', 'true'))
        self.TELEGRAM_FUTURES_BOT_TOKEN = os.getenv('TELEGRAM_FUTURES_BOT_TOKEN', '')
        self.TELEGRAM_FUTURES_CHAT_ID = os.getenv('TELEGRAM_FUTURES_CHAT_ID', '')
        
        # Spot Bot
        self.TELEGRAM_SPOT_ENABLED = self._str_to_bool(os.getenv('TELEGRAM_SPOT_ENABLED', 'true'))
        self.TELEGRAM_SPOT_BOT_TOKEN = os.getenv('TELEGRAM_SPOT_BOT_TOKEN', '')
        self.TELEGRAM_SPOT_CHAT_ID = os.getenv('TELEGRAM_SPOT_CHAT_ID', '')
        
        # Arbitrage Bot
        self.TELEGRAM_ARBITRAGE_ENABLED = self._str_to_bool(os.getenv('TELEGRAM_ARBITRAGE_ENABLED', 'true'))
        self.TELEGRAM_ARBITRAGE_BOT_TOKEN = os.getenv('TELEGRAM_ARBITRAGE_BOT_TOKEN', '')
        self.TELEGRAM_ARBITRAGE_CHAT_ID = os.getenv('TELEGRAM_ARBITRAGE_CHAT_ID', '')
        
        # Legacy Telegram settings (backwards compatibility)
        self.TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID', '')
        self.TELEGRAM_NOTIFICATIONS = self._str_to_bool(os.getenv('TELEGRAM_NOTIFICATIONS', 'true'))
        self.TELEGRAM_DAILY_SUMMARY = self._str_to_bool(os.getenv('TELEGRAM_DAILY_SUMMARY', 'true'))
        self.TELEGRAM_DAILY_SUMMARY_TIME = os.getenv('TELEGRAM_DAILY_SUMMARY_TIME', '00:00')
        
        # ===== Logging Configuration =====
        self.LOG_API_CALLS = self._str_to_bool(os.getenv('LOG_API_CALLS', 'true'))
        self.LOG_POSITION_REJECTIONS = self._str_to_bool(os.getenv('LOG_POSITION_REJECTIONS', 'true'))
        self.LOG_TOKEN_METRICS = self._str_to_bool(os.getenv('LOG_TOKEN_METRICS', 'false'))
        self.LOG_RISK_MANAGEMENT = self._str_to_bool(os.getenv('LOG_RISK_MANAGEMENT', 'true'))
        self.LOG_TRADE_EXECUTION = self._str_to_bool(os.getenv('LOG_TRADE_EXECUTION', 'true'))
        self.LOG_PERFORMANCE = self._str_to_bool(os.getenv('LOG_PERFORMANCE', 'true'))
        self.LOG_SYSTEM_EVENTS = self._str_to_bool(os.getenv('LOG_SYSTEM_EVENTS', 'true'))
        self.LOG_ERROR_TRACES = self._str_to_bool(os.getenv('LOG_ERROR_TRACES', 'true'))
        self.LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
        self.LOG_OUTPUT = os.getenv('LOG_OUTPUT', 'both')
        self.LOG_FILE_PATH = os.getenv('LOG_FILE_PATH', './logs')
        self.LOG_FILE_MAX_SIZE = int(os.getenv('LOG_FILE_MAX_SIZE', '10'))
        self.LOG_FILE_BACKUP_COUNT = int(os.getenv('LOG_FILE_BACKUP_COUNT', '5'))

        # ===== MongoDB Configuration =====
        self.MONGODB_ENABLED = self._str_to_bool(os.getenv('MONGODB_ENABLED', 'true'))
        self.MONGODB_HOST = os.getenv('MONGODB_HOST', '')
        self.MONGODB_PORT = int(os.getenv('MONGODB_PORT', '27017'))
        self.MONGODB_DATABASE = os.getenv('MONGODB_DATABASE', 'apex_hunter_v14')
        self.MONGODB_USERNAME = os.getenv('MONGODB_USERNAME', '')
        self.MONGODB_PASSWORD = os.getenv('MONGODB_PASSWORD', '')
        self.MONGODB_RETENTION_DAYS = int(os.getenv('MONGODB_RETENTION_DAYS', '30'))

        # ===== Cleanup Configuration =====
        self.CLEAN_LOGS = self._str_to_bool(os.getenv('CLEAN_LOGS', 'no'))
        self.CLEAN_DB = self._str_to_bool(os.getenv('CLEAN_DB', 'no'))

        # ===== System Configuration =====
        self.HEARTBEAT_INTERVAL = int(os.getenv('HEARTBEAT_INTERVAL', '60'))
        self.API_TIMEOUT = int(os.getenv('API_TIMEOUT', '10'))
        self.RETRY_ATTEMPTS = int(os.getenv('RETRY_ATTEMPTS', '3'))
        self.RETRY_DELAY = int(os.getenv('RETRY_DELAY', '1'))
        self.RATE_LIMIT_BUFFER = float(os.getenv('RATE_LIMIT_BUFFER', '0.8'))
        self.DATA_PERSISTENCE = self._str_to_bool(os.getenv('DATA_PERSISTENCE', 'true'))
        self.DATA_DIRECTORY = os.getenv('DATA_DIRECTORY', './data')
        
        # ===== Advanced Configuration =====
        self.ENABLE_PAPER_TRADING_SLIPPAGE = self._str_to_bool(
            os.getenv('ENABLE_PAPER_TRADING_SLIPPAGE', 'true')
        )
        self.PAPER_TRADING_SLIPPAGE_PERCENT = float(
            os.getenv('PAPER_TRADING_SLIPPAGE_PERCENT', '0.1')
        )
        self.ENABLE_PERFORMANCE_ANALYTICS = self._str_to_bool(
            os.getenv('ENABLE_PERFORMANCE_ANALYTICS', 'true')
        )
        self.REBALANCE_ON_PROFIT = self._str_to_bool(os.getenv('REBALANCE_ON_PROFIT', 'false'))
        self.REBALANCE_THRESHOLD = float(os.getenv('REBALANCE_THRESHOLD', '20'))
        
        # ===== Safety Configuration =====
        self.REQUIRE_CONFIRMATION_FOR_LIVE = self._str_to_bool(
            os.getenv('REQUIRE_CONFIRMATION_FOR_LIVE', 'true')
        )
        self.ENABLE_EMERGENCY_SHUTDOWN = self._str_to_bool(
            os.getenv('ENABLE_EMERGENCY_SHUTDOWN', 'true')
        )
        self.EMERGENCY_SHUTDOWN_PASSWORD = os.getenv(
            'EMERGENCY_SHUTDOWN_PASSWORD',
            'change_this_password'
        )
        self.MAX_API_ERRORS_PER_HOUR = int(os.getenv('MAX_API_ERRORS_PER_HOUR', '10'))
    
    def _validate_configuration(self):
        """Validate critical configuration values"""
        
        # Validate KuCoin credentials
        if self.TRADING_MODE == 'live':
            if not self.KUCOIN_API_KEY or not self.KUCOIN_SECRET_KEY or not self.KUCOIN_PASSPHRASE:
                raise ValueError("KuCoin API credentials are required for live trading")
        
        # Validate Telegram credentials if notifications enabled
        if self.TELEGRAM_NOTIFICATIONS:
            if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_USER_ID:
                raise ValueError("Telegram credentials required when notifications are enabled")
        
        # Validate numeric ranges
        if not 1 <= self.POSITION_SIZE_PERCENT <= 100:
            raise ValueError("POSITION_SIZE_PERCENT must be between 1 and 100")
        
        if self.MAX_LEVERAGE < 1:
            raise ValueError("MAX_LEVERAGE must be at least 1")
        
        if not 0 < self.CORRELATION_THRESHOLD <= 1:
            raise ValueError("CORRELATION_THRESHOLD must be between 0 and 1")
        
        # Validate mode settings
        if self.TRADING_MODE not in ['simulation', 'live']:
            raise ValueError("TRADING_MODE must be 'simulation' or 'live'")
        
        # Validate exchange environment
        if self.EXCHANGE_ENVIRONMENT not in ['testnet', 'production']:
            raise ValueError("EXCHANGE_ENVIRONMENT must be 'testnet' or 'production'")
        
        # Validate log level
        if self.LOG_LEVEL not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
        
        # Create necessary directories
        if self.DATA_PERSISTENCE:
            os.makedirs(self.DATA_DIRECTORY, exist_ok=True)
        
        if self.LOG_OUTPUT in ['file', 'both']:
            os.makedirs(self.LOG_FILE_PATH, exist_ok=True)
    
    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean"""
        return value.lower() in ('true', '1', 'yes', 'on')
    
    def get_drawdown_adjusted_position_size(self, current_drawdown: float) -> float:
        """
        Calculate position size adjustment based on current drawdown
        
        Args:
            current_drawdown: Current drawdown percentage (positive value)
        
        Returns:
            Adjusted position size multiplier (0.0 to 1.0)
        """
        if current_drawdown >= self.MAX_DRAWDOWN_PERCENT:
            return 0.0  # No trading
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.67:
            return 0.33  # 33% of normal size
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.33:
            return 0.67  # 67% of normal size
        else:
            return 1.0  # Full size
    
    def get_drawdown_adjusted_leverage(self, current_drawdown: float) -> int:
        """
        Calculate leverage adjustment based on current drawdown
        
        Args:
            current_drawdown: Current drawdown percentage (positive value)
        
        Returns:
            Adjusted maximum leverage
        """
        if current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.67:
            return max(1, self.MAX_LEVERAGE // 2)
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.33:
            return max(1, int(self.MAX_LEVERAGE * 0.7))
        else:
            return self.MAX_LEVERAGE
    
    def is_live_trading(self) -> bool:
        """Check if bot is in live trading mode"""
        return self.TRADING_MODE == 'live'
    
    def is_production_environment(self) -> bool:
        """Check if connected to production exchange"""
        return self.EXCHANGE_ENVIRONMENT == 'production'
    
    def __repr__(self) -> str:
        """String representation of configuration"""
        return (
            f"Config(mode={self.TRADING_MODE}, "
            f"env={self.EXCHANGE_ENVIRONMENT}, "
            f"capital={self.INITIAL_CAPITAL} {self.BASE_CURRENCY})"
        )
