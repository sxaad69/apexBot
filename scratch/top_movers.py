import ccxt
import pandas as pd
from datetime import datetime, timedelta

def get_top_movers():
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    print("Fetching tickers...")
    tickers = exchange.fetch_tickers()
    
    # Filter for USDT pairs with volume > 1M
    usdt_tickers = {s: t for s, t in tickers.items() if s.endswith('/USDT:USDT') and t['quoteVolume'] > 1000000}
    
    results = []
    print(f"Analyzing {len(usdt_tickers)} symbols for 1-hour change...")
    
    # To get 1h change, we use the 24h ticker's 'last' vs 'open' is not enough
    # We need OHLCV for the last hour
    for symbol in list(usdt_tickers.keys())[:50]: # Top 50 by volume for speed
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', limit=2)
            if len(ohlcv) < 2:
                continue
            
            last_price = ohlcv[-1][4]
            prev_hour_close = ohlcv[-2][4]
            change_pct = ((last_price - prev_hour_close) / prev_hour_close) * 100
            
            results.append({
                'symbol': symbol,
                '1h_change': change_pct,
                'current_price': last_price,
                'volume': usdt_tickers[symbol]['quoteVolume']
            })
        except:
            continue
            
    df = pd.DataFrame(results)
    if df.empty:
        print("No data found.")
        return
        
    top_gainers = df.sort_values(by='1h_change', ascending=False).head(10)
    top_losers = df.sort_values(by='1h_change', ascending=True).head(10)
    
    print("\n🚀 TOP 10 GAINERS (LAST 1 HOUR)")
    print(top_gainers[['symbol', '1h_change', 'volume']].to_string(index=False))
    
    print("\n📉 TOP 10 LOSERS (LAST 1 HOUR)")
    print(top_losers[['symbol', '1h_change', 'volume']].to_string(index=False))

if __name__ == "__main__":
    get_top_movers()
