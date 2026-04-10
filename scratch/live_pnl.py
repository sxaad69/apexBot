#!/usr/bin/env python3
import os
import sys
import asyncio
import ccxt.pro
from datetime import datetime
from dotenv import load_dotenv

# Add project root to path for config/exchange imports
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))

from config.config import Config

async def live_pnl_monitor():
    """
    High-Frequency P&L Monitor using WebSockets (ccxt.pro)
    Provides near-instant updates by calculating P&L in RAM.
    """
    config = Config()
    
    # Initialize Exchange with WebSocket support
    exchange = ccxt.pro.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    
    if config.EXCHANGE_ENVIRONMENT == 'testnet':
        exchange.set_sandbox_mode(True)
        print("🌐 Connected to Binance Testnet (WebSocket)")
    else:
        print("🚀 Connected to Binance Production (WebSocket)")

    # Shared state for the monitor
    positions_cache = {}
    last_ui_update = 0

    async def fetch_initial_positions():
        """Fetch all currently open positions from Binance."""
        print("🔍 Syncing active positions from Binance...")
        try:
            balance = await exchange.fetch_balance()
            # Binance specific: positions are under 'info' -> 'positions' 
            # or we can use fetch_positions()
            raw_positions = await exchange.fetch_positions()
            
            for pos in raw_positions:
                contracts = float(pos.get('contracts', 0))
                if contracts != 0:
                    symbol = pos['symbol']
                    positions_cache[symbol] = {
                        'symbol': symbol,
                        'side': pos['side'],
                        'amount': contracts,
                        'entry_price': float(pos['entryPrice']),
                        'leverage': float(pos.get('leverage', 1)),
                        'current_price': float(pos.get('markPrice', 0)),
                        'unrealized_pnl': float(pos.get('unrealizedPnl', 0)),
                        'last_update': datetime.now()
                    }
            return len(positions_cache)
        except Exception as e:
            print(f"❌ Error fetching positions: {e}")
            return 0

    async def stream_price(symbol):
        """WebSocket task: continuously watch the price for one symbol."""
        while True:
            try:
                ticker = await exchange.watch_ticker(symbol)
                if symbol in positions_cache:
                    new_price = ticker['last']
                    pos = positions_cache[symbol]
                    
                    # Local P&L Calculation (Ultra-fast)
                    entry = pos['entry_price']
                    side_mult = 1 if pos['side'].lower() == 'long' else -1
                    
                    price_diff = (new_price - entry) / entry
                    pos['current_price'] = new_price
                    pos['unrealized_pnl'] = pos['amount'] * entry * price_diff * side_mult
                    pos['roe_pct'] = price_diff * 100 * side_mult * pos['leverage']
                    pos['last_update'] = datetime.now()
                    
            except Exception as e:
                print(f"⚠️ WSS Stream Error ({symbol}): {e}")
                await asyncio.sleep(5)

    def draw_ui():
        """Draws the live dashboard in the terminal."""
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"📊 APEX HUNTER LIVE P&L MONITOR | {datetime.now().strftime('%H:%M:%S.%f')[:-3]}")
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        if not positions_cache:
            print("\n   ∅ No active positions found. Monitoring for new trades...")
        else:
            header = f"{'Symbol':<15} {'Side':<8} {'Entry':<10} {'Current':<10} {'ROE%':<10} {'Unrealized P&L':<15}"
            print(header)
            print("-" * len(header))
            
            total_pnl = 0
            for symbol, pos in positions_cache.items():
                roe = pos.get('roe_pct', 0)
                pnl = pos.get('unrealized_pnl', 0)
                total_pnl += pnl
                
                color_start = "\033[92m" if pnl >= 0 else "\033[91m"
                color_end = "\033[0m"
                
                print(f"{symbol:<15} "
                      f"{pos['side'].upper():<8} "
                      f"{pos['entry_price']:<10.4f} "
                      f"{pos['current_price']:<10.4f} "
                      f"{color_start}{roe:>8.2f}%{color_end}   "
                      f"{color_start}${pnl:>13.2f}{color_end}")
            
            print("-" * len(header))
            print(f"{'TOTAL PORTFOLIO P&L:':<45} \033[1m${total_pnl:.2f}\033[0m")
        
        print(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("Press Ctrl+C to exit monitor.")

    # --- Start Lifecycle ---
    count = await fetch_initial_positions()
    if count == 0:
        print("No open positions. Please open a trade to see live data.")
    
    # Spawn streamers for all found symbols
    for symbol in positions_cache.keys():
        asyncio.create_task(stream_price(symbol))

    # Main UI loop
    try:
        while True:
            draw_ui()
            # Update UI frequently (approx 10 times per second for smooth feel)
            await asyncio.sleep(0.1) 
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        await exchange.close()

if __name__ == "__main__":
    try:
        asyncio.run(live_pnl_monitor())
    except KeyboardInterrupt:
        pass
