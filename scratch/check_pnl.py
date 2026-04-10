import os
import sys
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger

def check_pnl():
    config = Config()
    logger = MongoLogger(config)
    exchange = CCXTExchangeClient(config, logger)
    
    print("\n--- BINANCE ACCOUNT STATUS ---")
    
    # Fetch Balance
    balance = exchange.exchange.fetch_balance()
    unrealized_pnl = float(balance['info'].get('totalUnrealizedProfit', 0))
    total_margin_balance = float(balance['info'].get('totalMarginBalance', 0))
    total_wallet_balance = float(balance['info'].get('totalWalletBalance', 0))
    
    print(f"Total Wallet Balance: ${total_wallet_balance:.2f}")
    print(f"Total Margin Balance: ${total_margin_balance:.2f}")
    print(f"Total Unrealized PnL: ${unrealized_pnl:.2f}")
    
    # Fetch Positions
    positions = exchange.exchange.fetch_positions()
    active_positions = [p for p in positions if float(p.get('contracts', 0)) != 0]
    
    if not active_positions:
        print("\n✅ Zero active positions found.")
    else:
        print(f"\n⚠️ Found {len(active_positions)} active positions:")
        for pos in active_positions:
            print(f"- {pos['symbol']}: {pos['side']} {pos['contracts']} @ {pos['entryPrice']} (PnL: ${pos['unrealizedPnl']})")

if __name__ == "__main__":
    check_pnl()
