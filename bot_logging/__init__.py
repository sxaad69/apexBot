"""
Bot Logging Module
Dynamic, category-based logging system with zero overhead when disabled
Note: Named 'bot_logging' to avoid conflict with Python's built-in 'logging' module
"""

from .logger import Logger, LogCategory

__all__ = ['Logger', 'LogCategory']
