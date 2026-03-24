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
    """
    config = Config()
    logger = MongoLogger(config)
    db = SQLiteManager(config)
    exchange = CCXTExchangeClient(config, logger)
    tm = TradeManager(config, db, exchange, logger)

    print("\n" + "!" * 80)
    print("⚠️  EMERGENCY LIQUIDATION INITIATED ⚠️")
    print("!" * 80 + "\n")

    # 1. Cancel ALL Open Orders (Clear SL/TP)
    print("🛡️  Step 1: Canceling all open orders...")
    try:
        # Fetch open orders to get symbols
        open_orders = exchange.exchange.fetch_open_orders()
        symbols_with_orders = list(set([o['symbol'] for o in open_orders]))
        
        if not symbols_with_orders:
            print("ℹ️  No open orders found.")
        else:
            for sym in symbols_with_orders:
                try:
                    exchange.exchange.cancel_all_orders(sym)
                    print(f"✅ Canceled all orders for {sym}")
                except Exception as e:
                    print(f"⚠️  Could not cancel orders for {sym}: {e}")
    except Exception as e:
        print(f"⚠️  Failed to fetch/cancel open orders: {e}")

    # 2. Fetch all open positions
    print("🔍 Step 2: Fetching open positions from Binance...")
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
                print(f"🔥 Closing {side.upper()} {symbol} ({contracts} contracts)...")
                
                try:
                    # Execute Market Close
                    order = exchange.close_position(symbol)
                    
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
                        print(f"✅ {symbol} Closed and Grounded in Database.")
                    else:
                        print(f"✅ {symbol} Closed (No matching trade in DB).")
                        
                except Exception as close_e:
                    print(f"❌ Failed to close {symbol}: {close_e}")

    except Exception as e:
        print(f"🚨 CRITICAL ERROR during liquidation: {e}")

    print("\n" + "=" * 80)
    print("🏁 EMERGENCY LIQUIDATION COMPLETE")
    print("=" * 80 + "\n")

if __name__ == "__main__":
    confirm = input("Are you ABSOLUTELY SURE you want to liquidate ALL positions? (YES/NO): ")
    if confirm == "YES":
        emergency_liquidate()
    else:
        print("Abort.")
