import ccxt
import pandas as pd
from datetime import datetime, timedelta
import time

def get_top_performers(limit=10):
    print(f"[{datetime.now()}] Initializing Binance Futures client...")
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True
    })

    try:
        print("Fetching tickers to identify active markets...")
        tickers = exchange.fetch_tickers()
        
        # Filter for USDT pairs and sort by volume
        usdt_tickers = [
            {'symbol': s, 'volume': t['quoteVolume']} 
            for s, t in tickers.items() 
            if s.endswith('/USDT:USDT') and t['quoteVolume'] is not None
        ]
        
        # Take the top 100 by volume to avoid low-liquidity noise
        top_100 = sorted(usdt_tickers, key=lambda x: x['volume'], reverse=True)[:100]
        symbols = [t['symbol'] for t in top_100]
        
        print(f"Analyzing last 1-hour performance for top 100 volume pairs...")
        results = []
        
        for symbol in symbols:
            try:
                # Fetch last 2 candles of 1h timeframe
                ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=2)
                if len(ohlcv) < 2:
                    continue
                
                open_price = ohlcv[0][1] # Open of the previous hour
                current_price = ohlcv[1][4] # Close of the current (incomplete) candle
                
                change_pct = ((current_price - open_price) / open_price) * 100
                results.append({
                    'symbol': symbol.split(':')[0],
                    'change_%': change_pct,
                    'price': current_price,
                    'volume_24h': next(t['volume'] for t in top_100 if t['symbol'] == symbol)
                })
                # Small sleep to respect rate limits if needed, though fetch_ohlcv for 100 is fine
            except Exception as e:
                continue

        df = pd.DataFrame(results)
        
        # Top Longs (Gains)
        top_longs = df.sort_values(by='change_%', ascending=False).head(limit)
        
        # Top Shorts (Losses)
        top_shorts = df.sort_values(by='change_%', ascending=True).head(limit)
        
        print("\n" + "="*60)
        print(f"🚀 TOP PERFORMERS (LAST 1 HOUR) - {datetime.now().strftime('%H:%M:%S')}")
        print("="*60)
        print("\n🟢 TOP LONGS (MOVERS UP):")
        print(top_longs[['symbol', 'change_%', 'price']].to_string(index=False))
        
        print("\n🔴 TOP SHORTS (MOVERS DOWN):")
        print(top_shorts[['symbol', 'change_%', 'price']].to_string(index=False))
        print("="*60)

    except Exception as e:
        print(f"Error fetching data: {e}")

if __name__ == "__main__":
    get_top_performers()
