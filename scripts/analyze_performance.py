import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict
from pathlib import Path

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.config import Config
    from database.sqlite_manager import SQLiteManager
except ImportError as e:
    print(f"❌ Error: Could not import bot modules: {e}")
    sys.exit(1)

def format_currency(amount: float) -> str:
    color = "\033[92m" if amount >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}${amount:+.2f}{reset}"

def get_day_key(dt_obj):
    if dt_obj is None: return "Unknown"
    if isinstance(dt_obj, str):
        try:
            # Handle ISO format strings or YYYY-MM-DD
            if 'T' in dt_obj:
                dt_obj = datetime.fromisoformat(dt_obj.split('+')[0].split('.')[0])
            else:
                dt_obj = datetime.strptime(dt_obj, "%Y-%m-%d")
        except:
            return dt_obj[:10] if len(dt_obj) >= 10 else "Unknown"
    return dt_obj.strftime("%Y-%m-%d")

def fetch_trades_from_sqlite(config):
    """Fetch all trades from main SQLite DB (The Sole Source)"""
    trades = []
    try:
        sqlite_mgr = SQLiteManager(config)
        import sqlite3
        conn = sqlite3.connect(sqlite_mgr.main_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # Fetch all trades to allow for future filtering/processing
        cursor.execute("SELECT * FROM trades")
        for row in cursor.fetchall():
            trades.append(dict(row))
            
        conn.close()
    except Exception as e:
        print(f"⚠️ SQLite Trade Fetch Error: {e}")
    return trades

def fetch_rejections_from_sqlite(config):
    """Fetch all rejections from activity_log DB (The Sole Source)"""
    rejections = []
    try:
        sqlite_mgr = SQLiteManager(config)
        import sqlite3
        conn = sqlite3.connect(sqlite_mgr.log_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        # In unified schema, rejections are in activity_log where type is 'position_rejections'
        cursor.execute("SELECT * FROM activity_log WHERE type = 'position_rejections'")
        for row in cursor.fetchall():
            try:
                import json
                meta = json.loads(row['metadata']) if row['metadata'] else {}
                rejections.append({
                    'timestamp': row['timestamp'],
                    'layer_name': meta.get('layer', 'Unknown'),
                    'symbol': row['symbol']
                })
            except: pass
            
        conn.close()
    except Exception as e:
        print(f"⚠️ SQLite Rejection Fetch Error: {e}")
    return rejections

def main():
    START_DATE = "2026-02-20"
    
    print("=" * 60)
    print("      🚀 APEX HUNTER V14 - PURE SQLITE PERFORMANCE 🚀")
    print(f"      (Analyzing data from {START_DATE} onwards)")
    print("=" * 60)

    from dotenv import load_dotenv
    load_dotenv()
    
    config = Config()
    
    print("\n📦 Fetching data from Unified SQLite Storage...")
    
    all_trades = fetch_trades_from_sqlite(config)
    risk_rejections = fetch_rejections_from_sqlite(config)
    
    print(f"   ✅ Trades: {len(all_trades)}")
    print(f"   ✅ Rejections: {len(risk_rejections)}")
    
    # --- PROCESSING ---
    daily_report = defaultdict(lambda: {
        'f_count': 0, 'f_pnl': 0.0, 's_count': 0, 's_pnl': 0.0
    })

    for t in all_trades:
        # Status filter - only for closed trades calculate P&L
        if t.get('status') != 'CLOSED': continue
        day = get_day_key(t.get('entry_time') or t.get('exit_time'))
        if day < START_DATE: continue
        
        pnl = t.get('pnl_amount') or 0.0
        m_type = t.get('market_type', 'futures').lower()
        
        if m_type == 'spot':
            daily_report[day]['s_count'] += 1
            daily_report[day]['s_pnl'] += pnl
        else:
            daily_report[day]['f_count'] += 1
            daily_report[day]['f_pnl'] += pnl

    # --- DISPLAY ---
    print("\n📅 DAILY PERFORMANCE BREAKDOWN")
    print("-" * 80)
    print(f"{'Date':<12} | {'F.Trades':<8} | {'F.P&L':<12} | {'S.Trades':<8} | {'S.P&L':<12} | {'Total P&L'}")
    print("-" * 80)

    total_f_pnl = 0.0
    total_s_pnl = 0.0
    total_f_trades = 0
    total_s_trades = 0

    for day in sorted(daily_report.keys(), reverse=True):
        if day == "Unknown": continue
        d = daily_report[day]
        total = d['f_pnl'] + d['s_pnl']
        print(f"{day:<12} | {d['f_count']:<8} | {format_currency(d['f_pnl']):<12} | {d['s_count']:<8} | {format_currency(d['s_pnl']):<12} | {format_currency(total)}")
        
        total_f_pnl += d['f_pnl']
        total_s_pnl += d['s_pnl']
        total_f_trades += d['f_count']
        total_s_trades += d['s_count']

    # --- 2. RISK FRICTION ANALYSIS ---
    print("\n🛡️ RISK REJECTION AUDIT")
    print("-" * 60)
    layer_stats = defaultdict(int)
    for r in risk_rejections:
        layer_stats[r.get('layer_name', 'Unknown')] += 1

    if not layer_stats:
        print("✅ No trade rejections found.")
    else:
        for layer, count in sorted(layer_stats.items(), key=lambda x: x[1], reverse=True):
            print(f" - {layer:25}: {count} rejections")

    # --- 3. OVERALL SUMMARY ---
    print("\n💰 PERFORMANCE SUMMARY")
    print("-" * 60)
    print(f"Total Futures P&L:      {format_currency(total_f_pnl)} ({total_f_trades} trades)")
    print(f"Total Spot P&L:         {format_currency(total_s_pnl)} ({total_s_trades} trades)")
    print(f"Combined Net P&L:       {format_currency(total_f_pnl + total_s_pnl)}")
    
    print("\n" + "=" * 60)
    print("📜 ANALYSIS COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()

