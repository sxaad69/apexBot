import os
import sys
import asyncio
import logging
import time
from datetime import datetime
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))

from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient
from notifications import TelegramNotificationManager
from risk.layers.portfolio_profit_ratchet import PortfolioProfitRatchet

async def run_live_verification():
    load_dotenv()
    
    print("💎 INITIALIZING LIVE STRESS TEST (REAL CLASSES)")
    
    # 1. Real Config with Testnet forcing
    config = Config()
    config.PROFIT_RATCHET_ENABLED = True
    config.PROFIT_RATCHET_ACTIVATION = -5.0  # ACTIVATE IMMEDIATELY (even at loss)
    config.PROFIT_RATCHET_TRAILING = 0.01
    config.PROFIT_RATCHET_FLOOR = -10.0
    config.PROFIT_RATCHET_COOLDOWN = 5
    config.FUTURES_EXCHANGE = 'binance'
    config.PROFIT_RATCHET_SLIPPAGE_BUFFER = 0.0  # 0 costs for test
    config.FUTURES_FEE_PERCENT = 0.0             # 0 fees for test
    config.EXCHANGE_ENVIRONMENT = 'testnet'

    # 2. Real Logger & Database
    logger = MongoLogger(config)
    db = logger.db # SQLiteManager instance
    
    # 3. Real Exchange Client
    exchange = CCXTExchangeClient(config, logger, 'binance')
    
    # 4. Real Telegram (Mute message sending for test if needed, but we'll leave it)
    telegram = TelegramNotificationManager(config, logger)
    
    # 5. Create the Ratchet Instance
    ratchet = PortfolioProfitRatchet(config, db, exchange, logger, telegram)
    
    try:
        # A. Open 5 Test Positions
        # Use symbols known to work on testnet with these sizes
        symbols = ['BTC/USDT:USDT', 'ETH/USDT:USDT', 'BNB/USDT:USDT', 'ADA/USDT:USDT', 'XRP/USDT:USDT']
        # Approx Notional: BTC($140), ETH($25), BNB($30), ADA($12), XRP($30)
        amounts = {'BTC/USDT:USDT': 0.002, 'ETH/USDT:USDT': 0.01, 'BNB/USDT:USDT': 0.05, 'ADA/USDT:USDT': 20.0, 'XRP/USDT:USDT': 50.0}
        
        print("\n🚀 [STAGE 1] Opening test portfolio...")
        for symbol in symbols:
            try:
                print(f"  🛒 Buying {symbol}...")
                await asyncio.to_thread(exchange.exchange.create_market_order, symbol, 'buy', amounts[symbol])
            except Exception as e:
                print(f"  ⚠️ Error opening {symbol}: {e}")

        # B. Run the actual Monitor Loop inside a task
        print("\n🚀 [STAGE 2] Starting REAL Profit Ratchet Monitor...")
        print(f"TARGET ACTIVATION: {config.PROFIT_RATCHET_ACTIVATION}%")
        
        monitor_task = asyncio.create_task(ratchet.monitor_loop())
        
        # C. Watch for state changes
        timeout = 60 # 60 seconds max
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Check if triggered
            if ratchet.ratchet_active:
                print(f"📈 [LIVE] Ratchet ACTIVATED! Peak ROE: {ratchet.peak_roe:.4f}%")
                
            if ratchet.is_liquidating:
                print("🚨 [LIVE] PROFIT RATCHET TRIPPED! Mass Liquidation in progress...")
                break
                
            await asyncio.sleep(1)
        
        # Give it 10 seconds to finish closing all orders
        if ratchet.is_liquidating:
            print("⏳ Waiting for liquidation to complete...")
            await asyncio.sleep(10)
        else:
            print("⏲️ Timeout reached. Forcing stop.")

        # D. Verification
        print("\n🚀 [STAGE 3] Final Position Sweep...")
        all_pos = await asyncio.to_thread(exchange.get_positions)
        leftovers = [p for p in all_pos if abs(float(p.get('contracts', 0))) > 0]
        
        if not leftovers:
            print("✅ VERIFIED: All positions were closed by the Ratchet Layer.")
        else:
            print(f"❌ FAILED: {len(leftovers)} positions still open!")
            # Emergency cleanup
            for p in leftovers:
                s = p['symbol']
                c = abs(float(p['contracts']))
                side = 'sell' if p['side'].lower() in ('buy', 'long') else 'buy'
                await asyncio.to_thread(exchange.exchange.create_market_order, s, side, c, None, {'reduceOnly': True})

        # E. Check DB Cooldown
        cooldown = db.get_setting('portfolio_ratchet_cooldown_until')
        print(f"📅 DB Cooldown Check: {cooldown}")
        if cooldown:
            print("✅ VERIFIED: Cooldown period successfully logged to SQLite.")

    except Exception as e:
        print(f"💥 CRITICAL ERROR during test: {e}")
    finally:
        ratchet.stop_event.set()
        if 'monitor_task' in locals():
            monitor_task.cancel()
        await exchange.exchange.close()
        print("\n🏁 Stress Test Complete.")

if __name__ == "__main__":
    asyncio.run(run_live_verification())
