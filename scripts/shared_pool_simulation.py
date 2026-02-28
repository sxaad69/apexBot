#!/usr/bin/env python3
import sys
import os
import pandas as pd
import ccxt
from datetime import datetime, timedelta
import uuid

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA6
from bot_logging import Logger

class SharedPoolSimulator:
    def __init__(self, initial_capital=100, max_positions=5):
        self.config = Config()
        self.logger = Logger(self.config)
        self.total_capital = initial_capital
        self.peak_balance = initial_capital
        self.max_positions = max_positions
        self.positions = {} # Shared positions
        self.trades = []
        self.missed_signals = []
        self.strategies = [
            StrategyA1(self.config, self.logger),
            StrategyA2(self.config, self.logger),
            StrategyA3(self.config, self.logger),
            StrategyA4(self.config, self.logger),
            # Strategy A6 logic simulation
        ]
        
    def calculate_pnl(self, position, current_price):
        if position['side'] == 'buy':
            pnl_pct = (current_price - position['entry_price']) / position['entry_price']
        else:
            pnl_pct = (position['entry_price'] - current_price) / position['entry_price']
        
        leveraged_pnl_pct = pnl_pct * position['leverage']
        return leveraged_pnl_pct

    def run_simulation(self, data_map, symbols):
        # Find common timestamps
        all_timestamps = sorted(list(set().union(*(df.index for df in data_map.values()))))
        
        print(f"--- STARTING 8-HOUR SHARED POOL SIMULATION ---")
        print(f"Initial Capital: ${self.total_capital:.2f}")
        print(f"Max Positions: {self.max_positions}")
        print(f"Time Range: {all_timestamps[0]} to {all_timestamps[-1]}")
        print("-" * 50)

        for ts in all_timestamps:
            # 1. Update Exits / Trailing for existing positions
            for pos_key, pos in list(self.positions.items()):
                symbol = pos['symbol']
                if ts not in data_map[symbol].index: continue
                
                curr_price = data_map[symbol].loc[ts, 'close']
                # Calculate P&L for logic checks (PRICE Percentage, Unleveraged)
                if pos['side'] == 'buy':
                    price_pnl_pct = (curr_price - pos['entry_price']) / pos['entry_price']
                else:
                    price_pnl_pct = (pos['entry_price'] - curr_price) / pos['entry_price']

                # Actual Leveraged P&L for Balance calculation
                net_leveraged_pnl = price_pnl_pct * pos['leverage']
                
                # Simple Exit Check (Triggering on Price Movement % as requested)
                exit_reason = None
                if price_pnl_pct <= -0.02: exit_reason = "STOP_LOSS" # 2% Price Move SL
                elif price_pnl_pct >= 0.04: exit_reason = "TAKE_PROFIT" # 4% Price Move TP
                
                if exit_reason:
                    # Close position
                    size = pos['size']
                    profit = size * net_leveraged_pnl
                    self.total_capital += (size + profit)

                    if self.total_capital > self.peak_balance: self.peak_balance = self.total_capital
                    
                    self.trades.append({
                        'symbol': symbol,
                        'strategy': pos['strategy'],
                        'pnl': profit,
                        'pnl_pct': price_pnl_pct * 100,
                        'reason': exit_reason,
                        'time': ts
                    })

                    print(f"[{ts}] EXIT {symbol} ({pos['strategy']}): {exit_reason} | P&L: ${profit:+.2f}")
                    del self.positions[pos_key]

            # 2. Check for New Entries
            if len(self.positions) >= self.max_positions:
                continue

            for symbol in symbols:
                if ts not in data_map[symbol].index: continue
                df_slice = data_map[symbol].loc[:ts]
                if len(df_slice) < 50: continue

                for strat in self.strategies:
                    pos_key = f"{strat.name}:{symbol}"
                    if pos_key in self.positions: continue
                    
                    signal = strat.generate_signal(df_slice)
                    if signal:
                        # Check Capital
                        # We need at least $10 margin to open a trade (Binance minimum simulation)
                        # Position sizing logic: 15% of available or $20 min
                        entry_size = max(20, self.total_capital * 0.15) 
                        
                        if self.total_capital < entry_size:
                            # Track what the price did after we missed this signal
                            future_df = data_map[symbol]
                            try:
                                future_prices = future_df.loc[ts:]['close']
                                if len(future_prices) > 1 and signal:
                                    curr_p = future_prices.iloc[0]
                                    if signal['side'] == 'buy':
                                        best_p = future_prices.max()
                                        best_gain_pct = (best_p - curr_p) / curr_p * 100
                                    else:
                                        best_p = future_prices.min()
                                        best_gain_pct = (curr_p - best_p) / curr_p * 100
                                    self.missed_signals.append({
                                        'ts': ts, 'sym': symbol, 'strat': strat.name,
                                        'reason': 'BAL_LOW', 'best_gain_pct': round(best_gain_pct, 2),
                                        'side': signal['side']
                                    })
                                else:
                                    self.missed_signals.append({'ts': ts, 'sym': symbol, 'strat': strat.name, 'reason': 'BAL_LOW', 'best_gain_pct': 0})
                            except Exception:
                                self.missed_signals.append({'ts': ts, 'sym': symbol, 'strat': strat.name, 'reason': 'BAL_LOW', 'best_gain_pct': 0})
                            continue

                        
                        if len(self.positions) >= self.max_positions:
                             self.missed_signals.append({'ts': ts, 'sym': symbol, 'strat': strat.name, 'reason': 'MAX_POS'})
                             break

                        # Execute Entry
                        leverage = 10 # Default for sim
                        self.total_capital -= entry_size
                        self.positions[pos_key] = {
                            'symbol': symbol,
                            'strategy': strat.name,
                            'entry_price': data_map[symbol].loc[ts, 'close'],
                            'side': signal['side'],
                            'size': entry_size,
                            'leverage': leverage,
                            'highest_price': data_map[symbol].loc[ts, 'close'] if signal['side'] == 'buy' else 0,
                            'lowest_price': data_map[symbol].loc[ts, 'close'] if signal['side'] == 'sell' else 999999
                        }
                        print(f"[{ts}] ENTRY {symbol} ({strat.name}) | Size: ${entry_size:.2f} | Bal: ${self.total_capital:.2f}")

        self.print_report()

    def print_report(self):
        open_margin = sum(p['size'] for p in self.positions.values())
        total_equity = self.total_capital + open_margin
        
        print("\n" + "=" * 50)
        print("📊 SHARED POOL SIMULATION REPORT")
        print("=" * 50)
        print(f"Available Liquidity: ${self.total_capital:.2f}")
        print(f"Locked Margin:       ${open_margin:.2f}")
        print(f"TOTAL EQUITY:        ${total_equity:.2f}")
        total_pnl = total_equity - 100 # Initial was 100
        print(f"Total Net P&L:      ${total_pnl:+.2f}")
        print(f"Total Return:       {(total_pnl/100)*100:+.2f}%")
        print(f"Trades Closed:      {len(self.trades)}")
        print(f"Win Rate:           {(len([t for t in self.trades if t['pnl'] > 0])/len(self.trades)*100 if self.trades else 0):.1f}%")
        print("-" * 50)

        
        print("📈 BREAKDOWN BY STRATEGY:")
        strat_stats = {}
        for t in self.trades:
            s = t['strategy']
            if s not in strat_stats: strat_stats[s] = {'pnl': 0, 'wins': 0, 'total': 0, 'reasons': {}}
            strat_stats[s]['pnl'] += t['pnl']
            strat_stats[s]['total'] += 1
            if t['pnl'] > 0: strat_stats[s]['wins'] += 1
            reason = t['reason']
            strat_stats[s]['reasons'][reason] = strat_stats[s]['reasons'].get(reason, 0) + 1
            
        for s, stats in strat_stats.items():
            wr = (stats['wins']/stats['total'])*100
            print(f"  [{s}]: P&L: ${stats['pnl']:+.2f} | Win%: {wr:.1f}% ({stats['total']} trades)")
            for r, count in stats['reasons'].items():
                print(f"     - {r}: {count}")

        print("-" * 50)
        print(f"Missed Signals:   {len(self.missed_signals)}")
        if self.missed_signals:
            reasons = {}
            for m in self.missed_signals:
                reasons[m['reason']] = reasons.get(m['reason'], 0) + 1
            for r, count in reasons.items():
                print(f"  - {r}: {count}")

            # Show top missed opportunities
            gainers = [m for m in self.missed_signals if m.get('best_gain_pct', 0) > 0]
            gainers_sorted = sorted(gainers, key=lambda x: x['best_gain_pct'], reverse=True)[:10]
            if gainers_sorted:
                print("\n🚀 TOP 10 MISSED OPPORTUNITIES (by max possible price move):")
                print(f"  {'#':<3} {'Symbol':<22} {'Strategy':<24} {'Side':<6} {'Best Move':<12} {'Time'}")
                print("  " + "-"*85)
                for i, m in enumerate(gainers_sorted, 1):
                    leveraged = m['best_gain_pct'] * 10  # At 10x leverage
                    print(f"  {i:<3} {m['sym']:<22} {m['strat']:<24} {m.get('side','?'):<6} {m['best_gain_pct']:>+.2f}% ({leveraged:>+.0f}% lev) {m['ts']}")
        print("=" * 50)


