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
import os
import time
from datetime import datetime, timedelta
import pandas as pd
import ccxt
from config import Config
from bot_logging import Logger
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4


from risk.layers.leverage_control import LeverageControlLayer
from risk.layers.stop_loss_management import StopLossManagementLayer

class Backtester:
    """Enhanced backtester tracking dynamic leverage, risk limits, and fees"""
    
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
        self.exchange = ccxt.binance()  # Use Binance for historical data
        self.leverage_layer = LeverageControlLayer(self.config, self.logger)
        self.stop_loss_layer = StopLossManagementLayer(self.config, self.logger)
    
    def fetch_ohlcv(self, symbol, timeframe='15m', since=None, limit=1000):
        """Fetch historical OHLCV data from local SQLite cache or fallback to Binance"""
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'apex_hunter.db')
        
        # 1. Try SQLite Database first
        if os.path.exists(db_path):
            try:
                import sqlite3
                conn = sqlite3.connect(db_path)
                
                query = f"""
                SELECT timestamp, open, high, low, close, volume 
                FROM historical_data 
                WHERE symbol = ? AND timeframe = ? AND timestamp >= ?
                ORDER BY timestamp ASC LIMIT ?
                """
                df = pd.read_sql_query(query, conn, params=(symbol, timeframe, since, limit))
                conn.close()
                
                if not df.empty:
                    # self.logger.info(f"Loaded {len(df)} {timeframe} candles for {symbol} from SQLite.")
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)
                    return df
            except Exception as e:
                self.logger.error(f"SQLite fetch error: {e}")
                
        # 2. Fallback to API if DB fails or lacks data
        try:
            self.logger.info(f"Fallback: Fetching {symbol} {timeframe} from API...")
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
        Run backtest for a strategy using full risk stack.
        """
        trades = []
        capital = initial_capital
        peak_capital = initial_capital
        position = None
        
        self.logger.info(f"Running backtest for {strategy.name}...")
        
        for i in range(len(df)):
            if i < 60:  # Need enough data for indicators
                continue
            
            current_data = df.iloc[:i+1]
            signal = strategy.generate_signal(current_data)
            
            # Entry logic via Risk Stack
            if signal and position is None:
                confidence = signal.get('confidence', 0.5)
                
                # Confidence-based sizing
                if confidence >= 0.90: base_size_pct = 0.15
                elif confidence >= 0.80: base_size_pct = 0.12
                elif confidence >= 0.70: base_size_pct = 0.10
                else: base_size_pct = 0.07

                trade_params = {
                    'symbol': 'BACKTEST',
                    'strategy': strategy.name,
                    'side': signal['side'],
                    'entry_price': signal['entry_price'],
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'confidence': confidence,
                    'atr': signal.get('indicators', {}).get('atr'),
                    'size': capital * base_size_pct,
                    'leverage': getattr(self.config, 'MAX_LEVERAGE_ABSOLUTE', 20)
                }

                drawdown_percent = ((peak_capital - capital) / peak_capital) * 100 if capital < peak_capital else 0
                account_state = {'drawdown_percent': drawdown_percent, 'total_balance': capital}

                # Evaluate Risk Layers
                trade_params = self.leverage_layer.evaluate(trade_params, account_state)
                if trade_params:
                    trade_params = self.stop_loss_layer.evaluate(trade_params, account_state)
                
                if trade_params:
                    fee_pct = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.05) / 100
                    position = {
                        'entry_time': df.index[i],
                        'entry_price': trade_params['entry_price'],
                        'side': trade_params['side'],
                        'stop_loss': trade_params['stop_loss'],
                        'take_profit': trade_params['take_profit'],
                        'margin': trade_params['size'],
                        'leverage': trade_params.get('leverage', 1),
                        'fee_pct': fee_pct
                    }
                    self.logger.debug(f"Entry: {position['side']} @ {position['entry_price']:.2f}")
            
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
                    # Leverage and fee adjusted P&L
                    if position['side'] == 'buy':
                        price_move = (current_price - position['entry_price']) / position['entry_price']
                    else:
                        price_move = (position['entry_price'] - current_price) / position['entry_price']
                    
                    pnl_raw_percent = price_move * position['leverage']
                    fee_cost_percent = (position['fee_pct'] * 2) * position['leverage']
                    net_pnl_percent = pnl_raw_percent - fee_cost_percent
                    
                    pnl_amount = position['margin'] * net_pnl_percent
                    capital += pnl_amount
                    if capital > peak_capital:
                        peak_capital = capital
                    
                    trade = {
                        'entry_time': position['entry_time'],
                        'exit_time': df.index[i],
                        'side': position['side'],
                        'entry_price': position['entry_price'],
                        'exit_price': current_price,
                        'pnl': pnl_amount,
                        'pnl_percent': net_pnl_percent * 100,
                        'reason': exit_reason,
                        'capital_after': capital,
                        'margin': position['margin'],
                        'leverage': position['leverage']
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
    parser.add_argument('--capital', type=float, default=1000,
                        help='Initial capital (default: 1000)')
    parser.add_argument('--portfolio', action='store_true',
                        help='Run massive backtest across all coins and 5 timeframes')
    
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
    
    # Define scope
    if args.portfolio:
        symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 
                   'ADA/USDT', 'AVAX/USDT', 'DOGE/USDT', 'LINK/USDT', 'MATIC/USDT']
        timeframes = ['15m', '1h']
    else:
        symbols = [args.symbol]
        timeframes = ['15m']
    
    heatmap_results = []

    for tf in timeframes:
        for sym in symbols:
            # How many candles limit? 
            # 1h = 24/day, 30m = 48/day, 15m = 96/day, 5m = 288/day, 3m = 480/day
            tf_mult = {'1h': 24, '30m': 48, '15m': 96, '5m': 288, '3m': 480}[tf]
            limit = days * tf_mult

            df = backtester.fetch_ohlcv(sym, timeframe=tf, since=since, limit=limit)
            if df is None or len(df) == 0:
                print(f"⚠️  Skipping {sym} [{tf}]: No data found.")
                continue

            # Initialize fresh strategies for each run
            strategies = []
            if args.strategy in ['all', 'A1']: strategies.append(StrategyA1(config, logger))
            if args.strategy in ['all', 'A2']: strategies.append(StrategyA2(config, logger))
            if args.strategy in ['all', 'A3']: strategies.append(StrategyA3(config, logger))
            if args.strategy in ['all', 'A4']: strategies.append(StrategyA4(config, logger))

            print(f"\n⏳ Testing {sym} [{tf}] ({len(df)} candles) ...")
            
            for strategy in strategies:
                res = backtester.run_backtest(strategy, df, args.capital)
                res['symbol'] = sym
                res['timeframe'] = tf
                heatmap_results.append(res)
                
                # Only print detailed output if we're not running a massive portfolio test
                if not args.portfolio:
                    print_results(res)

    # Print Final Heatmap
    if len(heatmap_results) > 1:
        print("\n" + "=" * 100)
        print("  PORTFOLIO HEATMAP & STRATEGY COMPARISON")
        print("=" * 100)
        print()
        print(f"  {'Strategy':<20} {'Symbol':<10} {'TF':<5} {'Trades':<8} {'Win%':<8} {'Return':<12} {'Max DD':<10}")
        print("  " + "-" * 88)
        
        for r in sorted(heatmap_results, key=lambda x: x['total_return'], reverse=True):
            print(f"  {r['strategy']:<20} {r['symbol']:<10} {r['timeframe']:<5} {r['total_trades']:<8} "
                  f"{r['win_rate']:<7.1f}% {r['total_return']:>+10.2f}%  {r['max_drawdown']:>8.2f}%")
        
        print()
        best = max(heatmap_results, key=lambda x: x['total_return'])
        print(f"  🏆 Best Combination: {best['strategy']} on {best['symbol']} [{best['timeframe']}] (+{best['total_return']:.2f}%)")
        print()
        
        # Aggregate by Strategy
        print("  Aggregate by Strategy:")
        for s_name in set(r['strategy'] for r in heatmap_results):
            s_results = [r for r in heatmap_results if r['strategy'] == s_name]
            avg_ret = sum(r['total_return'] for r in s_results) / len(s_results)
            avg_win = sum(r['win_rate'] for r in s_results) / len(s_results)
            tot_trades = sum(r['total_trades'] for r in s_results)
            print(f"    {s_name:<20} | Return: {avg_ret:>+8.2f}% | Win: {avg_win:>5.1f}% | Trades: {tot_trades}")
        print()


if __name__ == "__main__":
    main()
