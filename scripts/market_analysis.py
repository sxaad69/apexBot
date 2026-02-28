import ccxt
import pandas as pd
import time
from datetime import datetime, timedelta

def main():
    print("Initializing Binance Futures connection...")
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })

    tickers = exchange.fetch_tickers()
    
    # Filter for active USDT perpetuals
    usdt_pairs = []
    for symbol, ticker in tickers.items():
        if symbol.endswith(':USDT') and ticker.get('quoteVolume', 0) > 0:
            usdt_pairs.append({
                'symbol': symbol,
                'volume': ticker['quoteVolume'],
                'last': ticker.get('last', 0)
            })

    # Sort by volume to find what the bot's Auto-Scanner would pick
    usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)
    top_30_auto_scan = [p['symbol'] for p in usdt_pairs[:30]]
    
    print(f"\n--- Bot's Hypothetical Auto-Scanned Top 30 (By 24h Volume) ---")
    print(", ".join(top_30_auto_scan))

    print("\nFetching 5-day performance for Top 150 volume pairs to find true gainers/losers...")
    results = []
    since = int((datetime.now() - timedelta(days=5)).timestamp() * 1000)
    
    # Only check top 150 to save time and API rate limits
    for pair in usdt_pairs[:150]:
        symbol = pair['symbol']
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=6)
            if len(ohlcv) >= 5:
                # Price 5 days ago (open of the first candle)
                price_5d_ago = ohlcv[-5][1] 
                # Current price (close of the last candle)
                price_now = ohlcv[-1][4]
                
                if price_5d_ago > 0:
                    pct_change = ((price_now - price_5d_ago) / price_5d_ago) * 100
                    results.append({
                        'symbol': symbol,
                        'change': pct_change,
                        'in_top_30': symbol in top_30_auto_scan,
                        'volume_rank': usdt_pairs.index(pair) + 1
                    })
        except Exception as e:
            pass
        time.sleep(0.05) # Rate limit safety
        
    # Sort by % change
    results.sort(key=lambda x: x['change'], reverse=True)
    
    print("\n--- TOP 10 GAINERS (Last 5 Days) ---")
    for r in results[:10]:
        check = "✅ YES" if r['in_top_30'] else "❌ NO"
        print(f"{r['symbol']:<12} | {r['change']:>+7.2f}% | Caught by Auto-Scanner? {check} (Vol Rank: #{r['volume_rank']})")

    print("\n--- TOP 10 LOSERS (Last 5 Days) ---")
    for r in results[-10:]:
        check = "✅ YES" if r['in_top_30'] else "❌ NO"
        print(f"{r['symbol']:<12} | {r['change']:>+7.2f}% | Caught by Auto-Scanner? {check} (Vol Rank: #{r['volume_rank']})")

if __name__ == "__main__":
    main()
