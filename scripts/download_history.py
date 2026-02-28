#!/usr/bin/env python3
"""
APEX HUNTER V14 - Historical Data Downloader
Downloads 180 days of OHLCV data for multiple coins and timeframes via CCXT
and caches it into the SQLite database to prevent API rate limits during bulk backtesting.
"""

import os
import sys
import time
import sqlite3
import argparse
from datetime import datetime, timedelta
import pandas as pd
import ccxt

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config

# Default settings
DEFAULT_COINS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT', 
                 'ADA/USDT', 'AVAX/USDT', 'DOGE/USDT', 'LINK/USDT', 'MATIC/USDT']
DEFAULT_TIMEFRAMES = ['3m', '5m', '15m', '30m', '1h']
DAYS_TO_FETCH = 180
BATCH_SIZE = 1000  # CCXT limit per request for Binance

def init_db(db_path):
    """Ensure the historical_data table exists."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS historical_data (
            symbol TEXT NOT NULL,
            timeframe TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (symbol, timeframe, timestamp)
        )
    ''')
    # Optimizing queries
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_hist_sym_tf ON historical_data(symbol, timeframe)')
    conn.commit()
    return conn

def get_latest_timestamp(conn, symbol, timeframe):
    """Find the most recent fetched candle for sequential updates."""
    cursor = conn.cursor()
    cursor.execute('''
        SELECT MAX(timestamp) FROM historical_data 
        WHERE symbol = ? AND timeframe = ?
    ''', (symbol, timeframe))
    row = cursor.fetchone()
    return row[0] if row[0] is not None else None

def download_data(exchange, symbol, timeframe, since_ms, conn):
    """Download OHLCV data using pagination and save to DB."""
    all_ohlcv = []
    current_since = since_ms
    
    print(f"  Downloading {symbol} [{timeframe}] since {pd.to_datetime(since_ms, unit='ms')}...")
    
    while True:
        try:
            # Add a small delay to respect rate limits
            time.sleep(exchange.rateLimit / 1000.0) 
            
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe, since=current_since, limit=BATCH_SIZE)
            
            if not ohlcv:
                break
                
            all_ohlcv.extend(ohlcv)
            
            # The last candle's timestamp + 1 ms ensures we don't fetch duplicates
            last_timestamp = ohlcv[-1][0]
            if last_timestamp <= current_since:
                # API returned exactly what we asked for or older data (shouldn't happen, but safeguard)
                break
                
            current_since = last_timestamp + 1
            
            # If we fetched fewer than BATCH_SIZE, we've hit the present
            if len(ohlcv) < BATCH_SIZE:
                break
                
            print(f"    Fetched {len(all_ohlcv)} candles... (Latest: {pd.to_datetime(last_timestamp, unit='ms')})")
            
        except ccxt.NetworkError as e:
            print(f"    Network error: {e}. Retrying in 5 seconds...")
            time.sleep(5)
        except ccxt.ExchangeError as e:
            print(f"    Exchange error: {e}. Stopping fetch for {symbol} {timeframe}.")
            break
        except Exception as e:
            print(f"    Unexpected error: {e}. Stopping fetch for {symbol} {timeframe}.")
            break

    if all_ohlcv:
        # Save to database using executemany for bulk insert
        cursor = conn.cursor()
        
        # Prepare data: (symbol, timeframe, timestamp, open, high, low, close, volume)
        db_data = [(symbol, timeframe, row[0], row[1], row[2], row[3], row[4], row[5]) for row in all_ohlcv]
        
        cursor.executemany('''
            INSERT OR REPLACE INTO historical_data 
            (symbol, timeframe, timestamp, open, high, low, close, volume) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', db_data)
        
        conn.commit()
        print(f"  ✅ Saved {len(all_ohlcv)} candles to db. Total unique: {len(db_data)}")
    else:
        print(f"  ⚡ Already up to date.")

def main():
    parser = argparse.ArgumentParser(description='Download and cache historical OHLCV data.')
    parser.add_argument('--days', type=int, default=DAYS_TO_FETCH, help=f'Days to fetch (default: {DAYS_TO_FETCH})')
    args = parser.parse_args()
    
    # Init Exchange
    exchange = ccxt.binance({
        'enableRateLimit': True,
        'options': {'defaultType': 'future'} # We trade futures
    })

    # Init DB
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'apex_hunter.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = init_db(db_path)
    
    print("=" * 70)
    print(f"  APEX HUNTER V14 - HISTORICAL DATA DOWNLOADER")
    print(f"  Target: {args.days} Days | DB: {db_path}")
    print("=" * 70)

    start_time = int((datetime.now() - timedelta(days=args.days)).timestamp() * 1000)

    for symbol in DEFAULT_COINS:
        print(f"\n🪙 Processing {symbol}...")
        for tf in DEFAULT_TIMEFRAMES:
            # Check if we already have partial data to resume from
            latest_db_ts = get_latest_timestamp(conn, symbol, tf)
            
            # If we have data, and it's newer than our start time, resume from there
            if latest_db_ts and latest_db_ts > start_time:
                since_ms = latest_db_ts + 1
            else:
                since_ms = start_time
                
            # Only download if we are more than 1 timeframe candle away from present
            # Roughly estimating current time in ms
            now_ms = int(datetime.now().timestamp() * 1000)
            
            # Convert timeframe string to rough ms (e.g. '15m' -> 15 * 60 * 1000)
            tf_num = int(''.join(filter(str.isdigit, tf)))
            tf_unit = ''.join(filter(str.isalpha, tf))
            tf_ms = tf_num * 60 * 1000 if tf_unit == 'm' else tf_num * 60 * 60 * 1000
            
            if (now_ms - since_ms) > tf_ms:
                download_data(exchange, symbol, tf, since_ms, conn)
            else:
                print(f"  ⚡ {symbol} [{tf}] already up to date.")

    conn.close()
    print("\n✅ All downloads complete.")

if __name__ == '__main__':
    main()
