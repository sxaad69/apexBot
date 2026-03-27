import os
from dotenv import load_dotenv
import ccxt

load_dotenv()
binance = ccxt.binance({'options': {'defaultType': 'future'}})
binance.set_sandbox_mode(True)

try:
    markets = binance.load_markets()
    for symbol in ['1000000BOB/USDT:USDT', 'REZ/USDT:USDT', 'TST/USDT:USDT']:
        if symbol in markets:
            m = markets[symbol]
            print(f"--- {symbol} ---")
            print(f"Min Cost (Notional): {m['limits']['cost']['min']}")
            print(f"Max Amount (Quantity): {m['limits']['amount']['max']}")
        else:
            print(f"{symbol} not found")
except Exception as e:
    print(f"Error: {e}")
