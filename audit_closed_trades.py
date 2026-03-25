#!/usr/bin/env python3
"""
Binance-First Audit Script
Source of truth: Binance API fills.
Comparison: Mapping Binance reality → SQLite database.
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

class BinanceFirstAuditor:
    def __init__(self):
        self.config = Config()
        self.logger = MongoLogger(self.config)
        self.db = SQLiteManager(self.config)
        self.exchange = CCXTExchangeClient(self.config, self.logger)

    def format_row(self, data: List[Any], widths: List[int]) -> str:
        return " | ".join(str(val).ljust(width) for val, width in zip(data, widths))

    def run_audit(self):
        print(f"\n{'='*160}")
        print(f"🔒 BINANCE-FIRST AUDIT: EXCHANGE REALITY → DATABASE MIRROR - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*160}\n")

        # 1. FETCH GROUND TRUTH FROM BINANCE
        print("📡 Fetching last 100 trade fills from Binance...")
        try:
            # Global trades fetch (no symbol)
            fills = self.exchange.exchange.fapiPrivateGetUserTrades({'limit': 100})
            if not fills:
                print("ℹ️ No recent trades found on Binance.")
                return
            
            # Group fills by orderId (since one trade can have multiple partial fills)
            b_trades = {}
            for f in fills:
                oid = f['orderId']
                if oid not in b_trades:
                    b_trades[oid] = {
                        'symbol': f['symbol'],
                        'id': f['id'],
                        'orderId': f['orderId'],
                        'side': f['side'],
                        'price_total': 0.0,
                        'qty_total': 0.0,
                        'time': f['time'],
                        'pnl': 0.0,
                        'fills_count': 0
                    }
                bt = b_trades[oid]
                qty = float(f['qty'])
                bt['price_total'] += float(f['price']) * qty
                bt['qty_total'] += qty
                bt['pnl'] += float(f.get('realizedPnl', 0))
                bt['fills_count'] += 1
            
            # Sort by time desc
            sorted_oids = sorted(b_trades.keys(), key=lambda x: b_trades[x]['time'], reverse=True)
            
            # Pre-fetch leverage for involved symbols
            symbols = list(set([t['symbol'] for t in b_trades.values()]))
            leverage_map = {}
            try:
                # CCXT fetch_positions usually includes leverage
                pos_data = self.exchange.exchange.fetch_positions(symbols)
                for p in pos_data:
                    leverage_map[p['symbol']] = p.get('leverage')
            except: pass

        except Exception as e:
            print(f"❌ Binance API Error: {e}")
            return

        # 2. MATCH WITH DATABASE
        h = ["Symbol", "Order ID", "Side", "B.Price", "DB Price", "B.Size", "DB Size", "B.Lev", "DB Lev", "Status"]
        w = [15, 12, 6, 12, 12, 12, 12, 8, 8, 20]
        print(self.format_row(h, w))
        print("-" * (sum(w) + len(w)*3))

        for oid in sorted_oids:
            bt = b_trades[oid]
            sym = bt['symbol']
            
            # Normalize symbol for SQLite (Binance 'BTCUSDT' -> 'BTC/USDT:USDT')
            # This is a bit tricky, we'll try to find any open/closed trade with matching details
            b_avg_price = bt['price_total'] / bt['qty_total'] if bt['qty_total'] > 0 else 0
            b_size = bt['qty_total']
            
            # Search DB
            db_trade = None
            try:
                conn = self.db._get_connection(self.db.main_db)
                cursor = conn.cursor()
                # Try by exchange_order_id first (Phase 18+)
                cursor.execute("SELECT * FROM trades WHERE exchange_order_id = ?", (str(oid),))
                db_trade = cursor.fetchone()
                
                # Fallback: Fuzzy match by symbol and price
                if not db_trade:
                    # Search within a 2-hour window and 1% price diff
                    # timestamp is in ms
                    ts_iso = datetime.fromtimestamp(bt['time']/1000).isoformat()
                    # SQLite fuzzy for symbol (supports both BTCUSDT and BTC/USDT)
                    cursor.execute("SELECT * FROM trades WHERE (symbol LIKE ? OR symbol = ?) AND abs(entry_price - ?) / ? < 0.02 LIMIT 1", 
                                 (f"%{sym}%", sym, b_avg_price, b_avg_price if b_avg_price else 1))
                    db_trade = cursor.fetchone()
                conn.close()
            except: pass

            db_price = db_trade['entry_price'] if db_trade else 0.0
            db_size = db_trade['size'] if db_trade else 0.0
            db_lev = db_trade['leverage'] if db_trade else 0
            b_lev = leverage_map.get(sym, "?")
            
            status = "✅ MATCHED"
            if not db_trade:
                status = "🚨 ORPHAN (Not in DB)"
            elif abs(b_avg_price - db_price) > 0.0001:
                status = "🕒 PRICE DIFF"

            row = [
                sym,
                oid,
                bt['side'],
                f"{b_avg_price:.4f}",
                f"{db_price:.4f}" if db_price else "MISSING",
                f"{b_size:.2f}",
                f"{db_size:.2f}" if db_size else "MISSING",
                f"{b_lev}x",
                f"{db_lev}x" if db_lev else "?",
                status
            ]
            print(self.format_row(row, w))

        print(f"\n{'='*160}")
        print("💡 SUMMARY: This table starts with BINANCE reality and checks if our Database knows about it.")
        print("💡 ORPHAN: A trade exists on Binance that the Bot database has no record of.")
        print(f"{'='*160}\n")

if __name__ == "__main__":
    auditor = BinanceFirstAuditor()
    auditor.run_audit()
