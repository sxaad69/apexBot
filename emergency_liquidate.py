#!/usr/bin/env python3
import os
import sys
import logging
from typing import List

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from database.sqlite_manager import SQLiteManager
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger
from core.trade_manager import TradeManager

def emergency_liquidate():
    """
    NUCLEAR OPTION: Closes all open positions and cancels all open orders on Binance.
    Synchronizes with SQLite via TradeManager.
    Optimized to use fetch_positions to avoid rate limit warnings.
    """
    config = Config()
    logger = MongoLogger(config)
    db = SQLiteManager(config)
    exchange = CCXTExchangeClient(config, logger)
    tm = TradeManager(config, db, exchange, logger)

    print("\n" + "!" * 80)
    print("⚠️  EMERGENCY LIQUIDATION INITIATED ⚠️")
    print("!" * 80 + "\n")

    # 1. Fetch all open positions
    print("🔍 Step 1: Fetching open positions from Binance...")
    try:
        positions = exchange.exchange.fetch_positions()
        active_positions = [p for p in positions if float(p.get('contracts', 0)) != 0]
        
        if not active_positions:
            print("ℹ️  No active positions found on exchange.")
        else:
            print(f"🚀 Found {len(active_positions)} active positions. Liquidating now...")
            
            # Fetch DB trades for grounding
            db_trades = db.get_trades(status='OPEN')
            
            for pos in active_positions:
                symbol = pos['symbol']
                side = pos['side']
                contracts = pos['contracts']
                
                print(f"🔥 Processing {symbol}...")
                
                try:
                    # Cancel orders for this symbol first
                    exchange.exchange.cancel_all_orders(symbol)
                    print(f"  🛡️  Canceled orders for {symbol}")
                    
                    # Execute Market Close
                    order = exchange.close_position(symbol)
                    print(f"  🔥 Closed {side.upper()} {symbol}")
                    
                    # Find matching DB trade
                    matching_trade = next((t for t in db_trades if t['symbol'] == symbol), None)
                    
                    if matching_trade:
                        # Ground the exit in DB
                        tm.record_exit(
                            symbol=symbol,
                            trade_id=matching_trade['trade_id'],
                            reason="EMERGENCY_LIQUIDATION",
                            current_price=float(pos.get('info', {}).get('markPrice', 0)),
                            order_response=order
                        )
                        print(f"  ✅ Grounded in Database.")
                    else:
                        print(f"  ✅ Position closed (No matching trade in DB).")
                        
                except Exception as close_e:
                    print(f"  ❌ Failed to clean/close {symbol}: {close_e}")

    except Exception as e:
        print(f"🚨 CRITICAL ERROR during liquidation: {e}")

    print("\n" + "=" * 80)
    print("🏁 EMERGENCY LIQUIDATION COMPLETE")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    emergency_liquidate()
