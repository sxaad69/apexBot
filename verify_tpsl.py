import sqlite3
import ccxt
import os
from dotenv import load_dotenv
from datetime import datetime

# Load environment variables for exchange access
load_dotenv()

def verify_tpsl():
    db_path = 'data/apex_hunter.db'
    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        return

    # Initialize exchange for live price checks (optional but better for open trades)
    apiKey = os.getenv('BINANCE_API_KEY')
    secret = os.getenv('BINANCE_API_SECRET')
    is_testnet = os.getenv('EXCHANGE_ENVIRONMENT', 'testnet') == 'testnet'
    
    exchange = ccxt.binance({
        'apiKey': apiKey,
        'secret': secret,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    if is_testnet:
        exchange.set_sandbox_mode(True)

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Fetch all trades
        trades = cursor.execute("SELECT * FROM trades").fetchall()
        
        print(f"🔍 APEX HUNTER TP/SL VERIFICATION REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("="*100)
        print(f"{'SYMBOL':<20} | {'SIDE':<5} | {'STATUS':<8} | {'PNL % (ROE)':<12} | {'EXCEEDS 5%?'}")
        print("-"*100)
        
        exceeded_count = 0
        total_checked = 0
        
        # Cache for ticker prices to avoid redundant API calls
        ticker_cache = {}

        for row in trades:
            trade = dict(row)
            symbol = trade['symbol']
            side = trade['side'].lower()
            status = trade['status']
            leverage = trade.get('leverage', 1)
            entry_price = trade['entry_price']
            
            pnl_roe = 0.0
            
            if status == 'CLOSED':
                pnl_roe = trade.get('pnl_percent', 0.0)
            else:
                # For OPEN trades, try to get live price
                try:
                    if symbol not in ticker_cache:
                        ticker_cache[symbol] = exchange.fetch_ticker(symbol)['last']
                    current_price = ticker_cache[symbol]
                    
                    price_diff = (current_price - entry_price) / entry_price
                    if side == 'sell':
                        price_diff = -price_diff
                    
                    pnl_roe = price_diff * leverage * 100
                except Exception as e:
                    pnl_roe = 0.0 # Could not fetch live price

            # Check if it exceeds -5% (meaning it's lower than -5, e.g. -6, -10)
            exceeds = "⚠️ YES" if pnl_roe <= -5.0 else "✅ No"
            if pnl_roe <= -5.0:
                exceeded_count += 1
            
            total_checked += 1
            print(f"{symbol:<20} | {side:<5} | {status:<8} | {pnl_roe:>10.2f}% | {exceeds}")

        print("="*100)
        print(f"SUMMARY: {exceeded_count} out of {total_checked} trades exceeded the -5% ROE threshold.")
        
    except Exception as e:
        print(f"Error during verification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    verify_tpsl()
