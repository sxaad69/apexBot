import ccxt
import pandas as pd
from datetime import datetime, timedelta
import os

def download_ohlcv():
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    symbol = 'TAG/USDT'
    timeframe = '1m'
    
    # Entry was ~10:08, Exit was ~11:33
    # Fetch from 10:00 to 13:30
    start_dt = datetime(2026, 5, 2, 10, 0)
    since = int(start_dt.timestamp() * 1000)
    
    print(f"Downloading {symbol} OHLCV from {start_dt}...")
    
    all_ohlcv = []
    while since < int(datetime(2026, 5, 2, 13, 30).timestamp() * 1000):
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since, limit=500)
        if not ohlcv:
            break
        all_ohlcv.extend(ohlcv)
        since = ohlcv[-1][0] + 60000
        
    df = pd.DataFrame(all_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
    
    output_path = 'scratch/tag_analysis_ohlcv.csv'
    df.to_csv(output_path, index=False)
    print(f"Saved {len(df)} rows to {output_path}")

if __name__ == "__main__":
    download_ohlcv()
