"""
Spot Trading Engine - Complete Paper Trading for Spot Markets
Similar to futures engine but without leverage, with trailing stops and take profit
"""

import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional, Tuple


class SpotTradingEngine:
    """
    Spot Trading Engine - Full paper trading simulation for spot markets
    No leverage, but complete position management like futures engine
    """

    def __init__(self, config, logger, telegram, risk_manager):
        self.config = config
        self.logger = logger
        self.telegram = telegram
        self.risk_manager = risk_manager

        # Virtual capital (spot - no leverage)
        self.virtual_capital = getattr(config, 'SPOT_VIRTUAL_CAPITAL', 100)
        self.virtual_balance = self.virtual_capital
        self.peak_balance = self.virtual_capital

        # Positions tracking (like futures)
        self.positions: Dict[str, Dict] = {}

        # Performance tracking
        self.trades = []

        # Import strategies for signal generation
        from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5
        from strategies.filters import get_strategy_filters

        # Initialize strategies with universal filters
        self.strategies = []
        self.filters = get_strategy_filters(config)

        if hasattr(config, 'STRATEGY_A1_ENABLED') and config.STRATEGY_A1_ENABLED:
            self.strategies.append(StrategyA1(config, logger))
        if hasattr(config, 'STRATEGY_A2_ENABLED') and config.STRATEGY_A2_ENABLED:
            self.strategies.append(StrategyA2(config, logger))
        if hasattr(config, 'STRATEGY_A3_ENABLED') and config.STRATEGY_A3_ENABLED:
            self.strategies.append(StrategyA3(config, logger))
        if hasattr(config, 'STRATEGY_A4_ENABLED') and config.STRATEGY_A4_ENABLED:
            self.strategies.append(StrategyA4(config, logger))
        if hasattr(config, 'STRATEGY_A5_ENABLED') and config.STRATEGY_A5_ENABLED:
            self.strategies.append(StrategyA5(config, logger))

        # Fallback to all strategies including A5
        if not self.strategies:
            self.strategies = [
                StrategyA1(config, logger),
                StrategyA2(config, logger),
                StrategyA3(config, logger),
                StrategyA4(config, logger),
                StrategyA5(config, logger)
            ]

        self.logger.info(f"Spot trading initialized with {len(self.strategies)} strategies")
        self.logger.info(f"Virtual capital: ${self.virtual_capital}")
        self.logger.info(f"Exchange: {config.SPOT_EXCHANGE}")

    def fetch_market_data(self, symbol='BTC/USDT', timeframe='15m', limit=200):
        """Fetch spot market data using CCXT (exchange-agnostic)"""
        try:
            import ccxt

            # Get exchange class dynamically
            exchange_class = getattr(ccxt, self.config.SPOT_EXCHANGE.lower())
            exchange = exchange_class()

            # Fetch OHLCV data
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            return df
        except Exception as e:
            self.logger.error(f"Error fetching spot data for {symbol}: {e}")
            return None

    def run_cycle(self, symbol: str):
        """Run one complete trading cycle for spot symbol"""
        # Fetch market data
        df = self.fetch_market_data(symbol)
        if df is None or len(df) < 25:
            self.logger.debug(f"Insufficient spot data for {symbol}")
            return

        current_price = df.iloc[-1]['close']

        # Update trailing stops for existing positions
        self._update_trailing_stops(symbol, current_price)

        # Update trailing take profit for existing positions
        self._update_trailing_take_profit(symbol, current_price)

        # Check exits for existing positions
        self._check_exits(symbol, current_price)

        # Generate new signals
        for strategy in self.strategies:
            position_key = f"{strategy.name}:{symbol}"

            # Skip if strategy already has open position for this symbol
            if position_key in self.positions:
                continue

            # Generate signal using same strategy logic
            # CRITICAL FIX: Pass symbol and market_type
            if strategy.name.startswith("A5"):
                signal = strategy.generate_signal(df, symbol=symbol, market_type='spot')
            else:
                signal = strategy.generate_signal(df, symbol=symbol)

            if signal:
                self._execute_spot_trade(signal, strategy.name, symbol)

        # Log status
        total_pnl = self.virtual_balance - self.virtual_capital
        open_positions = len([p for p in self.positions.values() if p['symbol'] == symbol])

        self.logger.info(f"SPOT {symbol} | Price: ${current_price:.2f} | "
                        f"Open: {open_positions} | Balance: ${self.virtual_balance:.2f} | P&L: ${total_pnl:+.2f}")

    def _execute_spot_trade(self, signal: Dict, strategy_name: str, symbol: str):
        """Execute spot trade (no leverage)"""
        position_key = f"{strategy_name}:{symbol}"

        # Calculate position size (spot - no leverage)
        position_size_usdt = self.virtual_balance * (self.config.SPOT_POSITION_SIZE_PERCENT / 100)

        # Prepare trade parameters for risk evaluation
        trade_params = {
            'symbol': symbol,
            'side': signal['side'],
            'entry_price': signal['entry_price'],
            'position_size': position_size_usdt,
            'leverage': 1,  # Spot - no leverage
            'stop_loss': signal.get('stop_loss'),
            'take_profit': signal.get('take_profit'),
            'strategy': strategy_name
        }

        # Prepare account state for risk evaluation
        account_state = {
            'balance': self.virtual_balance,
            'peak_balance': self.peak_balance,
            'open_positions': list(self.positions.values()),
            'recent_trades': self.trades[-20:],  # Last 20 trades
            'current_time': datetime.now()
        }

        # Validate through risk management (same 11 layers)
        approved_params = self.risk_manager.evaluate_trade(trade_params, account_state)

        if approved_params is None:
            self.logger.warning(f"SPOT [{strategy_name}] {symbol} TRADE REJECTED by risk management")
            return

        # Create position (spot - no leverage calculations)
        position = {
            'entry_time': datetime.now(),
            'entry_price': approved_params['entry_price'],
            'side': approved_params['side'],
            'stop_loss': approved_params.get('stop_loss'),
            'take_profit': approved_params.get('take_profit'),
            'size_usdt': approved_params['position_size'],
            'strategy': strategy_name,
            'symbol': symbol,
            # Trailing stop tracking
            'trailing_stop_active': False,
            'highest_price': approved_params['entry_price'] if approved_params['side'] == 'buy' else None,
            'lowest_price': approved_params['entry_price'] if approved_params['side'] == 'sell' else None,
            'trailing_activation_price': None
        }

        self.positions[position_key] = position

        self.logger.info(f"SPOT [{strategy_name}] {symbol} ENTRY {signal['side'].upper()} @ ${signal['entry_price']:.2f} ✅ Risk Approved")

        # Telegram notification
        if self.telegram and hasattr(self.telegram, 'send_spot_trade_entry'):
            self.telegram.send_spot_trade_entry({
                'symbol': symbol,
                'side': signal['side'],
                'entry_price': signal['entry_price'],
                'size': position['size_usdt'],
                'stop_loss': signal.get('stop_loss'),
                'take_profit': signal.get('take_profit'),
                'strategy': strategy_name,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            })

    def _update_trailing_stops(self, symbol: str, current_price: float):
        """Update trailing stops for spot positions (no leverage)"""
        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            strategy_name = position['strategy']
            trailing_distance = self.config.TRAILING_STOP_DISTANCE / 100

            if position['side'] == 'buy':
                # Track highest price for long positions
                if position.get('highest_price') is None or current_price > position['highest_price']:
                    position['highest_price'] = current_price

                    # Check if trailing stop should activate
                    profit_pct = (current_price - position['entry_price']) / position['entry_price']
                    activation_threshold = self.config.TRAILING_STOP_ACTIVATION / 100

                    if profit_pct >= activation_threshold and not position.get('trailing_stop_active', False):
                        # Activate trailing stop
                        position['trailing_stop_active'] = True
                        new_stop = current_price * (1 - trailing_distance)

                        if new_stop > position.get('stop_loss', 0):
                            position['stop_loss'] = new_stop
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING STOP ACTIVATED @ ${new_stop:.2f}")

                            # Telegram notification
                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_stop_update'):
                                self.telegram.send_spot_trailing_stop_update({
                                    'type': 'activated',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'new_stop_loss': new_stop
                                })

                    # Update active trailing stop
                    elif position.get('trailing_stop_active', False):
                        new_stop = position['highest_price'] * (1 - trailing_distance)
                        if new_stop > position.get('stop_loss', 0):
                            old_stop = position['stop_loss']
                            position['stop_loss'] = new_stop
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING STOP UPDATED ${old_stop:.2f} → ${new_stop:.2f}")

                            # Telegram notification
                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_stop_update'):
                                profit_pct = (current_price - position['entry_price']) / position['entry_price']
                                self.telegram.send_spot_trailing_stop_update({
                                    'type': 'update',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'old_stop_loss': old_stop,
                                    'new_stop_loss': new_stop
                                })

    def _update_trailing_take_profit(self, symbol: str, current_price: float):
        """
        Update trailing take profit for spot positions.

        Trailing TP locks in profits by moving TP closer to current price as trade profits.
        - For LONG: TP moves DOWN (closer) as price rises
        - For SHORT: TP moves UP (closer) as price falls
        """
        if not getattr(self.config, 'TRAILING_TP_ENABLED', True):
            return

        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            strategy_name = position['strategy']
            tp_activation = getattr(self.config, 'TRAILING_TP_ACTIVATION', 3) / 100
            tp_distance = getattr(self.config, 'TRAILING_TP_DISTANCE', 1.5) / 100

            if position['side'] == 'buy':
                profit_pct = (current_price - position['entry_price']) / position['entry_price']

                # Initialize trailing TP tracking
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_peak'] = None

                # Track peak price
                if position['trailing_tp_peak'] is None or current_price > position['trailing_tp_peak']:
                    position['trailing_tp_peak'] = current_price

                    # Activate trailing TP
                    if profit_pct >= tp_activation and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        old_tp = position.get('take_profit', float('inf'))
                        new_tp = current_price * (1 - tp_distance)

                        if new_tp < old_tp and new_tp > position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${new_tp:.2f} "
                                           f"(Profit: {profit_pct*100:.1f}%)")

                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_tp_update'):
                                self.telegram.send_spot_trailing_tp_update({
                                    'type': 'activated',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'old_take_profit': old_tp,
                                    'new_take_profit': new_tp
                                })

                    # Update active trailing TP
                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_peak'] * (1 - tp_distance)
                        current_tp = position.get('take_profit', float('inf'))

                        if new_tp < current_tp and new_tp > position['entry_price']:
                            old_tp = current_tp
                            position['take_profit'] = new_tp
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING TP UPDATED ${old_tp:.2f} → ${new_tp:.2f}")

                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_tp_update'):
                                self.telegram.send_spot_trailing_tp_update({
                                    'type': 'update',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'old_take_profit': old_tp,
                                    'new_take_profit': new_tp
                                })

            else:  # sell position (short)
                profit_pct = (position['entry_price'] - current_price) / position['entry_price']

                # Initialize trailing TP tracking
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_trough'] = None

                # Track trough (lowest) price
                if position['trailing_tp_trough'] is None or current_price < position['trailing_tp_trough']:
                    position['trailing_tp_trough'] = current_price

                    # Activate trailing TP
                    if profit_pct >= tp_activation and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        old_tp = position.get('take_profit', 0)
                        new_tp = current_price * (1 + tp_distance)

                        if new_tp > old_tp and new_tp < position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${new_tp:.2f} "
                                           f"(Profit: {profit_pct*100:.1f}%)")

                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_tp_update'):
                                self.telegram.send_spot_trailing_tp_update({
                                    'type': 'activated',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'old_take_profit': old_tp,
                                    'new_take_profit': new_tp
                                })

                    # Update active trailing TP
                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_trough'] * (1 + tp_distance)
                        current_tp = position.get('take_profit', 0)

                        if new_tp > current_tp and new_tp < position['entry_price']:
                            old_tp = current_tp
                            position['take_profit'] = new_tp
                            self.logger.info(f"SPOT [{strategy_name}] {symbol} TRAILING TP UPDATED ${old_tp:.2f} → ${new_tp:.2f}")

                            if self.telegram and hasattr(self.telegram, 'send_spot_trailing_tp_update'):
                                self.telegram.send_spot_trailing_tp_update({
                                    'type': 'update',
                                    'symbol': symbol,
                                    'current_price': current_price,
                                    'strategy': strategy_name,
                                    'profit_percent': profit_pct * 100,
                                    'old_take_profit': old_tp,
                                    'new_take_profit': new_tp
                                })

    def _check_exits(self, symbol: str, current_price: float):
        """Check if spot positions should be closed"""
        positions_to_close = []

        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            should_exit, reason = self._check_position_exit(position, current_price)

            if should_exit:
                self._close_position(position_key, position, current_price, reason)
                positions_to_close.append(position_key)

        # Remove closed positions
        for key in positions_to_close:
            del self.positions[key]

    def _check_position_exit(self, position: Dict, current_price: float) -> Tuple[bool, str]:
        """Check if position should exit (stop loss or take profit)"""
        if position['side'] == 'buy':
            if current_price <= position.get('stop_loss', 0):
                return True, 'stop_loss'
            elif current_price >= position.get('take_profit', float('inf')):
                return True, 'take_profit'
        else:  # sell position
            if current_price >= position.get('stop_loss', float('inf')):
                return True, 'stop_loss'
            elif current_price <= position.get('take_profit', 0):
                return True, 'take_profit'

        return False, ''

    def _close_position(self, position_key: str, position: Dict, exit_price: float, reason: str):
        """Close position and calculate P&L"""
        strategy_name = position['strategy']
        symbol = position['symbol']
        entry_price = position['entry_price']
        size_usdt = position['size_usdt']
        side = position['side']

        # Calculate P&L (spot - no leverage)
        if side == 'buy':
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price

        pnl_usdt = size_usdt * pnl_pct

        # Update balance
        self.virtual_balance += pnl_usdt

        # Update peak balance
        if self.virtual_balance > self.peak_balance:
            self.peak_balance = self.virtual_balance

        # Record trade
        trade = {
            'entry_time': position['entry_time'],
            'exit_time': datetime.now(),
            'strategy': strategy_name,
            'symbol': symbol,
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl_usdt': pnl_usdt,
            'pnl_pct': pnl_pct * 100,
            'reason': reason,
            'balance_after': self.virtual_balance
        }

        self.trades.append(trade)

        self.logger.info(f"SPOT [{strategy_name}] {symbol} EXIT {reason.upper()} @ ${exit_price:.2f}, P&L: ${pnl_usdt:+.2f} ({pnl_pct*100:+.2f}%)")

        # Telegram notification
        if self.telegram and hasattr(self.telegram, 'send_spot_trade_exit'):
            duration = (trade['exit_time'] - trade['entry_time']).seconds // 60
            self.telegram.send_spot_trade_exit({
                'symbol': symbol,
                'exit_price': exit_price,
                'pnl': pnl_usdt,
                'pnl_pct': pnl_pct * 100,
                'duration': f"{duration} minutes",
                'strategy': strategy_name,
                'reason': reason
            })

    def get_status(self) -> Dict:
        """Get current spot trading status"""
        total_pnl = self.virtual_balance - self.virtual_capital
        drawdown = ((self.peak_balance - self.virtual_balance) / self.peak_balance) * 100 if self.peak_balance > 0 else 0

        return {
            'balance': self.virtual_balance,
            'peak_balance': self.peak_balance,
            'total_pnl': total_pnl,
            'pnl_pct': (total_pnl / self.virtual_capital) * 100,
            'drawdown': drawdown,
            'open_positions': len(self.positions),
            'total_trades': len(self.trades),
            'win_rate': len([t for t in self.trades if t['pnl_usdt'] > 0]) / len(self.trades) * 100 if self.trades else 0
        }

    def print_summary(self):
        """Print spot trading summary"""
        print("\n" + "=" * 80)
        print("  SPOT TRADING SUMMARY")
        print("=" * 80)

        if not self.trades:
            print("\n  No spot trades executed.")
            print("\n" + "=" * 80 + "\n")
            return

        total_pnl = self.virtual_balance - self.virtual_capital
        wins = [t for t in self.trades if t['pnl_usdt'] > 0]
        win_rate = len(wins) / len(self.trades) * 100

        print(f"\n  Virtual Capital: ${self.virtual_capital:.2f}")
        print(f"  Final Balance: ${self.virtual_balance:.2f}")
        print(f"  Total P&L: ${total_pnl:+.2f} ({total_pnl/self.virtual_capital*100:+.2f}%)")
        print(f"  Total Trades: {len(self.trades)}")
        print(f"  Win Rate: {win_rate:.1f}%")
        print(f"  Max Drawdown: {((self.peak_balance - min([self.virtual_capital] + [t['balance_after'] for t in self.trades])) / self.peak_balance * 100):.1f}%")

        print("\n" + "=" * 80 + "\n")
