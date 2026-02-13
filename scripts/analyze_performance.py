import os
import sys
from datetime import datetime, timedelta
from typing import Dict, List

# Add parent directory to path so we can import internal modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.config import Config
    from database.mongo_manager import MongoManager
except ImportError:
    print("❌ Error: Could not import bot modules. Run this from the root 'apexBot' folder.")
    sys.exit(1)

def format_currency(amount: float) -> str:
    color = "\033[92m" if amount >= 0 else "\033[91m"
    reset = "\033[0m"
    return f"{color}${amount:+.2f}{reset}"

def main():
    print("=" * 60)
    print("  APEX HUNTER V14 - Performance Analysis")
    print("=" * 60)

    # Create config without running validation for diagnostic purposes
    from dotenv import load_dotenv
    load_dotenv()
    
    config = Config.__new__(Config)
    config._load_configuration() 
    # Skip _validate_configuration() to avoid Telegram credential errors during analysis
    mongo = MongoManager(config)

    if not mongo.is_connected:
        print("❌ Could not connect to MongoDB Atlas. Check your .env file.")
        return

    # 1. Summarize Futures Trades
    print("\n📊 FUTURES PERFORMANCE SUMMARY")
    print("-" * 30)
    
    trades = mongo.find_documents('futures_trades', limit=1000)
    if not trades:
        print("ℹ️ No futures trades found in database.")
    else:
        # P&L Calculation
        # Note: In our system, both 'entry' and 'exit' are in the same collection
        # But based on the code, trade_exit inserts a full document with 'pnl_amount'
        completed_trades = [t for t in trades if 'pnl_amount' in t]
        
        total_pnl = sum(t.get('pnl_amount', 0) for t in completed_trades)
        wins = sum(1 for t in completed_trades if t.get('pnl_amount', 0) > 0)
        losses = sum(1 for t in completed_trades if t.get('pnl_amount', 0) <= 0)
        
        print(f"Total Trades Completed: {len(completed_trades)}")
        print(f"Total P&L:             {format_currency(total_pnl)}")
        
        if completed_trades:
            win_rate = (wins / len(completed_trades)) * 100
            print(f"Win Rate:              {win_rate:.1f}% ({wins}W / {losses}L)")
            
            # Best/Worst
            best = max(t.get('pnl_amount', 0) for t in completed_trades)
            worst = min(t.get('pnl_amount', 0) for t in completed_trades)
            print(f"Best Trade:            {format_currency(best)}")
            print(f"Worst Trade:           {format_currency(worst)}")
            
            # Strategy Breakdown
            strategies = {}
            for t in completed_trades:
                strat = t.get('strategy', 'Unknown')
                strategies[strat] = strategies.get(strat, 0) + t.get('pnl_amount', 0)
            
            print("\n📈 Strategy Performance:")
            for strat, pnl in sorted(strategies.items(), key=lambda x: x[1], reverse=True):
                print(f"   - {strat:20}: {format_currency(pnl)}")

    # 2. Summarize Spot Signals
    print("\n📊 SPOT SIGNALS SUMMARY")
    print("-" * 30)
    spot_signals = mongo.find_documents('spot_signals', limit=1000)
    if not spot_signals:
        print("ℹ️ No spot signals found.")
    else:
        executed = sum(1 for s in spot_signals if s.get('executed', False))
        print(f"Total Signals:         {len(spot_signals)}")
        print(f"Executed Trades:       {executed}")

    print("\n" + "=" * 60)
    print("📜 END OF REPORT")
    print("=" * 60)

if __name__ == "__main__":
    main()
