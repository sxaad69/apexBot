import os
import time
import ccxt
import dotenv
from pathlib import Path

# Load environment variables
dotenv.load_dotenv()

def run_isolated_test():
    print("🚀 INITIALIZING ISOLATED VERIFICATION TEST")
    
    # 1. Setup Exchange (Mirroring CCXTExchangeClient init)
    exchange = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    exchange.set_sandbox_mode(True)
    
    symbol = 'BTC/USDT:USDT'
    target_margin = 100.0 # 100 USDT
    leverage = 1
    
    try:
        # Fetch current price for quantity calculation
        ticker = exchange.fetch_ticker(symbol)
        price = ticker['last']
        quantity = (target_margin * leverage) / price
        
        # Precision formatting (Mirroring main.py logic)
        quantity = float(exchange.amount_to_precision(symbol, quantity))
        
        print(f"📊 Target: {target_margin} USDT | Price: {price} | Calculated Qty: {quantity}")
        
        # 2. CREATE ORDER (Phase 13 Footprint)
        print(f"📡 STEP 1: Sending Market Buy Order for {quantity} BTC...")
        order = exchange.create_order(
            symbol=symbol,
            type='market',
            side='buy',
            amount=quantity
        )
        print(f"✅ ORDER RESPONSE RECEIVED | ID: {order.get('id')} | Status: {order.get('status')}")
        
        # 3. VERIFICATION (Phase 15.1 Footprint)
        print(f"⏳ STEP 2: Sleeping for 1 second (Mirroring TradeManager)...")
        time.sleep(1)
        
        print(f"🔍 STEP 3: Calling fetch_positions for {symbol}...")
        # Note: fetch_positions([symbol]) is what the bot uses
        positions = exchange.fetch_positions([symbol])
        
        # Filtering logic (Mirroring ccxt_client.py:132)
        active_positions = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
        
        if active_positions:
            pos = active_positions[0]
            print(f"✅ VERIFICATION SUCCESS!")
            print(f"   Symbol: {pos['symbol']}")
            print(f"   Contracts: {pos['contracts']}")
            print(f"   Entry Price: {pos['entryPrice']}")
        else:
            print(f"🚨 VERIFICATION FAILED!")
            print(f"   Exchange reported 0 contracts for {symbol} after 1 second.")
            
            # Check all positions just in case of symbol mapping issues
            print("🔍 Checking ALL active positions as fallback...")
            all_positions = exchange.fetch_positions()
            real_active = [p for p in all_positions if abs(float(p.get('contracts', 0) or 0)) > 0]
            if real_active:
                print(f"⚠️ Found {len(real_active)} other active positions:")
                for p in real_active:
                    print(f"   - {p['symbol']}: {p['contracts']} contracts")
            else:
                print("   No active positions found anywhere on account.")

    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")

if __name__ == "__main__":
    run_isolated_test()
