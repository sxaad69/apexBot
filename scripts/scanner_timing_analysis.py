import ccxt
import pandas as pd
from datetime import datetime, timedelta

def main():
    print("Analyzing timeline of POWER/USDT pump over the last 5 days...")
    exchange = ccxt.binance({
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })

    symbol = 'POWER/USDT'
    # Fallback to spot if futures doesn't have enough history or format is weird, 
    # but we'll try futures generic format first
    try:
        # Fetch daily candles (OHLCV) for the last 7 days
        ohlcv = exchange.fetch_ohlcv(symbol + ':USDT', timeframe='1d', limit=7)
    except:
        try:
            # Maybe it's a spot pair that pumped or format issue
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1d', limit=7)
        except Exception as e:
            print(f"Error fetching data for {symbol}: {e}")
            return

    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.strftime('%Y-%m-%d')
    
    # Calculate daily percentage change and USDT volume estimate
    # CCXT volume is usually base asset. USDT volume = volume * (open+close)/2
    df['avg_price'] = (df['open'] + df['close']) / 2
    df['usdt_volume'] = df['volume'] * df['avg_price']
    df['pct_change'] = ((df['close'] - df['open']) / df['open']) * 100

    print("\n--- TIMELINE ANALYSIS ---")
    threshold = 1_000_000 # Config threshold
    
    for i, row in df.iterrows():
        vol = row['usdt_volume']
        pct = row['pct_change']
        date = row['date']
        
        status = "🟢 ON RADAR" if vol > threshold else "🔴 INVISIBLE"
        print(f"[{date}] Price: ${row['close']:.4f} | Change: {pct:>+7.2f}% | Vol: ${vol:,.0f} -> {status}")
        
    print("\nConclusion:")
    print("If '🟢 ON RADAR' appears on days with large positive 'Change', the bot caught the pump live.")
    print("If '🟢 ON RADAR' only appears on the very last day after the pump finished, it's lagging.")

if __name__ == "__main__":
    main()
