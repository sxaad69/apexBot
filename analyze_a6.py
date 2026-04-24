import sqlite3
import json

db_path = 'data/apex_hunter.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Get all closed trades for A6
cursor.execute("SELECT * FROM trades WHERE strategy LIKE 'A6%' AND status = 'CLOSED' ORDER BY exit_time DESC")
trades = cursor.fetchall()

if not trades:
    print("No closed trades found for A6.")
else:
    print(f"=== A6 PERFORMANCE ANALYSIS ({len(trades)} trades) ===")
    
    total_pnl = 0
    wins = 0
    losses = 0
    reasons = {}
    
    for t in trades:
        pnl = t['pnl_amount']
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1
            
        reason = t['reason'] or "Unknown"
        reasons[reason] = reasons.get(reason, 0) + 1
        
    print(f"Net P&L: ${total_pnl:.2f}")
    print(f"Win Rate: {(wins/len(trades)*100):.1f}% ({wins}W / {losses}L)")
    
    print("\n--- Exit Reasons ---")
    for r, count in reasons.items():
        print(f" {r}: {count}")
        
    print("\n--- Last 5 Losses ---")
    cursor.execute("SELECT symbol, side, entry_price, exit_price, pnl_amount, reason FROM trades WHERE strategy LIKE 'A6%' AND status = 'CLOSED' AND pnl_amount < 0 ORDER BY exit_time DESC LIMIT 5")
    last_losses = cursor.fetchall()
    for l in last_losses:
        print(f" {l['symbol']} ({l['side']}): ${l['pnl_amount']:.2f} | Reason: {l['reason']}")

conn.close()
