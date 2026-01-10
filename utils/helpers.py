"""
Utility Helper Functions
Common utility functions used across the application
"""

from datetime import datetime
from typing import Optional


def calculate_position_value(size: float, price: float, leverage: int) -> float:
    """Calculate total position value"""
    return size * price * leverage


def format_timestamp(dt: Optional[datetime] = None) -> str:
    """Format datetime for display"""
    if dt is None:
        dt = datetime.now()
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def calculate_pnl_percent(entry_price: float, exit_price: float, side: str) -> float:
    """Calculate P&L percentage"""
    if side == 'buy':
        return ((exit_price - entry_price) / entry_price) * 100
    else:
        return ((entry_price - exit_price) / entry_price) * 100


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds/60)}m"
    else:
        return f"{seconds/3600:.1f}h"
