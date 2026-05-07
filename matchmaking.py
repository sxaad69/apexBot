#!/usr/bin/env python3
"""
Matchmaking Script
Matches closed trades from the database with Binance's history to verify SL/TP execution and data sync.
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from database.sqlite_manager import SQLiteManager
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger

class ClosedTradeMatcher:
    def __init__(self):
        self.config = Config()
        self.logger = MongoLogger(self.config)
        self.db = SQLiteManager(self.config)
        self.exchange = CCXTExchangeClient(self.config, self.logger)
        self.stats = {
            'total': 0, 'ghosts': 0, 'desyncs': 0, 
            'sl_hits': 0, 'tp_hits': 0, 
            'sl_devs': [], 'tp_devs': []
        }
        
    def get_db_closed_trades(self, days: int = 1, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch trades marked as CLOSED in SQLite with time filtering."""
        conn = self.db._get_connection(self.db.main_db)
        cursor = conn.cursor()
        
        # Calculate the start time based on days
        start_time = (datetime.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%S')
        
        query = "SELECT * FROM trades WHERE status = 'CLOSED' AND exit_time >= ?"
        params = [start_time]
        
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
            
        query += " ORDER BY exit_time DESC"
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_binance_orders_for_symbols(self, symbols: set) -> Dict[str, List[Dict]]:
        """Fetch order history for specific symbols to map exit reasons and prices."""
        orders_by_sym = {}
        for sym in symbols:
            try:
                # fetch_orders fetches all orders (open and closed) for the symbol
                orders = self.exchange.exchange.fetch_closed_orders(sym, limit=500)
                orders_by_sym[sym] = orders
            except Exception as e:
                print(f"Error fetching orders for {sym}: {e}")
        return orders_by_sym

    def format_row(self, data: List[Any], widths: List[int]) -> str:
        return " | ".join(str(val).ljust(width)[:width] for val, width in zip(data, widths))

    def run_matchmaking(self, days: int = 1, symbol: Optional[str] = None):
        print(f"\n{'='*140}")
        filter_str = f"Today's trades" if days == 1 else f"Last {days} days"
        if symbol: filter_str += f" for {symbol}"
        print(f"🔍 APEX HUNTER MATCHMAKING - {filter_str} | {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*140}\n")

        db_trades = self.get_db_closed_trades(days=days, symbol=symbol)
        all_symbols = set(t['symbol'] for t in db_trades)
        
        print(f"Fetching Binance order history for {len(all_symbols)} symbols... this may take a moment.")
        binance_orders = self.get_binance_orders_for_symbols(all_symbols)
        
        # --- TABLE 1: STRICT VERIFICATION ---
        print("\n📊 TABLE 1: STRICT VERIFICATION (Closed Trades: Binance vs Database)")
        h1 = ["Symbol", "Side", "Entry B|DB", "Exit B|DB", "SL(DB)", "TP(DB)", "SL Hit?", "TP Hit?", "ROE%", "SL Dev", "TP Dev", "Status"]
        w1 = [13, 5, 15, 15, 8, 8, 8, 8, 8, 7, 7, 16]
        print("-" * sum(w1) + "---" * 12)
        print(self.format_row(h1, w1))
        print("-" * sum(w1) + "---" * 12)

        for trade in db_trades:
            sym = trade['symbol']
            entry_id = str(trade.get('exchange_order_id', ''))
            short_id = entry_id[-10:] if entry_id else "N/A"
            
            db_lev = trade.get('leverage', 0)
            db_margin = float(trade.get('size', 0))
            db_entry = float(trade.get('entry_price', 1)) 
            db_exit = float(trade.get('exit_price', 0))
            db_sl = float(trade.get('stop_loss', 0))
            db_tp = float(trade.get('take_profit', 0))
            side = trade.get('side', '').upper()
            exit_side = 'SELL' if side == 'BUY' else 'BUY'
            
            db_coins = (db_margin * db_lev) / db_entry if (db_entry > 0 and db_lev > 0) else 0

            # Parse binance orders to find the entry and exit match
            b_orders = binance_orders.get(sym, [])
            entry_order = next((o for o in b_orders if str(o.get('id', '')) == entry_id), None)
            
            b_entry = float(entry_order.get('average', entry_order.get('price', 0))) if entry_order else 0
            b_size = float(entry_order.get('amount', 0)) if entry_order else 0
            
            exit_candidates = []
            if trade.get('entry_time'):
                try:
                    entry_time_str = trade['entry_time']
                    for o in b_orders:
                        o_time = o.get('datetime', '')
                        if o.get('side', '').upper() == exit_side and o.get('status') == 'closed' and o_time >= entry_time_str:
                            exit_candidates.append(o)
                except:
                    pass
            
            b_exit = 0.0
            sl_hit = "No"
            tp_hit = "No"
            
            if exit_candidates:
                exit_match = exit_candidates[0] # Roughly the first trade that exited
                b_exit = float(exit_match.get('average') or exit_match.get('price') or 0)
                o_type = exit_match.get('type', '').upper()
                
                if 'STOP' in o_type: sl_hit = "Yes"
                elif 'TAKE_PROFIT' in o_type or 'LIMIT' in o_type: tp_hit = "Yes"
                elif o_type == 'MARKET':
                    if side == 'BUY':
                        if b_exit <= db_sl * 1.005 and db_sl > 0: sl_hit = "Yes(Prox)"
                        elif b_exit >= db_tp * 0.995 and db_tp > 0: tp_hit = "Yes(Prox)"
                    else:
                        if b_exit >= db_sl * 0.995 and db_sl > 0: sl_hit = "Yes(Prox)"
                        elif b_exit <= db_tp * 1.005 and db_tp > 0: tp_hit = "Yes(Prox)"

            b_lev_str = str(entry_order['info'].get('leverage', 'N/A')) if entry_order and entry_order.get('info') else "N/A"

            # Calculate ROE%
            roe_pct = 0.0
            if b_entry > 0 and b_exit > 0:
                side_mult = 1 if side == 'BUY' else -1
                actual_lev = float(b_lev_str) if b_lev_str != 'N/A' else float(db_lev)
                roe_pct = ((b_exit / b_entry) - 1) * 100 * side_mult * actual_lev

            # Calculate SL/TP Deviation (Slippage)
            sl_dev, tp_dev = 0.0, 0.0
            if "Yes" in sl_hit and db_sl > 0 and b_exit > 0:
                if side == 'BUY': sl_dev = ((b_exit / db_sl) - 1) * 100
                else: sl_dev = ((db_sl / b_exit) - 1) * 100
            
            if "Yes" in tp_hit and db_tp > 0 and b_exit > 0:
                if side == 'BUY': tp_dev = ((b_exit / db_tp) - 1) * 100
                else: tp_dev = ((db_tp / b_exit) - 1) * 100

            # Status logic
            status = "✅ VERIFIED"
            if not entry_id: status = "GHOST ENTRY"
            elif not exit_candidates: status = "NO BINANCE EXIT"
            elif entry_order and abs(db_coins - b_size) > (b_size * 0.01 + 0.0001): status = "❌ SIZE DESYNC"

            # Stats tracking
            self.stats['total'] += 1
            if not entry_id: self.stats['ghosts'] += 1
            if "Yes" in sl_hit:
                self.stats['sl_hits'] += 1
                self.stats['sl_devs'].append(sl_dev)
            if "Yes" in tp_hit:
                self.stats['tp_hits'] += 1
                self.stats['tp_devs'].append(tp_dev)
            if status == "❌ SIZE DESYNC": self.stats['desyncs'] += 1

            print(self.format_row([
                sym, side, f"{b_entry:.4f}|{db_entry:.4f}", f"{b_exit:.4f}|{db_exit:.4f}",
                f"{db_sl:.4f}", f"{db_tp:.4f}", sl_hit, tp_hit, f"{roe_pct:.1f}%", 
                f"{sl_dev:.1f}%", f"{tp_dev:.1f}%", status
            ], w1))

        print(f"\n{'='*140}")
        print("📊 EXECUTION SUMMARY")
        print(f"{'='*140}")
        avg_sl_dev = sum(self.stats['sl_devs']) / len(self.stats['sl_devs']) if self.stats['sl_devs'] else 0
        avg_tp_dev = sum(self.stats['tp_devs']) / len(self.stats['tp_devs']) if self.stats['tp_devs'] else 0
        
        print(f"Total Trades Analyzed: {self.stats['total']}")
        print(f"Ghost Trades (No ID): {self.stats['ghosts']}")
        print(f"Size Desyncs:         {self.stats['desyncs']}")
        print(f"Stop Loss Hits:       {self.stats['sl_hits']}")
        print(f"Take Profit Hits:     {self.stats['tp_hits']}")
        print(f"Avg SL Slippage:      {avg_sl_dev:.2f}%")
        print(f"Avg TP Slippage:      {avg_tp_dev:.2f}%")
        
        if abs(avg_sl_dev) < 0.5:
            print("\n✅ STOP LOSS PERFORMANCE: Working like a charm.")
        else:
            print("\n⚠️ STOP LOSS PERFORMANCE: Significant slippage detected.")

        if abs(avg_tp_dev) < 0.5:
            print("✅ TAKE PROFIT PERFORMANCE: Working like a charm.")
        else:
            print("⚠️ TAKE PROFIT PERFORMANCE: Deviation detected.")

        print(f"{'='*140}\n")
        print("🏁 MATCHMAKING COMPLETED")
        print(f"{'='*140}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Match closed trades with Binance history.")
    parser.add_argument("--days", type=int, default=1, help="Number of days to look back (default: 1)")
    parser.add_argument("--symbol", type=str, help="Filter by specific symbol")
    args = parser.parse_args()

    matcher = ClosedTradeMatcher()
    matcher.run_matchmaking(days=args.days, symbol=args.symbol)
