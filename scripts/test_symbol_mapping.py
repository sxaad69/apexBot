import ccxt
import os
from dotenv import load_dotenv

# Use .env.live which we know is working
load_dotenv('.env.live')

exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_API_SECRET'),
    'options': {'defaultType': 'future'}
})
exchange.set_sandbox_mode(True)

# Load markets first (required for mapping)
print("Loading markets...")
exchange.load_markets()

symbol = 'BTC/USDT'
try:
    market = exchange.market(symbol)
    print(f"\n--- SYMBOL TEST ---")
    print(f"Input Symbol: {symbol}")
    print(f"CCXT Canonical Symbol: {market['symbol']}")
    
    # Check what fetch_positions returns for this symbol
    positions = exchange.fetch_positions([symbol])
    if positions:
        print(f"Position Object Symbol: {positions[0]['symbol']}")
    else:
        print("No open position to check, but markets are loaded.")
except Exception as e:
    print(f"Error: {e}")
