"""
Dynamic Logging System
Category-based logging with zero-overhead when disabled
"""

import logging
import logging.handlers
import os
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from pathlib import Path


class LogCategory(Enum):
    """Log categories that can be independently enabled/disabled"""
    API_CALLS = "api_calls"
    POSITION_REJECTIONS = "position_rejections"
    TOKEN_METRICS = "token_metrics"
    RISK_MANAGEMENT = "risk_management"
    TRADE_EXECUTION = "trade_execution"
    PERFORMANCE = "performance"
    SYSTEM_EVENTS = "system_events"
    ERROR_TRACES = "error_traces"


class Logger:
    """
    Dynamic logging system with category-based control
    Zero overhead when categories are disabled
    """
    
    def __init__(self, config):
        """
        Initialize logger with configuration
        
        Args:
            config: Configuration object
        """
        self.config = config
        self._loggers: Dict[str, logging.Logger] = {}
        self._category_states: Dict[LogCategory, bool] = {}
        
        # Initialize logging infrastructure
        self._setup_logging()
        
        # Load category states from configuration
        self._load_category_states()
    
    def _setup_logging(self):
        """Setup logging infrastructure"""
        
        # Create logs directory if needed
        if self.config.LOG_OUTPUT in ['file', 'both']:
            os.makedirs(self.config.LOG_FILE_PATH, exist_ok=True)
        
        # Get log level
        log_level = getattr(logging, self.config.LOG_LEVEL, logging.INFO)
        
        # Create main logger
        main_logger = logging.getLogger('apex_hunter')
        main_logger.setLevel(log_level)
        main_logger.handlers = []  # Clear existing handlers
        
        # Create formatters
        detailed_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        simple_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # Add console handler
        if self.config.LOG_OUTPUT in ['console', 'both']:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(log_level)
            console_handler.setFormatter(simple_formatter)
            main_logger.addHandler(console_handler)
        
        # Add file handler with rotation
        if self.config.LOG_OUTPUT in ['file', 'both']:
            log_file = Path(self.config.LOG_FILE_PATH) / f"apex_hunter_{datetime.now().strftime('%Y%m%d')}.log"
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=self.config.LOG_FILE_MAX_SIZE * 1024 * 1024,  # Convert MB to bytes
                backupCount=self.config.LOG_FILE_BACKUP_COUNT
            )
            file_handler.setLevel(log_level)
            file_handler.setFormatter(detailed_formatter)
            main_logger.addHandler(file_handler)
        
        self._loggers['main'] = main_logger
        
        # Create category-specific loggers
        for category in LogCategory:
            cat_logger = logging.getLogger(f'apex_hunter.{category.value}')
            cat_logger.setLevel(log_level)
            cat_logger.propagate = True  # Inherit handlers from parent
            self._loggers[category.value] = cat_logger
    
    def _load_category_states(self):
        """Load category enable/disable states from configuration"""
        self._category_states = {
            LogCategory.API_CALLS: self.config.LOG_API_CALLS,
            LogCategory.POSITION_REJECTIONS: self.config.LOG_POSITION_REJECTIONS,
            LogCategory.TOKEN_METRICS: self.config.LOG_TOKEN_METRICS,
            LogCategory.RISK_MANAGEMENT: self.config.LOG_RISK_MANAGEMENT,
            LogCategory.TRADE_EXECUTION: self.config.LOG_TRADE_EXECUTION,
            LogCategory.PERFORMANCE: self.config.LOG_PERFORMANCE,
            LogCategory.SYSTEM_EVENTS: self.config.LOG_SYSTEM_EVENTS,
            LogCategory.ERROR_TRACES: True,  # Always enabled
        }
    
    def is_enabled(self, category: LogCategory) -> bool:
        """
        Check if a log category is enabled
        
        Args:
            category: Log category to check
        
        Returns:
            True if category is enabled
        """
        return self._category_states.get(category, False)
    
    def enable_category(self, category: LogCategory):
        """Enable a log category"""
        self._category_states[category] = True
        self.system(f"Enabled logging category: {category.value}")
    
    def disable_category(self, category: LogCategory):
        """Disable a log category (except ERROR_TRACES)"""
        if category == LogCategory.ERROR_TRACES:
            self.warning("Cannot disable ERROR_TRACES category")
            return
        
        self._category_states[category] = False
        self.system(f"Disabled logging category: {category.value}")
    
    def get_category_status(self) -> Dict[str, bool]:
        """Get status of all log categories"""
        return {cat.value: enabled for cat, enabled in self._category_states.items()}
    
    def _log(self, category: LogCategory, level: int, message: str, **kwargs):
        """
        Internal logging method with category check
        
        Args:
            category: Log category
            level: Logging level
            message: Log message
            **kwargs: Additional context
        """
        # Check if category is enabled (zero-overhead check)
        if not self._category_states.get(category, False):
            return
        
        # Get logger for this category
        logger = self._loggers.get(category.value, self._loggers['main'])
        
        # Format message with context
        if kwargs:
            context_str = " | ".join([f"{k}={v}" for k, v in kwargs.items()])
            message = f"{message} | {context_str}"
        
        # Log the message
        logger.log(level, message)
    
    # ===== Category-specific logging methods =====
    
    def api_call(self, method: str, url: str, status: Optional[int] = None, duration: Optional[float] = None, **kwargs):
        """Log API call details"""
        self._log(
            LogCategory.API_CALLS,
            logging.INFO,
            f"API Call: {method} {url}",
            status=status,
            duration_ms=f"{duration*1000:.2f}" if duration else None,
            **kwargs
        )
    
    def position_rejected(self, symbol: str, reason: str, layer: str, **kwargs):
        """Log rejected position with reason"""
        self._log(
            LogCategory.POSITION_REJECTIONS,
            logging.WARNING,
            f"Position Rejected: {symbol}",
            reason=reason,
            layer=layer,
            **kwargs
        )
    
    def token_usage(self, endpoint: str, tokens: int, total: int):
        """Log API token usage"""
        self._log(
            LogCategory.TOKEN_METRICS,
            logging.DEBUG,
            f"Token Usage: {endpoint}",
            tokens=tokens,
            total=total
        )
    
    def risk_layer_triggered(self, layer: str, reason: str, action: str, **kwargs):
        """Log risk layer activation"""
        self._log(
            LogCategory.RISK_MANAGEMENT,
            logging.WARNING,
            f"Risk Layer Triggered: {layer}",
            reason=reason,
            action=action,
            **kwargs
        )
    
    def trade_entry(self, symbol: str, side: str, size: float, price: float, leverage: int, **kwargs):
        """Log trade entry"""
        self._log(
            LogCategory.TRADE_EXECUTION,
            logging.INFO,
            f"Trade Entry: {side} {symbol}",
            size=size,
            price=price,
            leverage=leverage,
            **kwargs
        )
    
    def trade_exit(self, symbol: str, pnl: float, pnl_percent: float, duration: str, **kwargs):
        """Log trade exit"""
        self._log(
            LogCategory.TRADE_EXECUTION,
            logging.INFO,
            f"Trade Exit: {symbol}",
            pnl=f"{pnl:+.2f} USDT",
            pnl_percent=f"{pnl_percent:+.2f}%",
            duration=duration,
            **kwargs
        )
    
    def performance_update(self, total_pnl: float, win_rate: float, total_trades: int, **kwargs):
        """Log performance metrics"""
        self._log(
            LogCategory.PERFORMANCE,
            logging.INFO,
            "Performance Update",
            total_pnl=f"{total_pnl:+.2f} USDT",
            win_rate=f"{win_rate:.1f}%",
            trades=total_trades,
            **kwargs
        )
    
    def system(self, message: str, **kwargs):
        """Log system event"""
        self._log(
            LogCategory.SYSTEM_EVENTS,
            logging.INFO,
            message,
            **kwargs
        )
    
    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Log error (always enabled)"""
        self._log(
            LogCategory.ERROR_TRACES,
            logging.ERROR,
            message,
            **kwargs
        )
        
        # Log exception info if provided
        if exc_info:
            self._loggers.get('main', logging.getLogger('apex_hunter')).exception(message)
    
    # ===== Standard logging methods =====
    
    def debug(self, message: str, **kwargs):
        """Debug level log"""
        logger = self._loggers.get('main', logging.getLogger('apex_hunter'))
        logger.debug(f"{message} | {kwargs}" if kwargs else message)
    
    def info(self, message: str, **kwargs):
        """Info level log"""
        logger = self._loggers.get('main', logging.getLogger('apex_hunter'))
        logger.info(f"{message} | {kwargs}" if kwargs else message)
    
    def warning(self, message: str, **kwargs):
        """Warning level log"""
        logger = self._loggers.get('main', logging.getLogger('apex_hunter'))
        logger.warning(f"{message} | {kwargs}" if kwargs else message)
    
    def critical(self, message: str, **kwargs):
        """Critical level log"""
        logger = self._loggers.get('main', logging.getLogger('apex_hunter'))
        logger.critical(f"{message} | {kwargs}" if kwargs else message)
