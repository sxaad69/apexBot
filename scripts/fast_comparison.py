#!/usr/bin/env python3
"""
Apex Hunter – UltraFast Timeframe Comparison (Vectorized)
Optimized for speed: pre-calculates signals for all symbols/strategies.
"""

import sys, os, argparse
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import ccxt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from bot_logging import Logger
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4

# Simulation Constants
INITIAL_CAPITAL = 100
MAX_POSITIONS   = 8
ENTRY_SIZE_PCT  = 0.12
MIN_ENTRY       = 12.0
SL_PCT          = 0.02
TP_PCT          = 0.04
LEVERAGE        = 10
FEE_PCT         = 0.0004

def fetch_ohlcv(exchange, sym, tf, since, limit):
    all_ohlcv = []
    current_since = since
    remaining = limit
    
    while remaining > 0:
        fetch_limit = min(remaining, 1000)
        try:
            ohlcv = exchange.fetch_ohlcv(sym, tf, since=current_since, limit=fetch_limit)
            if not ohlcv: break
            all_ohlcv.extend(ohlcv)
            remaining -= len(ohlcv)
            current_since = ohlcv[-1][0] + 1
            if len(ohlcv) < fetch_limit: break
        except Exception: break
        
    if not all_ohlcv: return None
    df = pd.DataFrame(all_ohlcv, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)
    return df

def get_top_symbols(exchange, n=10):
    tickers = exchange.fetch_tickers()
    return sorted(
        [s for s in tickers if (':USDT' in s or s.endswith('/USDT'))
         and tickers[s].get('quoteVolume', 0) > 5_000_000],
        key=lambda x: tickers[x].get('quoteVolume', 0), reverse=True
    )[:n]

