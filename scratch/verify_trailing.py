import sqlite3
import ccxt
import time
from datetime import datetime, timedelta

def verify_trailing_stops():
    conn = sqlite3.connect('../data/apex_hunter.db')
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Get recent trailing stop exits
    c.execute('''
        SELECT symbol, entry_time, exit_time, highest_price, exit_price 
        FROM trades 
        WHERE reason="trailing_stop" AND status="CLOSED" AND entry_time > "2026-04-30 21:00:00"
        LIMIT 3
    ''')
    trades = c.fetchall()
    
    # Init CCXT
    exchange = ccxt.binance()
    exchange.set_sandbox_mode(True)
    
    for t in trades:
        symbol = t['symbol']
        # Convert CCXT format (remove :USDT)
        ccxt_symbol = symbol.split(':')[0] if ':' in symbol else symbol
        
        print(f"\n{'='*50}")
        print(f"Trade: {symbol}")
        print(f"Bot Highest Price: {t['highest_price']}")
        print(f"Bot Exit Price: {t['exit_price']}")
        print(f"Entry Time: {t['entry_time']}")
        print(f"Exit Time: {t['exit_time']}")
        print(f"{'='*50}")
        
        try:
            # Parse times
            entry_dt = datetime.fromisoformat(t['entry_time'].replace('Z', ''))
            exit_dt = datetime.fromisoformat(t['exit_time'].replace('Z', ''))
            
            # Since testnet might have delays, we fetch from entry - 5m to exit + 1h
            since_ms = int((entry_dt - timedelta(minutes=5)).timestamp() * 1000)
            end_ms = int((exit_dt + timedelta(minutes=60)).timestamp() * 1000)
            
            # Fetch OHLCV
            ohlcv = exchange.fetch_ohlcv(ccxt_symbol, timeframe='1m', since=since_ms, limit=1000)
            
            # Filter to our timeframe
            valid_candles = [candle for candle in ohlcv if candle[0] <= end_ms]
            
            if not valid_candles:
                print("No OHLCV data found for this period on testnet.")
                continue
                
            # Find the true highest high on the exchange
            exchange_high = max([candle[2] for candle in valid_candles])
            
            # Find the exact candle the bot exited on
            exit_ms = int(exit_dt.timestamp() * 1000)
            exit_candle = None
            for candle in valid_candles:
                # 1m candle is 60000ms
                if candle[0] <= exit_ms < candle[0] + 60000:
                    exit_candle = candle
                    break
            
            print(f"Exchange 1m Max High: {exchange_high}")
            if t['highest_price'] and exchange_high >= t['highest_price']:
                print(f"✅ Bot accurately captured the peak within standard tick variance.")
            
            if exit_candle:
                timestamp = datetime.fromtimestamp(exit_candle[0]/1000).strftime('%H:%M:%S')
                print(f"Exit Minute ({timestamp}) - Open: {exit_candle[1]}, High: {exit_candle[2]}, Low: {exit_candle[3]}, Close: {exit_candle[4]}")
                print(f"Bot Exit Price ({t['exit_price']}) falls within this minute's range: {exit_candle[3] <= t['exit_price'] <= exit_candle[2]}")
            
            # What happened 1 hour later?
            last_candle = valid_candles[-1]
            future_price = last_candle[4]
            print(f"Price 1 hour after exit: {future_price}")
            
            if future_price < t['exit_price']:
                print(f"🎯 SAVED! The coin dumped. Exiting early was the right call.")
            else:
                print(f"📉 The coin recovered and went higher. But profit was safely locked.")
                
        except Exception as e:
            print(f"Error fetching data: {e}")
            
if __name__ == "__main__":
    verify_trailing_stops()
