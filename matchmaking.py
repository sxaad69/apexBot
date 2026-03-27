#!/usr/bin/env python3
"""
Matchmaking Script
Matches closed trades from the database with Binance's history to verify SL/TP execution and data sync.
"""

import os
import sys
from datetime import datetime
from typing import Dict, List, Any

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
        
    def get_db_closed_trades(self) -> List[Dict[str, Any]]:
        """Fetch trades marked as CLOSED in SQLite."""
        conn = self.db._get_connection(self.db.main_db)
        cursor = conn.cursor()
        query = "SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC LIMIT 100"
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_binance_orders_for_symbols(self, symbols: set) -> Dict[str, List[Dict]]:
        """Fetch order history for specific symbols to map exit reasons and prices."""
        orders_by_sym = {}
        for sym in symbols:
            try:
                # fetch_orders fetches all orders (open and closed) for the symbol
                orders = self.exchange.exchange.fetch_closed_orders(sym, limit=100)
                orders_by_sym[sym] = orders
            except Exception as e:
                print(f"Error fetching orders for {sym}: {e}")
        return orders_by_sym

    def format_row(self, data: List[Any], widths: List[int]) -> str:
        return " | ".join(str(val).ljust(width)[:width] for val, width in zip(data, widths))

    def run_matchmaking(self):
        print(f"\n{'='*140}")
        print(f"🔍 APEX HUNTER CLOSED TRADES MATCHMAKING - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*140}\n")

        db_trades = self.get_db_closed_trades()
        all_symbols = set(t['symbol'] for t in db_trades)
        
        print(f"Fetching Binance order history for {len(all_symbols)} symbols... this may take a moment.")
        binance_orders = self.get_binance_orders_for_symbols(all_symbols)
        
        # --- TABLE 1: STRICT VERIFICATION ---
        print("\n📊 TABLE 1: STRICT VERIFICATION (Closed Trades: Binance vs Database)")
        h1 = ["Symbol", "Entry ID", "Side", "Margin($)", "Size", "Lev B|DB", "Entry B|DB", "Exit B|DB", "SL(DB)", "TP(DB)", "SL Hit?", "TP Hit?", "ROE%", "Status"]
        w1 = [13, 11, 5, 8, 10, 8, 15, 15, 8, 8, 8, 8, 8, 16]
        print("-" * sum(w1) + "---" * 13)
        print(self.format_row(h1, w1))
        print("-" * sum(w1) + "---" * 13)

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

            # Status logic
            status = "✅ VERIFIED"
            if not entry_id: status = "GHOST ENTRY"
            elif not exit_candidates: status = "NO BINANCE EXIT"
            elif entry_order and abs(db_coins - b_size) > (b_size * 0.01 + 0.0001): status = "❌ SIZE DESYNC"
            
            print(self.format_row([
                sym, short_id, side, f"{db_margin:.1f}", f"{b_size:.1f}|{db_coins:.1f}", 
                f"{b_lev_str}|{db_lev}", f"{b_entry:.4f}|{db_entry:.4f}", f"{b_exit:.4f}|{db_exit:.4f}",
                f"{db_sl:.4f}", f"{db_tp:.4f}", sl_hit, tp_hit, f"{roe_pct:.1f}%", status
            ], w1))

        print(f"\n{'='*140}")
        print("🏁 MATCHMAKING COMPLETED")
        print(f"{'='*140}\n")

if __name__ == "__main__":
    matcher = ClosedTradeMatcher()
    matcher.run_matchmaking()
