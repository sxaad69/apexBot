#!/usr/bin/env python3
"""
APEX HUNTER V14 - Standalone Backtest Script
Backtest trading strategies on historical data

Usage:
    python backtest.py                           # Run all strategies, last 30 days
    python backtest.py --strategy A2             # Test specific strategy
    python backtest.py --days 90                 # Test last 90 days
    python backtest.py --start 2024-01-01 --end 2024-06-30
    python backtest.py --symbol ETH/USDT         # Test different pair
"""

import sys
import argparse
from datetime import datetime, timedelta
import pandas as pd
import ccxt
from config import Config
from bot_logging import Logger
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4


class Backtester:
    """Simple but functional backtester"""
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.exchange = ccxt.binance()  # Use Binance for historical data
    
    def fetch_ohlcv(self, symbol, timeframe='15m', since=None, limit=1000):
        """Fetch historical OHLCV data"""
        try:
            self.logger.info(f"Fetching {symbol} data...")
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, since, limit)
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
            return df
        except Exception as e:
            self.logger.error(f"Error fetching data: {e}")
            return None
    
    def run_backtest(self, strategy, df, initial_capital=100):
        """
        Run backtest for a strategy
        
        Returns:
            dict: Backtest results
        """
        trades = []
        capital = initial_capital
        position = None
        
        self.logger.info(f"Running backtest for {strategy.name}...")
        
        for i in range(len(df)):
            if i < 60:  # Need enough data for indicators
                continue
            
            current_data = df.iloc[:i+1]
            signal = strategy.generate_signal(current_data)
            
            # Entry logic
            if signal and position is None:
                position = {
                    'entry_time': df.index[i],
                    'entry_price': signal['entry_price'],
                    'side': signal['side'],
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'size': capital * 0.1  # 10% of capital per trade
                }
                self.logger.debug(f"Entry: {signal['side']} @ {signal['entry_price']:.2f}")
            
            # Exit logic
            elif position:
                current_price = df.iloc[i]['close']
                exit_triggered = False
                exit_reason = None
                
                if position['side'] == 'buy':
                    if current_price <= position['stop_loss']:
                        exit_triggered = True
                        exit_reason = 'stop_loss'
                    elif current_price >= position['take_profit']:
                        exit_triggered = True
                        exit_reason = 'take_profit'
                else:  # sell
                    if current_price >= position['stop_loss']:
                        exit_triggered = True
                        exit_reason = 'stop_loss'
                    elif current_price <= position['take_profit']:
                        exit_triggered = True
                        exit_reason = 'take_profit'
                
                if exit_triggered:
                    # Calculate P&L
                    if position['side'] == 'buy':
                        pnl = (current_price - position['entry_price']) / position['entry_price']
                    else:
                        pnl = (position['entry_price'] - current_price) / position['entry_price']
                    
                    pnl_amount = position['size'] * pnl
                    capital += pnl_amount
                    
                    trade = {
                        'entry_time': position['entry_time'],
                        'exit_time': df.index[i],
                        'side': position['side'],
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'pnl': pnl_amount,
                        'pnl_percent': pnl * 100,
                        'reason': exit_reason,
                        'capital_after': capital
                    }
                    
                    trades.append(trade)
                    self.logger.debug(f"Exit: {exit_reason} @ {current_price:.2f}, P&L: {pnl_amount:+.2f}")
                    position = None
        
        # Calculate metrics
        if trades:
            wins = [t for t in trades if t['pnl'] > 0]
            losses = [t for t in trades if t['pnl'] <= 0]
            
            total_pnl = sum(t['pnl'] for t in trades)
            win_rate = len(wins) / len(trades) * 100 if trades else 0
            
            avg_win = sum(t['pnl'] for t in wins) / len(wins) if wins else 0
            avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0
            
            # Calculate max drawdown
            equity_curve = [initial_capital]
            for trade in trades:
                equity_curve.append(trade['capital_after'])
            
            peak = equity_curve[0]
            max_dd = 0
            for equity in equity_curve:
                if equity > peak:
                    peak = equity
                dd = (peak - equity) / peak * 100
                if dd > max_dd:
                    max_dd = dd
            
            results = {
                'strategy': strategy.name,
                'total_trades': len(trades),
                'wins': len(wins),
                'losses': len(losses),
                'win_rate': win_rate,
                'total_pnl': total_pnl,
                'total_return': (capital - initial_capital) / initial_capital * 100,
                'final_capital': capital,
                'avg_win': avg_win,
                'avg_loss': avg_loss,
                'profit_factor': abs(avg_win / avg_loss) if avg_loss != 0 else 0,
                'max_drawdown': max_dd,
                'trades': trades
            }
        else:
            results = {
                'strategy': strategy.name,
                'total_trades': 0,
                'wins': 0,
                'losses': 0,
                'win_rate': 0,
                'total_pnl': 0,
                'total_return': 0,
                'final_capital': initial_capital,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'max_drawdown': 0,
                'trades': []
            }
        
        return results


