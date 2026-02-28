#!/usr/bin/env python3
"""
Apex Hunter – Timeframe Comparison Backtest
Runs 5m vs 15m across the last N days with a day-by-day P&L breakdown.

Usage:
    python3 scripts/timeframe_comparison.py --days 5
    python3 scripts/timeframe_comparison.py --days 10
"""

import sys, os, argparse
from datetime import datetime, timedelta, timezone
import pandas as pd
import ccxt

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import Config
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4
from bot_logging import Logger

INITIAL_CAPITAL = 100
MAX_POSITIONS   = 8
ENTRY_SIZE_PCT  = 0.20   # 20 % of available pool per trade
MIN_ENTRY       = 20.0   # $20 minimum
SL_PCT          = 0.02   # 2 % price-based SL
TP_PCT          = 0.04   # 4 % price-based TP
LEVERAGE        = 10


# ─── Simulator ────────────────────────────────────────────────────────────────

class DailySimulator:
    def __init__(self, strategies, initial_capital=INITIAL_CAPITAL, max_pos=MAX_POSITIONS):
        self.strategies    = strategies
        self.initial_cap   = initial_capital
        self.max_pos       = max_pos
        self.total_capital = initial_capital
        self.positions     = {}   # key → position dict
        self.trades        = []   # closed trade records
        self.missed        = []

    # -- helpers ----------------------------------------------------------------
    def _entry_size(self):
        return max(MIN_ENTRY, self.total_capital * ENTRY_SIZE_PCT)

    def _price_pnl(self, pos, price):
        if pos['side'] == 'buy':
            return (price - pos['entry']) / pos['entry']
        return (pos['entry'] - price) / pos['entry']

    # -- main loop --------------------------------------------------------------
    def simulate(self, data_map, symbols):
        all_ts = sorted(set().union(*(df.index for df in data_map.values())))

        for ts in all_ts:
            # 1. Manage exits
            for key, pos in list(self.positions.items()):
                sym = pos['symbol']
                if ts not in data_map.get(sym, pd.DataFrame()).index:
                    continue
                price = data_map[sym].loc[ts, 'close']
                ppnl  = self._price_pnl(pos, price)
                reason = None
                if ppnl <= -SL_PCT:
                    reason = 'SL'
                elif ppnl >= TP_PCT:
                    reason = 'TP'
                if reason:
                    profit = pos['size'] * ppnl * LEVERAGE
                    self.total_capital += pos['size'] + profit
                    self.trades.append({
                        'ts': ts, 'symbol': sym, 'strategy': pos['strategy'],
                        'side': pos['side'], 'pnl': profit,
                        'pnl_pct': ppnl * 100, 'reason': reason,
                        'day': ts.date()
                    })
                    del self.positions[key]

            # 2. Entries
            if len(self.positions) >= self.max_pos:
                continue

            for sym in symbols:
                if ts not in data_map.get(sym, pd.DataFrame()).index:
                    continue
                df_slice = data_map[sym].loc[:ts]
                if len(df_slice) < 50:
                    continue

                for strat in self.strategies:
                    key = f"{strat.name}:{sym}"
                    if key in self.positions:
                        continue
                    if len(self.positions) >= self.max_pos:
                        break

                    signal = strat.generate_signal(df_slice)
                    if not signal:
                        continue

                    size = self._entry_size()
                    if self.total_capital < size:
                        # track missed with forward potential
                        try:
                            fp = data_map[sym].loc[ts:]['close']
                            ep = fp.iloc[0]
                            if signal['side'] == 'buy':
                                gain = (fp.max() - ep) / ep * 100
                            else:
                                gain = (ep - fp.min()) / ep * 100
                        except Exception:
                            gain = 0
                        self.missed.append({'ts': ts, 'sym': sym, 'strat': strat.name,
                                            'side': signal['side'], 'gain': round(gain, 2),
                                            'day': ts.date()})
                        continue

                    self.total_capital -= size
                    self.positions[key] = {
                        'symbol': sym, 'strategy': strat.name,
                        'side': signal['side'], 'entry': data_map[sym].loc[ts, 'close'],
                        'size': size
                    }

    # -- reporting --------------------------------------------------------------
    def report(self, label, days_list):
        closed_pnl = sum(t['pnl'] for t in self.trades)
        open_margin = sum(p['size'] for p in self.positions.values())
        total_equity = self.total_capital + open_margin
        net_pnl = total_equity - self.initial_cap
        wins = [t for t in self.trades if t['pnl'] > 0]
        wr = len(wins) / len(self.trades) * 100 if self.trades else 0

        print(f"\n{'='*62}")
        print(f"  📈 {label.upper()} SIMULATION SUMMARY")
        print(f"{'='*62}")
        print(f"  Total Equity:   ${total_equity:.2f}  (Avail: ${self.total_capital:.2f}  Locked: ${open_margin:.2f})")
        print(f"  Net P&L:        ${net_pnl:+.2f}  ({net_pnl/self.initial_cap*100:+.2f}%)")
        print(f"  Closed Trades:  {len(self.trades)}  |  Win Rate: {wr:.1f}%")
        print(f"  Missed Signals: {len(self.missed)}")

        # Strategy breakdown
        print(f"\n  {'Strategy':<26} {'Trades':>7} {'Wins':>5} {'P&L':>10}")
        print(f"  {'-'*52}")
        strats = {}
        for t in self.trades:
            s = t['strategy']
            strats.setdefault(s, {'pnl':0,'wins':0,'n':0,'sl':0,'tp':0})
            strats[s]['pnl'] += t['pnl']
            strats[s]['n']   += 1
            strats[s]['sl']  += 1 if t['reason'] == 'SL' else 0
            strats[s]['tp']  += 1 if t['reason'] == 'TP' else 0
            if t['pnl'] > 0: strats[s]['wins'] += 1
        for s, v in sorted(strats.items(), key=lambda x: -x[1]['pnl']):
            w = v['wins']/v['n']*100 if v['n'] else 0
            print(f"  {s:<26} {v['n']:>7}  {w:>4.0f}%  ${v['pnl']:>+8.2f}  (TP:{v['tp']} SL:{v['sl']})")

        # Day-by-day breakdown
        print(f"\n  {'Day':<12} {'Trades':>7} {'P&L':>10} {'Equity':>10}")
        print(f"  {'-'*42}")
        running = self.initial_cap
        for d in sorted(days_list):
            day_trades = [t for t in self.trades if t['day'] == d]
            day_pnl = sum(t['pnl'] for t in day_trades)
            running += day_pnl
            missed_today = len([m for m in self.missed if m['day'] == d])
            tag = "📈" if day_pnl > 0 else ("📉" if day_pnl < 0 else "–")
            print(f"  {str(d):<12} {len(day_trades):>7}  ${day_pnl:>+8.2f}  ${running:>8.2f}  {tag}  (missed:{missed_today})")

        # Top missed signals
        top_missed = sorted(self.missed, key=lambda x: -x['gain'])[:5]
        if top_missed:
            print(f"\n  🔥 TOP 5 MISSED OPPORTUNITIES:")
            for m in top_missed:
                lev = m['gain'] * LEVERAGE
                print(f"     {m['sym']:<22} {m['side']:<5} +{m['gain']:.2f}% price  (+{lev:.0f}% leveraged)  {m['ts']}")
        print(f"{'='*62}")


