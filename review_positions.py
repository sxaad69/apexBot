#!/usr/bin/env python3
"""
Review Positions Script (READ-ONLY)
1. Safely compares Binance live positions with bot database.
2. Identifies ZOMBIES (DB only) and STRANGERS (Exchange only).
3. Audits SL/TP vs Current Price without taking action.
"""

import os
import sys
import json
from datetime import datetime
from typing import Dict, List, Any

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from database.sqlite_manager import SQLiteManager
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger

class PositionReviewer:
    def __init__(self):
        self.config = Config()
        self.logger = MongoLogger(self.config)
        self.db = SQLiteManager(self.config)
        self.exchange = CCXTExchangeClient(self.config, self.logger)
        
    def get_db_open_trades(self, since: str = None) -> Dict[str, Any]:
        """Fetch trades marked as OPEN in SQLite, optionally since a specific date."""
        conn = self.db._get_connection(self.db.main_db)
        cursor = conn.cursor()
        query = "SELECT * FROM trades WHERE status = 'OPEN'"
        params = []
        if since:
            query += " AND entry_time >= ?"
            params.append(since)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        conn.close()
        return {row['symbol']: dict(row) for row in rows}

    def get_live_positions(self) -> Dict[str, Any]:
        """Fetch active positions from Binance"""
        positions = self.exchange.get_positions()
        active = {}
        for pos in positions:
            if abs(float(pos.get('contracts', 0) or 0)) > 0:
                symbol = pos['symbol']
                active[symbol] = pos
        return active

    def format_row(self, data: List[Any], widths: List[int]) -> str:
        return " | ".join(str(val).ljust(width) for val, width in zip(data, widths))

    def run_review(self, since: str = None):
        print(f"\n{'='*100}")
        print(f"🔍 APEX HUNTER POSITION REVIEW (READ-ONLY) - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        if since:
            print(f"📅 Reviewing trades opened since: {since}")
        print(f"{'='*100}\n")

        db_trades = self.get_db_open_trades(since)
        live_positions = self.get_live_positions()
        
        all_symbols = sorted(list(set(db_trades.keys()) | live_positions.keys()))
        symbols_to_fetch = [s for s in all_symbols if s in live_positions or s in db_trades]
        
        tickers = {}
        if symbols_to_fetch:
            try:
                tickers = self.exchange.exchange.fetch_tickers(symbols_to_fetch)
            except: pass

        # --- TABLE 1: RECONCILIATION ---
        print("📊 TABLE 1: STRICT VERIFICATION (Binance vs Database)")
        h1 = ["Symbol", "Order ID", "Side", "Margin ($)", "Size(Tokens)", "Entry (B)", "Curr Price", "SL (DB)", "TP (DB)", "ROE%", "Status"]
        w1 = [16, 12, 5, 12, 14, 10, 10, 10, 10, 8, 18]
        print("-" * sum(w1) + "---" * 10)
        print(self.format_row(h1, w1))
        print("-" * sum(w1) + "---" * 10)

        for sym in all_symbols:
            db_trade = db_trades.get(sym)
            live_pos = live_positions.get(sym)
            
            binance_size = live_pos.get('contracts', 0) if live_pos else 0
            db_margin = db_trade.get('size', 0) if db_trade else 0
            db_lev = db_trade.get('leverage', 0) if db_trade else 0
            db_entry = db_trade.get('entry_price', 1) if db_trade else 1
            
            # Calculate DB size in Coins instead of Margin
            db_coins = (float(db_margin) * float(db_lev)) / float(db_entry) if (db_entry and db_lev and db_margin) else 0
            
            binance_lev = live_pos.get('leverage', 0) if live_pos else 0
            side = db_trade.get('side', 'N/A').upper() if db_trade else (live_pos.get('side', 'N/A').upper() if live_pos else 'N/A')
            order_id = str(db_trade.get('exchange_order_id', 'N/A'))[-10:] if db_trade else "N/A"
            sl_price = db_trade.get('stop_loss', 0) if db_trade else 0
            tp_price = db_trade.get('take_profit', 0) if db_trade else 0
            
            # Formatting floats
            b_sz_str = f"{float(binance_size):.1f}"
            db_sz_str = f"{float(db_coins):.1f}"
            size_str = f"{b_sz_str}|{db_sz_str}"
            
            # ROE% Calculation
            roe_pct = 0
            curr_price = 0
            entry_price = 0
            if live_pos:
                entry_price = float(live_pos.get('entryPrice', 0))
                curr_price = float(tickers.get(sym, {}).get('last', entry_price))
                side_mult = 1 if live_pos.get('side', 'buy').lower() == 'buy' else -1
                if entry_price > 0:
                    roe_pct = ((curr_price / entry_price) - 1) * 100 * side_mult * binance_lev

            # Margin calculation (Binance side)
            binance_margin = 0
            if binance_size and entry_price and binance_lev:
                binance_margin = (float(binance_size) * entry_price) / float(binance_lev)
            
            margin_str = f"${float(binance_margin):.2f}|${float(db_margin):.2f}"

            # Strict verification logic
            status = "✅ VERIFIED"
            if db_trade and not live_pos:
                status = "🚨 DB ZOMBIE"
            elif live_pos and not db_trade:
                status = "👻 EXCHANGE ORPHAN"
            elif order_id == "N/A" or not order_id:
                status = "⚠️ NO ORDER ID"
            elif abs(float(db_coins) - float(binance_size)) > (float(binance_size) * 0.01): # 1% tolerance for floating point math
                status = "❌ SIZE DESYNC"
            elif abs(float(db_lev) - float(binance_lev)) > 0:
                status = "⚠️ LEV DESYNC"
                
            print(self.format_row([
                sym, order_id, side, margin_str, size_str, 
                f"{entry_price:.5f}" if entry_price else "0", 
                f"{curr_price:.5f}" if curr_price else "0",
                f"{float(sl_price):.5f}" if sl_price else "0",
                f"{float(tp_price):.5f}" if tp_price else "0",
                f"{roe_pct:.1f}%",
                status
            ], w1))

        # --- TABLE 2: RECENT HISTORY ---
        print("\n📜 TABLE 2: RECENTLY CLOSED TRADES (Last 5)")
        h2 = ["Symbol", "Exit Price", "P&L $", "P&L %", "Reason", "Exit Time"]
        w2 = [15, 12, 10, 10, 20, 20]
        print("-" * sum(w2) + "---" * 6)
        print(self.format_row(h2, w2))
        print("-" * sum(w2) + "---" * 6)
        
        try:
            conn = self.db._get_connection(self.db.main_db)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC LIMIT 5")
            for row in cursor.fetchall():
                pnl_percent_val = row['pnl_percent'] if row['pnl_percent'] is not None else 0
                print(self.format_row([
                    row['symbol'], row['exit_price'], f"${row['pnl_amount']:.2f}",
                    f"{pnl_percent_val:.2f}%", row['reason'], str(row['exit_time'])[:16]
                ], w2))
            conn.close()
        except: pass

        print(f"\n{'='*100}")
        print("🏁 REVIEW COMPLETED (No actions taken)")
        print(f"{'='*100}\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="Filter trades since (YYYY-MM-DD)")
    args = parser.parse_args()
    
    reviewer = PositionReviewer()
    reviewer.run_review(since=args.since)