def print_results(results):
    """Print backtest results in a nice format"""
    print("\n" + "=" * 80)
    print(f"  BACKTEST RESULTS: {results['strategy']}")
    print("=" * 80)
    print()
    print(f"  Total Trades:     {results['total_trades']}")
    print(f"  Wins:             {results['wins']} ({results['win_rate']:.1f}%)")
    print(f"  Losses:           {results['losses']}")
    print()
    print(f"  Total Return:     {results['total_return']:+.2f}%")
    print(f"  Final Capital:    ${results['final_capital']:.2f}")
    print(f"  Total P&L:        ${results['total_pnl']:+.2f}")
    print()
    print(f"  Average Win:      ${results['avg_win']:.2f}")
    print(f"  Average Loss:     ${results['avg_loss']:.2f}")
    print(f"  Profit Factor:    {results['profit_factor']:.2f}")
    print(f"  Max Drawdown:     {results['max_drawdown']:.2f}%")
    print()


def main():
    parser = argparse.ArgumentParser(description='Backtest trading strategies')
    parser.add_argument('--strategy', type=str, choices=['A1', 'A2', 'A3', 'A4', 'all'], default='all',
                        help='Strategy to test (default: all)')
    parser.add_argument('--symbol', type=str, default='BTC/USDT',
                        help='Trading pair (default: BTC/USDT)')
    parser.add_argument('--days', type=int, default=30,
                        help='Number of days to test (default: 30)')
    parser.add_argument('--start', type=str,
                        help='Start date (YYYY-MM-DD)')
    parser.add_argument('--end', type=str,
                        help='End date (YYYY-MM-DD)')
    parser.add_argument('--capital', type=float, default=100,
                        help='Initial capital (default: 100)')
    
    args = parser.parse_args()
    
    print("=" * 80)
    print("  APEX HUNTER V14 - BACKTEST")
    print("=" * 80)
    print()
    
    # Load config
    try:
        config = Config()
        logger = Logger(config)
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        sys.exit(1)
    
    # Calculate date range
    if args.start and args.end:
        since = int(datetime.strptime(args.start, '%Y-%m-%d').timestamp() * 1000)
        end_date = datetime.strptime(args.end, '%Y-%m-%d')
        days = (end_date - datetime.strptime(args.start, '%Y-%m-%d')).days
    else:
        since = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        days = args.days
    
    print(f"  Symbol:           {args.symbol}")
    print(f"  Period:           {days} days")
    print(f"  Initial Capital:  ${args.capital:.2f}")
    print(f"  Strategy:         {args.strategy}")
    print()
    
    # Initialize backtester
    backtester = Backtester(config, logger)
    
    # Fetch data
    df = backtester.fetch_ohlcv(args.symbol, since=since, limit=days*96)  # 15m candles
    
    if df is None or len(df) == 0:
        print("❌ Failed to fetch historical data")
        sys.exit(1)
    
    print(f"✅ Fetched {len(df)} candles")
    print()
    
    # Initialize strategies
    strategies = []
    if args.strategy == 'all' or args.strategy == 'A1':
        strategies.append(StrategyA1(config, logger))
    if args.strategy == 'all' or args.strategy == 'A2':
        strategies.append(StrategyA2(config, logger))
    if args.strategy == 'all' or args.strategy == 'A3':
        strategies.append(StrategyA3(config, logger))
    if args.strategy == 'all' or args.strategy == 'A4':
        strategies.append(StrategyA4(config, logger))
    
    # Run backtests
    all_results = []
    for strategy in strategies:
        results = backtester.run_backtest(strategy, df, args.capital)
        all_results.append(results)
        print_results(results)
    
    # Comparison
    if len(all_results) > 1:
        print("=" * 80)
        print("  STRATEGY COMPARISON")
        print("=" * 80)
        print()
        print(f"  {'Strategy':<20} {'Trades':<8} {'Win%':<8} {'Return':<12} {'Max DD':<10}")
        print("  " + "-" * 70)
        
        for r in sorted(all_results, key=lambda x: x['total_return'], reverse=True):
            print(f"  {r['strategy']:<20} {r['total_trades']:<8} {r['win_rate']:<7.1f}% "
                  f"{r['total_return']:>+10.2f}%  {r['max_drawdown']:>8.2f}%")
        
        print()
        print(f"  🏆 Best Strategy: {max(all_results, key=lambda x: x['total_return'])['strategy']}")
        print()


if __name__ == "__main__":
    main()
