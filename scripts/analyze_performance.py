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
    from database.mongo_manager import MongoManager
    from database.json_manager import JSONManager
except ImportError as e:
    print(f"❌ Error: Could not import bot modules: {e}")
    print("💡 Tip: Ensure you have installed requirements: pip install -r requirements.txt")
    sys.exit(1)

def format_currency(amount: float) -> str:
    color = "\033[92m" if amount >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}${amount:+.2f}{reset}"

def get_day_key(dt_obj):
    if isinstance(dt_obj, str):
        try:
            # Handle ISO format strings
            dt_obj = datetime.fromisoformat(dt_obj.replace('Z', '+00:00'))
        except:
            return "Unknown"
    return dt_obj.strftime("%Y-%m-%d")

def main():
    START_DATE = "2026-02-20"
    
    print("=" * 60)
    print("      🚀 APEX HUNTER V14 - DEEP DIVE PERFORMANCE 🚀")
    print(f"      (Analyzing data from {START_DATE} onwards)")
    print("=" * 60)

    from dotenv import load_dotenv
    load_dotenv()
    
    config = Config.__new__(Config)
    config._load_configuration()
    
    # Check for forced JSON mode or MongoDB connection
    force_json = os.getenv('FORCE_JSON_ANALYSIS', 'false').lower() == 'true'
    mongo = None
    db_manager = None
    
    if not force_json:
        try:
            mongo = MongoManager(config)
            if mongo.is_connected:
                db_manager = mongo
                print("✅ Connected to MongoDB Atlas")
        except Exception:
            pass

    if db_manager is None:
        # Check if we have an export folder 'db' with data
        db_path = Path("db")
        if db_path.exists() and any(db_path.glob("*.json")):
            print("📦 Using Exported MongoDB Data (db/ folder)")
            db_manager = JSONManager(config, data_dir="db")
        else:
            print("📁 Using Local JSON Fallback (data/ folder)")
            db_manager = JSONManager(config)

    # Fetch all data
    raw_futures = db_manager.find_documents('futures_trades', limit=5000)
    raw_spot_signals = db_manager.find_documents('spot_signals', query={'executed': True}, limit=2000)
    
    # Categorize and Filter
    futures_trades = []
    spot_trades = []
    
    # Process futures
    for t in raw_futures:
        day = get_day_key(t['timestamp'])
        if day < START_DATE: continue
        
        # Check if it's actually spot leaked into futures
        strat = t.get('strategy', 'Unknown')
        if t.get('market_type') == 'spot' or strat == 'SpotLogger':
            spot_trades.append(t)
        else:
            futures_trades.append(t)
            
    # Process spot
    for t in raw_spot_signals:
        day = get_day_key(t['timestamp'])
        if day < START_DATE: continue
        spot_trades.append(t)

    # --- 1. DAILY COMBINED PERFORMANCE ---
    print("\n📅 DAILY PERFORMANCE BREAKDOWN (Futures & Spot)")
    print("-" * 80)
    print(f"{'Date':<12} | {'F.Trades':<8} | {'F.P&L':<12} | {'S.Trades':<8} | {'S.P&L':<12} | {'Total P&L'}")
    print("-" * 80)

    daily_report = defaultdict(lambda: {
        'f_count': 0, 'f_pnl': 0.0, 's_count': 0, 's_pnl': 0.0
    })

    # Aggregate Futures
    for t in futures_trades:
        if 'pnl_amount' not in t: continue
        day = get_day_key(t['timestamp'])
        daily_report[day]['f_count'] += 1
        daily_report[day]['f_pnl'] += t['pnl_amount']

    # Aggregate Spot (Exits only)
    for t in spot_trades:
        # Check if it's an exit record (has pnl or pnl_amount)
        pnl = t.get('pnl_amount') or t.get('pnl') or t.get('pnl_usdt')
        if pnl is None and t.get('type') != 'exit':
            continue
            
        pnl = pnl or 0.0
        day = get_day_key(t['timestamp'])
        daily_report[day]['s_count'] += 1
        daily_report[day]['s_pnl'] += pnl

    total_f_pnl = 0.0
    total_s_pnl = 0.0
    total_f_trades = 0
    total_s_trades = 0

    for day in sorted(daily_report.keys(), reverse=True):
        d = daily_report[day]
        total = d['f_pnl'] + d['s_pnl']
        print(f"{day:<12} | {d['f_count']:<8} | {format_currency(d['f_pnl']):<12} | {d['s_count']:<8} | {format_currency(d['s_pnl']):<12} | {format_currency(total)}")
        
        total_f_pnl += d['f_pnl']
        total_s_pnl += d['s_pnl']
        total_f_trades += d['f_count']
        total_s_trades += d['s_count']

    # --- 2. RISK FRICTION ANALYSIS ---
    print("\n🛡️ RISK REJECTION AUDIT (Bottleneck Detection)")
    print("-" * 60)
    rejections = db_manager.find_documents('risk_rejections', limit=1000)
    layer_stats = defaultdict(int)
    for r in rejections:
        day = get_day_key(r['timestamp'])
        if day < START_DATE: continue
        layer_stats[r.get('layer_name', 'Unknown')] += 1

    if not layer_stats:
        print("✅ No trade rejections found. Risk layers are smooth.")
    else:
        for layer, count in sorted(layer_stats.items(), key=lambda x: x[1], reverse=True):
            print(f" - {layer:25}: {count} rejections")

    # --- 3. OVERALL SUMMARY ---
    print("\n💰 PERFORMANCE SUMMARY (Feb 20th - Present)")
    print("-" * 60)
    print(f"Total Futures P&L:      {format_currency(total_f_pnl)} ({total_f_trades} trades)")
    print(f"Total Spot P&L:         {format_currency(total_s_pnl)} ({total_s_trades} trades)")
    print(f"Combined Net P&L:       {format_currency(total_f_pnl + total_s_pnl)}")
    
    # Fee Estimation
    total_volume = sum(t.get('position_size', 0) * t.get('leverage', 1) for t in futures_trades)
    est_fees = total_volume * 0.0004 * 2
    print(f"Est. Exchange Fees:     {format_currency(-est_fees)}")
    print(f"Final Adjusted P&L:     {format_currency(total_f_pnl + total_s_pnl - est_fees)}")

    print("\n" + "=" * 60)
    print("📜 ANALYSIS COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()

