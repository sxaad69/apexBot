#!/usr/bin/env python3
import os
import sys
import asyncio
import ccxt.pro
from datetime import datetime
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))

async def test_portfolio_roe():
    load_dotenv()
    
    # 1. Setup Exchange (Testnet)
    exchange = ccxt.pro.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    
    if os.getenv('EXCHANGE_ENVIRONMENT') == 'testnet':
        exchange.set_sandbox_mode(True)
        print("🌐 Connected to Binance Testnet")
    else:
        print("🛑 WARNING: SYSTEM IS IN PRODUCTION MODE. Script aborted for safety.")
        return

    try:
        # 2. Get Initial Balance
        print("🔍 Fetching initial balance...")
        balance = await exchange.fetch_balance()
        initial_wallet = float(balance['info']['totalWalletBalance'])
        print(f"💰 Initial Wallet Balance: ${initial_wallet:.2f}")

        # 3. Open 5 Positions (Market Orders)
        symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT', 'BNB/USDT:USDT', 'ADA/USDT:USDT']
        print(f"🚀 Opening {len(symbols)} test positions...")
        
        amounts = {
            'BTC/USDT:USDT': 0.002,   # ~$140
            'ETH/USDT:USDT': 0.01,    # ~$25
            'SOL/USDT:USDT': 0.05,    # ~$9
            'BNB/USDT:USDT': 0.05,    # ~$30
            'ADA/USDT:USDT': 20.0     # ~$12
        }
        
        for symbol in symbols:
            try:
                amount = amounts[symbol]
                print(f"  - Market Buy {symbol} (Qty: {amount})")
                await exchange.create_market_order(symbol, 'buy', amount)
            except Exception as e:
                print(f"  ⚠️ Error opening {symbol}: {e}")

        print("\n📈 DASHBOARD STARTING... (Press Ctrl+C to stop and close all)")
        print("="*60)

        # 4. Monitor Loop
        print("\n📈 MONITORING STARTING... (Check your Binance Testnet Dashboard NOW!)")
        print("I will keep these positions open for 5 minutes.")
        
        for _ in range(300): # Run for 5 minutes
            # Fetch balance to get info blocks
            updated_bal = await exchange.fetch_balance()
            info = updated_bal.get('info', {})
            
            total_pnl = float(info.get('totalUnrealizedProfit', 0))
            total_margin = float(info.get('totalInitialMargin', 0))
            
            roe_percent = (total_pnl / total_margin * 100) if total_margin > 0 else 0
            
            print(f"[{datetime.now().strftime('%H:%M:%S')}] PnL: ${total_pnl:+.2f} | Margin: ${total_margin:.2f} | ROE: {roe_percent:.4f}%")
            
            await asyncio.sleep(1)

    except Exception as e:
        print(f"\n❌ Script Error: {e}")
    finally:
        # 5. WE ARE LEAVING POSITIONS OPEN so you can see them!
        print("\n🛑 Monitor stopped. Positions are still OPEN on your account.")
        await exchange.close()
        print("✅ Done.")

if __name__ == "__main__":
    asyncio.run(test_portfolio_roe())