def main():
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    # 1. Get Top 30 Symbols
    print("Fetching top volatile symbols...")
    tickers = exchange.fetch_tickers()
    # Filter for USDT futures pairs
    top_symbols = sorted(
        [s for s in tickers if (':USDT' in s or s.endswith('/USDT')) and tickers[s].get('quoteVolume', 0) > 10000000],
        key=lambda x: tickers[x].get('quoteVolume', 0),
        reverse=True
    )[:30]
    
    if not top_symbols:
        print("❌ No symbols found! Check exchange connection or filters.")
        sys.exit(1)

    print(f"Found {len(top_symbols)} symbols. Fetching OHLCV...")

    # 2. Fetch 24 Hours of 5m Data (288 candles per symbol)
    data_map = {}
    since = int((datetime.now() - timedelta(hours=24)).timestamp() * 1000)
    for sym in top_symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, '5m', since=since, limit=600)
            if not ohlcv: continue
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])


            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            data_map[sym] = df

            # print(f"Fetched {sym}: {len(df)} candles")
        except Exception as e:
            print(f"Error fetching {sym}: {e}")
            continue
    
    if not data_map:
        print("❌ No data fetched! Possible API error or timeframe mismatch.")
        sys.exit(1)

    # 3. Run Simulation
    sim = SharedPoolSimulator(initial_capital=100, max_positions=8)
    sim.run_simulation(data_map, list(data_map.keys()))



if __name__ == "__main__":
    main()
