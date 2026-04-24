import sys
import os
sys.path.append(os.getcwd())
from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient

config = Config()
logger = MongoLogger(config)
exchange = CCXTExchangeClient(config, logger, config.FUTURES_EXCHANGE)

try:
    print(f"Connecting to {config.FUTURES_EXCHANGE.upper()} ({config.EXCHANGE_ENVIRONMENT})...")
    positions = exchange.exchange.fetch_positions()
    
    total_unrealized = 0
    active_positions = []
    
    for pos in positions:
        pnl = float(pos.get('unrealizedPnl', 0) or pos.get('info', {}).get('unrealizedProfit', 0))
        size = float(pos.get('contracts', 0) or pos.get('info', {}).get('positionAmt', 0))
        
        if abs(size) > 0:
            symbol = pos.get('symbol')
            side = 'LONG' if size > 0 else 'SHORT'
            total_unrealized += pnl
            active_positions.append({
                'symbol': symbol,
                'side': side,
                'size': size,
                'pnl': pnl
            })
            
    print(f"\n=== BINANCE LIVE FUTURES POSITIONS ===")
    print(f"Total Unrealized P&L: ${total_unrealized:.2f}")
    print(f"Active Pairs: {len(active_positions)}")
    
    if active_positions:
        print("\n--- Top 10 by P&L ---")
        sorted_pos = sorted(active_positions, key=lambda x: x['pnl'], reverse=True)
        for p in sorted_pos[:10]:
            print(f" {p['symbol']} ({p['side']}): ${p['pnl']:.2f}")
    else:
        print("No active positions found on exchange.")

except Exception as e:
    print(f"Error fetching data from Binance: {e}")