# ─── Data Fetching ─────────────────────────────────────────────────────────────

def fetch_data(exchange, symbols, timeframe, days):
    since  = int((datetime.now() - timedelta(days=days)).timestamp() * 1000)
    # 5m → 288 candles/day, 15m → 96 candles/day
    mult   = 288 if timeframe == '5m' else 96
    limit  = min(1000, days * mult + 50)
    data   = {}
    for sym in symbols:
        try:
            ohlcv = exchange.fetch_ohlcv(sym, timeframe, since=since, limit=limit)
            if not ohlcv:
                continue
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            data[sym] = df
        except Exception as e:
            print(f"  Warning: could not fetch {sym} [{timeframe}]: {e}")
    return data


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=5, help='Number of past days (5 or 10)')
    args = parser.parse_args()
    days = args.days

    print(f"\n🤖 APEX HUNTER — {days}-DAY TIMEFRAME COMPARISON  (5m vs 15m)")
    print(f"   Capital: ${INITIAL_CAPITAL}  |  Leverage: {LEVERAGE}x  |  Max Positions: {MAX_POSITIONS}")
    print(f"   SL: {SL_PCT*100:.0f}% price move  |  TP: {TP_PCT*100:.0f}% price move\n")

    # Config & Strategies
    config = Config()
    logger = Logger(config)
    strats_5m  = [StrategyA1(config, logger), StrategyA2(config, logger),
                  StrategyA3(config, logger), StrategyA4(config, logger)]
    strats_15m = [StrategyA1(config, logger), StrategyA2(config, logger),
                  StrategyA3(config, logger), StrategyA4(config, logger)]

    # Exchange – top 30 by volume
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    print("Fetching top 30 symbols by volume...")
    tickers = exchange.fetch_tickers()
    symbols = sorted(
        [s for s in tickers if (':USDT' in s or s.endswith('/USDT'))
         and tickers[s].get('quoteVolume', 0) > 5_000_000],
        key=lambda x: tickers[x].get('quoteVolume', 0), reverse=True
    )[:30]
    print(f"Found {len(symbols)} symbols.\n")

    # Date range for day list
    day_list = [(datetime.now() - timedelta(days=d)).date() for d in range(days, 0, -1)]

    # ── 5m run ─────────────
    print("📥 Fetching 5m data...")
    data_5m = fetch_data(exchange, symbols, '5m', days)
    print(f"   Loaded {len(data_5m)} symbols.")
    sim_5m = DailySimulator(strats_5m)
    sim_5m.simulate(data_5m, list(data_5m.keys()))
    sim_5m.report(f"5m Timeframe — Last {days} Days", day_list)

    # ── 15m run ────────────
    print(f"\n📥 Fetching 15m data...")
    data_15m = fetch_data(exchange, symbols, '15m', days)
    print(f"   Loaded {len(data_15m)} symbols.")
    sim_15m = DailySimulator(strats_15m)
    sim_15m.simulate(data_15m, list(data_15m.keys()))
    sim_15m.report(f"15m Timeframe — Last {days} Days", day_list)

    # ── Side-by-side summary ──
    eq_5m  = sim_5m.total_capital  + sum(p['size'] for p in sim_5m.positions.values())
    eq_15m = sim_15m.total_capital + sum(p['size'] for p in sim_15m.positions.values())
    pnl_5m  = eq_5m  - INITIAL_CAPITAL
    pnl_15m = eq_15m - INITIAL_CAPITAL
    print(f"\n{'='*62}")
    print(f"  ⚖️  SIDE-BY-SIDE: Last {days} days")
    print(f"{'='*62}")
    print(f"  {'Metric':<25} {'5m':>14} {'15m':>14}")
    print(f"  {'-'*55}")
    print(f"  {'Total Equity':<25} ${eq_5m:>12.2f} ${eq_15m:>12.2f}")
    print(f"  {'Net P&L':<25} ${pnl_5m:>+12.2f} ${pnl_15m:>+12.2f}")
    print(f"  {'Return %':<25} {pnl_5m/INITIAL_CAPITAL*100:>+12.2f}% {pnl_15m/INITIAL_CAPITAL*100:>+12.2f}%")
    print(f"  {'Closed Trades':<25} {len(sim_5m.trades):>14} {len(sim_15m.trades):>14}")
    wr5  = len([t for t in sim_5m.trades  if t['pnl']>0])/len(sim_5m.trades)*100  if sim_5m.trades  else 0
    wr15 = len([t for t in sim_15m.trades if t['pnl']>0])/len(sim_15m.trades)*100 if sim_15m.trades else 0
    print(f"  {'Win Rate':<25} {wr5:>13.1f}% {wr15:>13.1f}%")
    print(f"  {'Missed Signals':<25} {len(sim_5m.missed):>14} {len(sim_15m.missed):>14}")
    winner = "5m" if pnl_5m > pnl_15m else "15m"
    print(f"\n  🏆 Winner: {winner}")
    print(f"{'='*62}\n")


if __name__ == "__main__":
    main()
