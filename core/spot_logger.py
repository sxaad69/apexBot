"""
Spot Trading Logger
Simulates spot trading strategy without leverage
For strategy validation and comparison with futures
"""

from datetime import datetime
from typing import Dict, List, Optional


class SpotLogger:
    """
    Spot trading simulation logger
    Tracks what would happen without leverage
    """
    
    def __init__(self, config, logger, exchange_client, risk_manager, telegram_bot=None):
        """
        Initialize spot logger
        
        Args:
            config: Configuration object
            logger: Logger instance
            exchange_client: Exchange client for spot
            risk_manager: Risk manager instance
            telegram_bot: Optional Telegram bot
        """
        self.config = config
        self.logger = logger
        self.exchange = exchange_client
        self.risk_manager = risk_manager
        self.telegram = telegram_bot
        
        # Virtual tracking
        self.virtual_capital = config.SPOT_VIRTUAL_CAPITAL
        self.virtual_balance = config.SPOT_VIRTUAL_CAPITAL
        self.peak_balance = config.SPOT_VIRTUAL_CAPITAL
        
        # Positions
        self.virtual_positions: Dict[str, Dict] = {}
        
        # Stats
        self.signals_today = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        
        self.logger.system(f"Spot logger initialized (${self.virtual_capital} virtual capital)")
    
    def process_signal(self, signal: Dict) -> Dict:
        """
        Process a trading signal
        
        Args:
            signal: Trading signal dict with symbol, side, price, etc.
        
        Returns:
            Result dictionary
        """
        symbol = signal['symbol']
        side = signal['side']
        price = signal['price']
        
        # Calculate position size (same as futures for comparison)
        position_size_usdt = self.virtual_balance * (self.config.SPOT_POSITION_SIZE_PERCENT / 100)
        
        # Apply risk management
        account_state = {
            'total_balance': self.virtual_balance,
            'available_balance': self.virtual_balance,
            'drawdown_percent': self._calculate_drawdown(),
            'open_positions_count': len(self.virtual_positions)
        }
        
        trade_params = {
            'symbol': symbol,
            'side': side,
            'entry_price': price,
            'position_size': position_size_usdt
        }
        
        # Evaluate through risk layers
        approved = self.risk_manager.evaluate_trade(trade_params, account_state)
        
        if not approved:
            self.logger.position_rejected(symbol, "Risk layer rejection", "SpotLogger")
            return {'status': 'rejected', 'reason': 'risk_management'}
        
        # Log the signal
        self._log_signal(approved)
        
        # Track virtual position if enabled
        if self.config.SPOT_TRADING_ENABLED == 'yes':
            self._open_virtual_position(approved)
        
        self.signals_today.append(approved)
        
        return {'status': 'logged', 'signal': approved}
    
    def _open_virtual_position(self, signal: Dict):
        """Open a virtual spot position"""
        symbol = signal['symbol']
        
        # Close existing position if any
        if symbol in self.virtual_positions:
            self._close_virtual_position(symbol, signal['entry_price'])
        
        # Open new position
        self.virtual_positions[symbol] = {
            'symbol': symbol,
            'side': signal['side'],
            'entry_price': signal['entry_price'],
            'size_usdt': signal['position_size'],
            'stop_loss': signal.get('stop_loss'),
            'entry_time': datetime.now()
        }
        
        self.logger.info(f"Virtual spot position opened: {symbol} @ ${signal['entry_price']}")
    
    def _close_virtual_position(self, symbol: str, exit_price: float):
        """Close a virtual position and calculate P&L"""
        if symbol not in self.virtual_positions:
            return
        
        position = self.virtual_positions[symbol]
        entry_price = position['entry_price']
        size_usdt = position['size_usdt']
        side = position['side']
        
        # Calculate P&L (no leverage)
        if side == 'buy':
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100
        else:
            pnl_percent = ((entry_price - exit_price) / entry_price) * 100
        
        pnl_usdt = size_usdt * (pnl_percent / 100)
        
        # Update balance
        self.virtual_balance += pnl_usdt
        self.total_pnl += pnl_usdt
        
        # Update stats
        if pnl_usdt > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        # Update peak
        if self.virtual_balance > self.peak_balance:
            self.peak_balance = self.virtual_balance
        
        # Log exit
        duration = datetime.now() - position['entry_time']
        self.logger.trade_exit(
            symbol=symbol,
            pnl=pnl_usdt,
            pnl_percent=pnl_percent,
            duration=str(duration)
        )
        
        # Remove position
        del self.virtual_positions[symbol]
    
    def _log_signal(self, signal: Dict):
        """Log signal to Telegram"""
        if not self.config.SPOT_TELEGRAM_NOTIFICATIONS or not self.telegram:
            return
        
        message = f"""
📍 SPOT SIGNAL (Simulation)

Signal: {signal['side'].upper()} {signal['symbol']}
Price: ${signal['entry_price']:.2f}
Amount: ${signal['position_size']:.2f}
Stop Loss: ${signal.get('stop_loss', 0):.2f} (-{self.config.SPOT_STOP_LOSS_PERCENT}%)

🔮 If Executed:
- Entry: ${signal['entry_price']:.2f}
- Target: ${signal['entry_price'] * (1 + self.config.SPOT_TAKE_PROFIT_PERCENT/100):.2f} (+{self.config.SPOT_TAKE_PROFIT_PERCENT}%)
- Risk: ${signal['position_size'] * (self.config.SPOT_STOP_LOSS_PERCENT/100):.2f}
- Reward: ${signal['position_size'] * (self.config.SPOT_TAKE_PROFIT_PERCENT/100):.2f}

Trading: {'ENABLED ⚠️' if self.config.SPOT_TRADING_ENABLED == 'yes' else 'DISABLED (Log Only)'}
"""
        
        try:
            self.telegram.send_message(message)
        except:
            pass
    
    def _calculate_drawdown(self) -> float:
        """Calculate current drawdown percentage"""
        if self.peak_balance == 0:
            return 0.0
        return ((self.peak_balance - self.virtual_balance) / self.peak_balance) * 100
    
    def get_daily_summary(self) -> Dict:
        """Get daily summary statistics"""
        total_trades = self.wins + self.losses
        win_rate = (self.wins / total_trades * 100) if total_trades > 0 else 0
        
        return {
            'signals': len(self.signals_today),
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': win_rate,
            'total_pnl': self.total_pnl,
            'pnl_percent': (self.total_pnl / self.virtual_capital * 100) if self.virtual_capital > 0 else 0,
            'current_balance': self.virtual_balance,
            'drawdown': self._calculate_drawdown()
        }
    
    def reset_daily_stats(self):
        """Reset daily statistics"""
        self.signals_today = []
        self.total_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.logger.system("Spot logger daily stats reset")
