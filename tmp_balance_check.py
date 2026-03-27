import os
from dotenv import load_dotenv
import ccxt

load_dotenv()

binance = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_API_SECRET'),
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})
binance.set_sandbox_mode(True)

try:
    balance = binance.fetch_balance()
    print("USDT Balance Info:")
    usdt = balance.get('USDT', {})
    print(f"Free: {usdt.get('free')}")
    print(f"Used: {usdt.get('used')}")
    print(f"Total: {usdt.get('total')}")
    
    # Let's check open orders
    open_orders = binance.fetch_open_orders()
    print(f"Open Orders Count: {len(open_orders)}")
    total_locked_in_orders = 0
    for order in open_orders:
        if order.get('type') == 'stop_market' or order.get('type') == 'limit':
            # rough estimate
            pass
            
except Exception as e:
    print(f"Error: {e}")
