import os
import sys
import sqlite3
import json
from datetime import datetime, timedelta
from typing import Dict, List, Any
import pandas as pd

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.config import Config
    from database.sqlite_manager import SQLiteManager
    from exchange.ccxt_client import CCXTExchangeClient
    from bot_logging.logger import Logger
except ImportError as e:
    print(f"❌ Error: Could not import bot modules: {e}")
    sys.exit(1)

def format_pnl(amount: float) -> str:
    color = "\033[92m" if amount >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}{amount:+.2f}%{reset}"

def analyze_rejection(exchange, rejection):
    """Analyze a single rejection against subsequent market movement"""
    symbol = rejection['symbol']
    # SQLite timestamp is UTC
    timestamp_str = rejection['timestamp']
    try:
        # Handle cases like '2026-03-02 07:15:30'
        ts = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            # Handle ISO format
            ts = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
        except:
            return None

    # Convert to ms for CCXT
    start_ms = int(ts.timestamp() * 1000)
    
    try:
        # Fetch OHLCV for the 4 hours following the rejection
        # 1m candles for precision
        ohlcv = exchange.exchange.fetch_ohlcv(symbol, '1m', since=start_ms, limit=240)
        if not ohlcv:
            return None
            
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        
        # Logic for Strategy Skips (Price = 0.0, Side = 'all')
        entry_price = rejection['entry_price']
        side = rejection['side']
        
        # If skip occurred before signal generation, use first candle open as baseline
        if entry_price == 0.0:
            entry_price = df['open'].iloc[0]
            
        if side == 'buy':
            # Max potential profit (highest price reached)
            max_price = df['high'].max()
            potential_pnl = (max_price - entry_price) / entry_price * 100
            # Min draw (lowest price reached)
            max_draw = (df['low'].min() - entry_price) / entry_price * 100
        elif side == 'sell':
            # Short: profit if price goes down
            min_price = df['low'].min()
            potential_pnl = (entry_price - min_price) / entry_price * 100
            # Min draw (highest price reached)
            max_draw = (entry_price - df['high'].max()) / entry_price * 100
        else:
            # side == 'all' (Strategy Skip - direction unknown)
            # Calculate BOTH potential Long and Short move
            long_pnl = (df['high'].max() - entry_price) / entry_price * 100
            short_pnl = (entry_price - df['low'].min()) / entry_price * 100
            
            # Report the best possible alpha missed
            if long_pnl >= short_pnl:
                potential_pnl = long_pnl
                max_draw = (df['low'].min() - entry_price) / entry_price * 100
            else:
                potential_pnl = short_pnl
                max_draw = (entry_price - df['high'].max()) / entry_price * 100
            
        return {
            'potential_pnl': potential_pnl,
            'max_drawdown': max_draw,
            'best_exit_price': df['high'].max() if side != 'sell' else df['low'].min()
        }
    except Exception as e:
        # print(f"Error analyzing {symbol}: {e}")
        return None

def main():
    print("=" * 70)
    print("      🔍 APEX HUNTER V14 - MISSED ALPHA AUDITOR 🔍")
    print("      (Analyzing rejections vs subsequent market performance)")
    print("=" * 70)

    from dotenv import load_dotenv
    load_dotenv()
    
    config = Config()
    sqlite_mgr = SQLiteManager(config)
    
    # Initialize CCXT client (public data only)
    logger = Logger(config)
    exchange = CCXTExchangeClient(config, logger)

    print("\n📂 Fetching rejections from activity_log.db...")
    
    rejections = []
    try:
        conn = sqlite3.connect(sqlite_mgr.log_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM rejections ORDER BY timestamp DESC LIMIT 100")
        for row in cursor.fetchall():
            rejections.append(dict(row))
        conn.close()
    except Exception as e:
        print(f"❌ Database error: {e}")
        return

    if not rejections:
        print("ℹ️  No rejections found in database.")
        return

    print(f"✅ Found {len(rejections)} recent events. Analyzing alpha...")
    
    results = []
    # impact maps: { category: { layer_name: { stats } } }
    impact = {
        'STRATEGY': {},
        'RISK': {}
    }
    
    for rej in rejections:
        analysis = analyze_rejection(exchange, rej)
        if analysis:
            res = {**rej, **analysis}
            results.append(res)
            
            # Determine category
            category = 'STRATEGY' if 'STRATEGY' in rej['reason'] else 'RISK'
            layer = rej['layer']
            
            if layer not in impact[category]:
                impact[category][layer] = {'count': 0, 'missed_alpha': 0.0, 'winners': 0}
            
            impact[category][layer]['count'] += 1
            if res['potential_pnl'] > 1.0: # Count as winner if > 1% move
                impact[category][layer]['winners'] += 1
                impact[category][layer]['missed_alpha'] += res['potential_pnl']

    # --- REPORT ---
    print("\n📊 EVENT PERFORMANCE AUDIT (Next 4 Hours)")
    print("-" * 120)
    print(f"{'Timestamp':<20} | {'Type':<10} | {'Layer/Filter':<20} | {'Symbol':<12} | {'Side':<5} | {'Max ROI':<10} | {'Max Draw'}")
    print("-" * 120)

    for r in results[:20]: # Show top 20
        e_type = 'STRATEGY' if 'STRATEGY' in r['reason'] else 'RISK'
        print(f"{r['timestamp']:<20} | {e_type:<10} | {r['layer']:<20} | {r['symbol']:<12} | {r['side'].upper():<5} | {format_pnl(r['potential_pnl']):<10} | {format_pnl(r['max_drawdown'])}")

    for category in ['STRATEGY', 'RISK']:
        print(f"\n🛡️ {category} FRICTION ANALYSIS")
        print("-" * 80)
        print(f"{'Layer/Filter':<30} | {'Events':<10} | {'Winners':<8} | {'Avg Missed ROI'}")
        print("-" * 80)

        cat_stats = impact[category]
        if not cat_stats:
            print(f"   No {category.lower()} events found.")
            continue

        for layer, stats in sorted(cat_stats.items(), key=lambda x: x[1]['missed_alpha'], reverse=True):
            avg_alpha = (stats['missed_alpha'] / stats['winners']) if stats['winners'] > 0 else 0
            print(f"{layer:<30} | {stats['count']:<10} | {stats['winners']:<8} | {avg_alpha:.2f}%")

    # --- ADVISORY ---
    print("\n💡 STRATEGIC ADVISORY:")
    
    # Check Strategy friction
    strat_bottleneck = max(impact['STRATEGY'].items(), key=lambda x: x[1]['missed_alpha'], default=(None, None))[0]
    if strat_bottleneck:
        print(f"⚠️  {strat_bottleneck} is causing the most 'Strategy Silence'. Consider loosening ADX/ATR thresholds.")
    
    # Check Risk friction
    risk_bottleneck = max(impact['RISK'].items(), key=lambda x: x[1]['missed_alpha'], default=(None, None))[0]
    if risk_bottleneck:
        print(f"⚠️  {risk_bottleneck} is your primary 'Risk Veto' bottleneck. Review if Max Positions/Correlation is too tight.")

    if not strat_bottleneck and not risk_bottleneck:
        print("✅ Bot is perfectly tuned. No significant missed alpha found.")

    print("\n" + "=" * 70)
    print("📜 AUDIT COMPLETE")
    print("=" * 70)

if __name__ == "__main__":
    main()
