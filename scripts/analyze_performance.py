import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict

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
    print("=" * 60)
    print("      🚀 APEX HUNTER V14 - DEEP DIVE PERFORMANCE 🚀")
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
        print("📁 Using Local JSON Fallback (Connect to AWS for latest data)")
        db_manager = JSONManager(config)

    # --- 1. DAILY FUTURES PERFORMANCE ---
    print("\n📅 DAY-BY-DAY FUTURES PERFORMANCE")
    print("-" * 60)
    print(f"{'Date':<12} | {'Trades':<8} | {'Win%':<8} | {'Net P&L':<12} | {'Top Strategy'}")
    print("-" * 60)

    # Fetch trades
    raw_trades = db_manager.find_documents('futures_trades', limit=2000)
    
    # Categorize trades
    futures_trades = []
    spot_leaks = []
    
    for t in raw_trades:
        # Normalize strategy name for filtering
        strat = t.get('strategy', 'Unknown')
        if t.get('market_type') == 'spot' or strat == 'SpotLogger':
            spot_leaks.append(t)
        else:
            futures_trades.append(t)

    daily_stats = defaultdict(lambda: {
        'count': 0, 'wins': 0, 'pnl': 0.0, 'strats': defaultdict(float)
    })

    total_futures_pnl = 0.0
    total_futures_count = 0
    total_futures_wins = 0

    for t in futures_trades:
        if 'pnl_amount' not in t: continue
        day = get_day_key(t['timestamp'])
        stats = daily_stats[day]
        stats['count'] += 1
        stats['pnl'] += t['pnl_amount']
        if t['pnl_amount'] > 0: stats['wins'] += 1
        
        strat = t.get('strategy', 'Unknown')
        stats['strats'][strat] += t['pnl_amount']
        
        total_futures_pnl += t['pnl_amount']
        total_futures_count += 1
        if t['pnl_amount'] > 0: total_futures_wins += 1

    for day in sorted(daily_stats.keys(), reverse=True):
        s = daily_stats[day]
        wr = (s['wins'] / s['count'] * 100) if s['count'] > 0 else 0
        top_strat = max(s['strats'].items(), key=lambda x: x[1])[0] if s['strats'] else "N/A"
        print(f"{day:<12} | {s['count']:<8} | {wr:>5.1f}% | {format_currency(s['pnl']):<12} | {top_strat}")

    # --- 2. SPOT PERFORMANCE ---
    print("\n📦 SPOT TRADING AUDIT")
    print("-" * 60)
    
    # Check official spot collection
    spot_signals = db_manager.find_documents('spot_signals', query={'executed': True}, limit=1000)
    
    # Combine signals + leaks
    all_spot = spot_signals + spot_leaks
    
    total_spot_pnl = sum(s.get('pnl_amount', 0) for s in all_spot if s.get('pnl_amount') is not None)
    total_spot_pnl += sum(s.get('pnl', 0) for s in all_spot if s.get('pnl') is not None) # Handle both keys
    
    spot_wins = sum(1 for s in all_spot if (s.get('pnl_amount', 0) > 0 or s.get('pnl', 0) > 0))
    
    print(f"Executed Spot Trades: {len(all_spot)}")
    print(f"Total Spot Net P&L:   {format_currency(total_spot_pnl)}")
    if all_spot:
        spot_wr = (spot_wins/len(all_spot)*100)
        print(f"Spot Win Rate:        {spot_wr:.1f}%")

    # --- 3. RISK FRICTION ANALYSIS ---
    print("\n🛡️ RISK REJECTION AUDIT (Bottleneck Detection)")
    print("-" * 60)
    rejections = db_manager.find_documents('risk_rejections', limit=1000)
    layer_stats = defaultdict(int)
    for r in rejections:
        layer_stats[r.get('layer_name', 'Unknown')] += 1

    if not rejections:
        print("✅ No trade rejections found. Risk layers are smooth.")
    else:
        for layer, count in sorted(layer_stats.items(), key=lambda x: x[1], reverse=True):
            print(f" - {layer:25}: {count} rejections")

    # --- 4. FEE IMPACT AUDIT ---
    # Using futures_trades for analysis
    total_volume = sum(t.get('position_size', 0) * t.get('leverage', 1) for t in futures_trades)
    est_fees = total_volume * 0.0004 * 2 # Entry + Exit
    print(f"\n💸 Estimated Exchange Fees Paid: {format_currency(-est_fees)}")
    
    net_efficiency = (total_futures_pnl / (est_fees + 0.0001)) * 100
    print(f"📊 Net Efficiency Score:        {net_efficiency:.1f}%")

    # --- 5. OVERALL SUMMARY ---
    print("\n💰 TOTAL MANAGED PERFORMANCE SUMMARY")
    print("-" * 60)
    grand_total_pnl = total_futures_pnl + total_spot_pnl
    print(f"Combined Net P&L (All Markets): {format_currency(grand_total_pnl)}")
    
    total_trades = total_futures_count + len(all_spot)
    print(f"Total Executed Trades:         {total_trades}")
    
    avg_win_rate = ((total_futures_wins + spot_wins) / (total_trades + 0.0001)) * 100
    print(f"Global Win Rate:               {avg_win_rate:.1f}%")

    print("\n" + "=" * 60)
    print("📜 ANALYSIS COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
