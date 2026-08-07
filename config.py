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
        self.ARBITRAGE_MODE = 'select'.lower()
        
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
        self.FUTURES_TRADING_ENABLED = 'true'.lower()
        self.SPOT_TRADING_ENABLED = 'false'.lower()
        self.ARBITRAGE_TRADING_ENABLED = 'false'.lower()
        
        # ===== Futures Trading Configuration (Primary) =====
        self.FUTURES_TRADING_ENABLED = self._str_to_bool('true')
        self.FUTURES_VIRTUAL_CAPITAL = 136.78  # Real Wallet Baseline
        self.FUTURES_POSITION_SIZE_PERCENT = float('4.0')
        self.FUTURES_MAX_LEVERAGE = int('3')
        self.FUTURES_TAKE_PROFIT_PERCENT = float('10')
        self.FUTURES_MAX_DAILY_LOSS_PERCENT = float('5')
        self.FUTURES_MAX_DRAWDOWN_PERCENT = float('70')
        self.FUTURES_MAX_OPEN_POSITIONS = int('15')

        # --- Exposure & Reserve Management ---
        # FUTURES_MAX_EXPOSURE_NORMAL: Max % of capital for standard signals (Confidence < Threshold)
        self.FUTURES_MAX_EXPOSURE_NORMAL = 0.85 
        
        # FUTURES_MAX_EXPOSURE_ELITE: Max % of capital for high-conviction signals (Confidence >= Threshold)
        self.FUTURES_MAX_EXPOSURE_ELITE = 0.95
        
        # FUTURES_OPPORTUNITY_THRESHOLD: Confidence needed to tap into the Elite reserve.
        self.FUTURES_OPPORTUNITY_THRESHOLD = 0.90

        # Market Discovery Sync
        self.FUTURES_PAIRS = 'auto'
        self.FUTURES_AUTO_TOP_N = int('100')
        self.FUTURES_AUTO_MIN_VOLUME = float('500000')
        
        # Position Boundary Sync
        self.MIN_POSITION_SIZE = float('10.0')  # SETTING FOR TESTNET (Previous Live: 10.0)
        self.MAX_POSITION_SIZE = float('30.0')  # SETTING FOR TESTNET (Previous Live: 30.0)

        # ===== Global Synchronization (For Legacy/Risk Layers compatibility) =====
        self.INITIAL_CAPITAL = self.FUTURES_VIRTUAL_CAPITAL
        self.POSITION_SIZE_PERCENT = self.FUTURES_POSITION_SIZE_PERCENT
        self.MAX_LEVERAGE = self.FUTURES_MAX_LEVERAGE
        # Strategy-Specific Overrides (The "Breathing Room" Knobs)
        self.A5_MAX_LEVERAGE = int('3')
        self.A5_STOP_LOSS_ROE = float('10.0')
        self.A6_STOP_LOSS_ROE = float('10.0')
        
        # GLOBAL ROE Shield (The "Safety Guard")
        self.GLOBAL_STOP_LOSS_ROE = float('10.0')
        self.MAX_ROE_DRAWDOWN = float('10.0')
        self.MAX_EQUITY_RISK_PERCENT = float('5.0')
        self.TAKE_PROFIT_PERCENT = self.FUTURES_TAKE_PROFIT_PERCENT
        self.MAX_DAILY_LOSS_PERCENT = self.FUTURES_MAX_DAILY_LOSS_PERCENT
        self.MAX_DRAWDOWN_PERCENT = self.FUTURES_MAX_DRAWDOWN_PERCENT
        self.MAX_OPEN_POSITIONS = self.FUTURES_MAX_OPEN_POSITIONS
        
        # Parallel Execution & Safety Cooldown Configurations
        self.FUTURES_SYMBOL_COOLDOWN_MINUTES = int('15')
        self.DISCOVERY_MAX_WORKERS = int('5')
        
        # Core Mode & Currency (Synched)
        # Core Mode & Currency (Synched)
        self.TRADING_MODE = os.getenv('TRADING_MODE', 'paper').lower()
        self.BASE_CURRENCY = 'USDT'
        trading_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT'
        self.TRADING_PAIRS = [p.strip() for p in trading_pairs_str.split(',')]
        
        # Margin configuration (Phase 32)
        self.FUTURES_MARGIN_MODE = 'ISOLATED'.upper()
        
        # Risk Layer Sync (Fixes technical debt from Phase 45)
        self.MAX_LEVERAGE_ABSOLUTE = self.FUTURES_MAX_LEVERAGE

        # ===== Spot Trading Configuration =====
        self.ENABLE_SPOT_TRADING = self._str_to_bool('false')
        self.ENABLE_SPOT_LOGGER = self._str_to_bool('true')
        self.SPOT_VIRTUAL_CAPITAL = float('100')
        self.SPOT_POSITION_SIZE_PERCENT = float('10')
        self.SPOT_STOP_LOSS_PERCENT = float('2')
        self.SPOT_TAKE_PROFIT_PERCENT = float('4')
        self.SPOT_USE_FULL_RISK_SYSTEM = self._str_to_bool('true')
        self.SPOT_MAX_DAILY_LOSS_PERCENT = float('5')
        self.SPOT_MAX_DRAWDOWN_PERCENT = float('15')
        spot_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT'
        self.SPOT_PAIRS = [pair.strip() for pair in spot_pairs_str.split(',')]
        self.SPOT_TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.SPOT_LOG_SIGNALS = self._str_to_bool('true')
        self.SPOT_DAILY_SUMMARY = self._str_to_bool('true')
        self.SPOT_DAILY_SUMMARY_TIME = '00:00'
        
        # ===== Arbitrage Scanner Configuration =====
        self.ENABLE_ARBITRAGE_SCANNER = self._str_to_bool('true')
        self.ARBITRAGE_VIRTUAL_CAPITAL = float('100')
        self.ARBITRAGE_SIMPLE = self._str_to_bool('true')
        self.ARBITRAGE_TRIANGULAR = self._str_to_bool('true')
        self.ARBITRAGE_CROSS_TRIANGULAR = self._str_to_bool('false')
        self.ARBITRAGE_MIN_PROFIT_PERCENT = float('2.0')
        self.ARBITRAGE_CHECK_INTERVAL = int('30')
        self.ARBITRAGE_INCLUDE_ALL_FEES = self._str_to_bool('true')
        self.ARBITRAGE_MIN_VOLUME = float('10000')
        self.ARBITRAGE_EXCHANGES = 'binance,kucoin,bybit,okx,gate,huobi,kraken,coinbase,bitget,mexc'
        arb_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT'
        self.ARBITRAGE_PAIRS = [pair.strip() for pair in arb_pairs_str.split(',')]
        self.ARBITRAGE_TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.ARBITRAGE_LOG_TOP_N_PER_HOUR = int('5')
        self.ARBITRAGE_DAILY_SUMMARY = self._str_to_bool('true')
        self.ARBITRAGE_DAILY_SUMMARY_TIME = '00:00'
        
        # (Legacy overrides REMOVED to prevent FUTURES_* settings from being overwritten)
        # self.POSITION_SIZE_PERCENT, self.MAX_LEVERAGE, etc are now managed in the Global section above
        
        # ===== Strategy Selection Configuration =====
        # NOTE: A3, A4, A5 enabled for TESTNET only. DISABLE for LIVE prod.
        self.STRATEGY_A1_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A2_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A3_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A4_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A5_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A6_ENABLED = self._str_to_bool('true')
        self.A6_ALLOW_SHORT = self._str_to_bool('false')
        
        # Strategy-specific Risk Overrides
        self.STRATEGY_A3_SL_ROE = float('5.0')
        
        # ===== Fee Configuration (Simulation) =====
        self.FUTURES_FEE_PERCENT = float('0.04')
        self.SPOT_FEE_PERCENT = float('0.1')
        
        # ===== Risk Management Configuration =====
        self.TRAILING_STOP_ACTIVATION = float('5.0')  # Activate trailing stop after 5% profit
        self.TRAILING_STOP_DISTANCE = float('3.0')    # Trail 3% below the peak

        # Trailing Take Profit Configuration (Phase 53: Leverage-Aware)
        self.TRAILING_TP_ENABLED = self._str_to_bool('true')
        self.TRAILING_TP_ACTIVATION = float('1.0')  # Fallback price move %
        self.TRAILING_TP_DISTANCE = float('1.5')    # Fallback price move %
        
        # New ROE-based Targets (Scales with leverage)
        self.TRAILING_TARGET_ROE = float('6.0')      # Target 6% ROE to start trailing
        self.TRAILING_CAPTURE_ROE = float('2.5')    # Gap of 2.5% ROE from peak

        # Exchange-Side Execution
        self.ENABLE_EXCHANGE_STOPS = self._str_to_bool('false')
        self.EXCHANGE_TP_ORDER_TYPE = 'TAKE_PROFIT_MARKET'

        self.MAX_DAILY_LOSS_PERCENT = float('15')
        self.MAX_DRAWDOWN_PERCENT = float('10')
        
        # ===== Tiered Risk Management (Phase 14) =====
        self.TIERED_RISK_ENABLED = self._str_to_bool('true')
        self.NORMAL_SIGNAL_THRESHOLD = float('50.0')
        self.ELITE_SIGNAL_THRESHOLD = float('70.0')
        self.ELITE_CONFIDENCE_LEVEL = float('0.90')
        
        self.CORRELATION_THRESHOLD = float('0.7')
        self.VOLATILITY_LOOKBACK_PERIODS = int('20')
        self.MIN_LIQUIDITY_DEPTH = float('10000')
        
        # ===== Circuit Breaker Configuration =====
        self.ENABLE_CIRCUIT_BREAKER = self._str_to_bool('false')
        self.TRADE_FAILURE_HALT_HOURS = float('0.5')
        self.CONSECUTIVE_LOSSES_THRESHOLD = int('5')
        self.FLASH_CRASH_THRESHOLD = float('-10')
        
        # ===== Portfolio Loss Circuit Breaker =====
        self.LOSS_CB_ENABLED = self._str_to_bool('true')
        self.LOSS_CB_PCT = float('10.0')
        self.LOSS_CB_COOLDOWN_MINUTES = int('30')
        
        # ===== Telegram Integration (3 Separate Bots) =====
        # Futures Bot
        self.TELEGRAM_FUTURES_ENABLED = self._str_to_bool('true')
        self.TELEGRAM_FUTURES_BOT_TOKEN = os.getenv('TELEGRAM_FUTURES_BOT_TOKEN', '')
        self.TELEGRAM_FUTURES_CHAT_ID = os.getenv('TELEGRAM_FUTURES_CHAT_ID', '')
        
        # Spot Bot
        self.TELEGRAM_SPOT_ENABLED = self._str_to_bool('false')
        self.TELEGRAM_SPOT_BOT_TOKEN = os.getenv('TELEGRAM_SPOT_BOT_TOKEN', '')
        self.TELEGRAM_SPOT_CHAT_ID = os.getenv('TELEGRAM_SPOT_CHAT_ID', '')
        
        # Arbitrage Bot
        self.TELEGRAM_ARBITRAGE_ENABLED = self._str_to_bool('false')
        self.TELEGRAM_ARBITRAGE_BOT_TOKEN = os.getenv('TELEGRAM_ARBITRAGE_BOT_TOKEN', '')
        self.TELEGRAM_ARBITRAGE_CHAT_ID = os.getenv('TELEGRAM_ARBITRAGE_CHAT_ID', '')
        
        # Legacy Telegram settings (backwards compatibility)
        self.TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID', '')
        self.TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.TELEGRAM_DAILY_SUMMARY = self._str_to_bool('false')
        self.TELEGRAM_DAILY_SUMMARY_TIME = '00:00'
        
        # ===== Logging Configuration =====
        self.LOG_API_CALLS = self._str_to_bool('true')
        self.LOG_POSITION_REJECTIONS = self._str_to_bool('true')
        self.LOG_TOKEN_METRICS = self._str_to_bool('false')
        self.LOG_RISK_MANAGEMENT = self._str_to_bool('true')
        self.LOG_TRADE_EXECUTION = self._str_to_bool('true')
        self.LOG_PERFORMANCE = self._str_to_bool('true')
        self.LOG_SYSTEM_EVENTS = self._str_to_bool('true')
        self.LOG_ERROR_TRACES = self._str_to_bool('true')
        self.LOG_LEVEL = 'INFO'.upper()
        self.LOG_OUTPUT = 'both'
        self.LOG_FILE_PATH = './logs'
        self.LOG_FILE_MAX_SIZE = int('10')
        self.LOG_FILE_BACKUP_COUNT = int('5')
        self.MUTE_REJECTION_LOGS = self._str_to_bool('false')
        self.ERROR_LOG_FILE = 'apex_error.log'

        # ===== MongoDB Configuration =====
        self.MONGODB_ENABLED = self._str_to_bool('false')
        # ===== SQLite Configuration =====
        self.SQLITE_ENABLED = self._str_to_bool('true')
        self.SQLITE_DB_NAME = os.getenv('SQLITE_DB_NAME', 'apex_hunter_v14.db')

        self.MONGODB_HOST = os.getenv('MONGODB_HOST', '')
        self.MONGODB_PORT = int(os.getenv('MONGODB_PORT', '27017'))
        self.MONGODB_DATABASE = os.getenv('MONGODB_DATABASE', 'apex_hunter_v14')
        self.MONGODB_USERNAME = os.getenv('MONGODB_USERNAME', '')
        self.MONGODB_PASSWORD = os.getenv('MONGODB_PASSWORD', '')
        self.MONGODB_RETENTION_DAYS = int('30')

        # ===== Cleanup Configuration =====
        self.CLEAN_LOGS = self._str_to_bool('no')
        self.CLEAN_DB = self._str_to_bool('no')
        self.CLEAN_TELEGRAM = self._str_to_bool('no')

        # ===== Testing/Filter Configuration =====
        self.TESTING_MODE = self._str_to_bool('false')
        self.TESTING_ADX_MIN = float('15')
        self.TESTING_VOLUME_MULT = float('0.8')
        self.MIN_VOLUME_USDT = float('10000')
        self.MAX_VOLATILITY_PERCENT = float('5.0')
        self.FORCE_TRADES = self._str_to_bool('false')

        # ===== System Configuration =====
        self.TIMEFRAME = '15m'
        self.HEARTBEAT_INTERVAL = int('60')
        self.API_TIMEOUT = int('10')
        self.RETRY_ATTEMPTS = int('3')
        self.RETRY_DELAY = int('1')
        self.RATE_LIMIT_BUFFER = float('0.8')
        self.DATA_PERSISTENCE = self._str_to_bool('true')
        self.DATA_DIRECTORY = './data'
        
        # ===== Advanced Configuration =====
        self.ENABLE_PAPER_TRADING_SLIPPAGE = self._str_to_bool(
            'true'
        )
        self.PAPER_TRADING_SLIPPAGE_PERCENT = float(
            '0.1'
        )
        self.ENABLE_PERFORMANCE_ANALYTICS = self._str_to_bool(
            'true'
        )
        self.REBALANCE_ON_PROFIT = self._str_to_bool('false')
        self.REBALANCE_THRESHOLD = float('20')
        
        # ===== Safety Configuration =====
        self.REQUIRE_CONFIRMATION_FOR_LIVE = self._str_to_bool(
            'true'
        )
        self.ENABLE_EMERGENCY_SHUTDOWN = self._str_to_bool(
            'true'
        )
        self.EMERGENCY_SHUTDOWN_PASSWORD = os.getenv(
            'EMERGENCY_SHUTDOWN_PASSWORD',
            'change_this_password'
        )
        self.MAX_API_ERRORS_PER_HOUR = int('10')

        # ===== Portfolio Profit Ratchet (Phase: Gains Lock) =====
        self.PROFIT_RATCHET_ENABLED = self._str_to_bool('false')
        self.PROFIT_RATCHET_ACTIVATION = float('1.0')
        self.PROFIT_RATCHET_TRAILING = float('1.0')
        self.PROFIT_RATCHET_FLOOR = float('1.0')
        self.PROFIT_RATCHET_COOLDOWN = int('5')
        self.PROFIT_RATCHET_SLIPPAGE_BUFFER = float('0.2')
    
    def _validate_configuration(self):
        """Validate critical configuration values"""
        
        # Validate Exchange Credentials (Only for active exchanges)
        if self.TRADING_MODE == 'live':
            # KuCoin Validation
            if any(ex == 'kucoin' for ex in [self.EXCHANGE, self.FUTURES_EXCHANGE, self.SPOT_EXCHANGE]):
                if not self.KUCOIN_API_KEY or not self.KUCOIN_API_SECRET or not self.KUCOIN_API_PASSPHRASE:
                    raise ValueError("KuCoin API credentials are required for live KuCoin trading")
            
            # Binance Validation
            if any(ex == 'binance' for ex in [self.EXCHANGE, self.FUTURES_EXCHANGE, self.SPOT_EXCHANGE]):
                if not self.BINANCE_API_KEY or not self.BINANCE_API_SECRET:
                    raise ValueError("Binance API credentials are required for live Binance trading")
        
        # Validate Telegram credentials if notifications enabled
        if self.TELEGRAM_NOTIFICATIONS:
            if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_USER_ID:
                raise ValueError("Telegram credentials required when notifications are enabled")
        
        # Validate numeric ranges
        if not 0.1 <= self.POSITION_SIZE_PERCENT <= 100:
            raise ValueError("POSITION_SIZE_PERCENT must be between 0.1 and 100")
        
        if self.MAX_LEVERAGE < 1:
            raise ValueError("MAX_LEVERAGE must be at least 1")
        
        if not 0 < self.CORRELATION_THRESHOLD <= 1:
            raise ValueError("CORRELATION_THRESHOLD must be between 0 and 1")
        
        # Validate mode settings
        if self.TRADING_MODE not in ['paper', 'simulation', 'live']:
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        
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