class SuperFastSimulator:
    def __init__(self, strategies, label):
        self.strategies    = strategies
        self.label         = label
        self.capital       = INITIAL_CAPITAL
        self.positions     = {}
        self.trades        = []

    def precalculate_signals(self, data_map):
        """Pre-calculate signals for ALL symbols/strategies in vectorized way if possible."""
        signals = {} # (ts, sym, strat_name) -> side
        for sym, df in data_map.items():
            for strat in self.strategies:
                # We still call generate_signal on slices in this version 
                # but we do it once per candle per symbol/strat outside the shared loop
                # To be TRULY fast we would need vectorized logic.
                # However, even doing it symbol-by-symbol once is faster than the main loop.
                pass
        return signals

    def run(self, data_map, symbols, day_list):
        all_timestamps = sorted(set().union(*(df.index for df in data_map.values())))
        
        # Pre-extract signals to avoid repeated slicing
        print("   Analyzing market signals...")
        sig_cache = {sym: {} for sym in symbols}
        
        for sym, df in data_map.items():
            for strat in self.strategies:
                # Optimized: only calculate indicators ONCE per symbol/strat
                # We need to preserve the columns, so we use a copy
                s_df = df.copy()
                s_df = strat.calculate_indicators(s_df)
                s_df = strat.calculate_adx(s_df)
                
                # Pre-calculate signal BOOLEANS to avoid row-by-row logic in main loop
                # We replicate the basic logic of generate_signal here in vectorized form
                s_df['is_buy'] = False
                s_df['is_sell'] = False
                
                if strat.name.startswith("A1"):
                    # EMA 9/21 cross + MACD hist
                    s_df['is_buy'] = (s_df['ema_fast'] > s_df['ema_slow']) & (s_df['macd_histogram'] > 0)
                    s_df['is_sell'] = (s_df['ema_fast'] < s_df['ema_slow']) & (s_df['macd_histogram'] < 0)
                elif strat.name.startswith("A2"):
                    # EMA 9/21 cross + RSI
                    s_df['is_buy'] = (s_df['ema_fast'] > s_df['ema_slow']) & (s_df['rsi'] > 50)
                    s_df['is_sell'] = (s_df['ema_fast'] < s_df['ema_slow']) & (s_df['rsi'] < 50)
                elif strat.name.startswith("A3"):
                    # Fast EMA 5/13 cross + ADX
                    s_df['is_buy'] = (s_df['ema_fast'] > s_df['ema_slow']) & (s_df['adx'] > 30)
                    s_df['is_sell'] = (s_df['ema_fast'] < s_df['ema_slow']) & (s_df['adx'] > 30)
                
                sig_cache[sym][strat.name] = s_df[['is_buy', 'is_sell']]

        print("   Simulating Shared Pool...")
        for ts in all_timestamps:
            # 1. Manage Exits
            for key, pos in list(self.positions.items()):
                sym = pos['symbol']
                if ts not in data_map[sym].index: continue
                curr_price = data_map[sym].loc[ts, 'close']
                move = (curr_price - pos['entry']) / pos['entry'] if pos['side'] == 'buy' else (pos['entry'] - curr_price) / pos['entry']
                
                exit_reason = None
                if move <= -SL_PCT: exit_reason = 'SL'
                elif move >= TP_PCT: exit_reason = 'TP'
                
                if exit_reason:
                    profit = pos['size'] * move * LEVERAGE
                    fee = (pos['size'] * LEVERAGE * FEE_PCT * 2)
                    net_profit = profit - fee
                    self.capital += pos['size'] + net_profit
                    self.trades.append({
                        'day': ts.date(), 'symbol': sym, 'strat': pos['strategy'],
                        'side': pos['side'], 'pnl': net_profit, 'reason': exit_reason
                    })
                    del self.positions[key]

            if len(self.positions) >= MAX_POSITIONS: continue

            # 2. Manage Entries
            for sym in symbols:
                if ts not in data_map[sym].index: continue
                if len(self.positions) >= MAX_POSITIONS: break

                for strat in self.strategies:
                    pos_key = f"{strat.name}:{sym}"
                    if pos_key in self.positions: continue
                    
                    row = sig_cache[sym][strat.name].loc[ts]
                    
                    signal_side = None
                    if row['is_buy']: signal_side = 'buy'
                    elif row['is_sell']: signal_side = 'sell'
                    
                    if signal_side:
                        entry_size = max(MIN_ENTRY, self.capital * ENTRY_SIZE_PCT)
                        if self.capital < entry_size: continue
                        self.capital -= entry_size
                        self.positions[pos_key] = {
                            'symbol': sym, 'strategy': strat.name,
                            'side': signal_side, 'entry': data_map[sym].loc[ts, 'close'],
                            'size': entry_size
                        }


    def report(self, day_list):
        open_val = sum(p['size'] for p in self.positions.values())
        equity = self.capital + open_val
        net_pnl = equity - INITIAL_CAPITAL
        print(f"\n📊 {self.label.upper()}")
        print(f"   Final Equity: ${equity:.2f} | P&L: ${net_pnl:+.2f} ({net_pnl/INITIAL_CAPITAL*100:+.2f}%)")
        print(f"   Trades: {len(self.trades)}")
        
        print(f"   {'Date':<12} {'Trades':>6} {'P&L':>10}")
        for d in sorted(day_list):
            dt = [t for t in self.trades if t['day'] == d]
            dp = sum(t['pnl'] for t in dt)
            print(f"   {str(d):<12} {len(dt):>6}  ${dp:>+9.2f}")
        return net_pnl, len(self.trades)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=5)
    args = parser.parse_args()
    
    config = Config()
    logger = Logger(config)
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    
    symbols = get_top_symbols(exchange, n=10)
    day_list = [(datetime.now() - timedelta(days=d)).date() for d in range(args.days, 0, -1)]

    def run_tf(tf):
        strats = [StrategyA1(config, logger), StrategyA2(config, logger), 
                  StrategyA3(config, logger), StrategyA4(config, logger)]
        since = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)
        limit = (args.days * (288 if tf == '5m' else 96)) + 100
        data_map = {}
        print(f"\nFetching {tf} data...")
        for s in symbols:
            df = fetch_ohlcv(exchange, s, tf, since, limit)
            if df is not None: data_map[s] = df
            
        sim = SuperFastSimulator(strats, f"{tf} - {args.days} Days")
        sim.run(data_map, symbols, day_list)
        return sim.report(day_list)

    pnl_5m, t_5m = run_tf('5m')
    pnl_15m, t_15m = run_tf('15m')
    print(f"\n🏆 VERDICT: {'5m' if pnl_5m > pnl_15m else '15m'} Wins")

if __name__ == "__main__":
    main()
