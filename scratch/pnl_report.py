import sqlite3
import json
from datetime import datetime

db_path = 'data/apex_hunter.db'

def get_report():
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 1. Closed Trades Summary
    cursor.execute("""
        SELECT 
            COUNT(*) as total_closed,
            SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_amount) as total_realized_pnl,
            AVG(pnl_amount) as avg_pnl
        FROM trades 
        WHERE status = 'CLOSED'
    """)
    closed_summary = cursor.fetchone()

    # 2. Strategy Breakdown
    cursor.execute("""
        SELECT 
            strategy,
            COUNT(*) as trades,
            SUM(CASE WHEN pnl_amount > 0 THEN 1 ELSE 0 END) as wins,
            SUM(pnl_amount) as realized_pnl,
            AVG(pnl_amount) as avg_pnl
        FROM trades 
        WHERE status = 'CLOSED'
        GROUP BY strategy
    """)
    strategies = cursor.fetchall()

    # 3. Unrealized PnL (from active_positions snapshot)
    cursor.execute("SELECT SUM(pnl_percent) as total_pnl_pct, COUNT(*) as count FROM active_positions")
    unrealized_data = cursor.fetchone()
    
    # We don't have pnl_amount in active_positions directly, but we can estimate if we had size/price
    # Let's see if we can get an estimate from trades table for OPEN status
    cursor.execute("SELECT SUM(pnl_amount) as total_pnl FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
    trades_unrealized = cursor.fetchone()

    print("\n" + "="*50)
    print("      APEX BOT PERFORMANCE REPORT")
    print("="*50)
    print(f"Generated at: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("-"*50)

    total_closed = closed_summary['total_closed'] or 0
    total_realized = closed_summary['total_realized_pnl'] or 0
    wins = closed_summary['wins'] or 0
    win_rate = (wins / total_closed * 100) if total_closed > 0 else 0

    print(f"OVERALL STATUS: {'🟢 IN PROFIT' if (total_realized + (trades_unrealized['total_pnl'] or 0)) > 0 else '🔴 IN LOSS'}")
    print(f"Total Realized P&L: ${total_realized:.2f}")
    print(f"Total Closed Trades: {total_closed}")
    print(f"Overall Win Rate: {win_rate:.2f}%")
    print(f"Average P&L per Trade: ${closed_summary['avg_pnl'] or 0:.2f}")

    print("\n--- Strategy Performance ---")
    print(f"{'Strategy':<25} | {'Trades':<6} | {'Win%':<7} | {'PnL':<10}")
    print("-" * 55)
    for s in strategies:
        s_trades = s['trades'] or 0
        s_wins = s['wins'] or 0
        s_pnl = s['realized_pnl'] or 0
        s_win_rate = (s_wins / s_trades * 100) if s_trades > 0 else 0
        print(f"{s['strategy'][:25]:<25} | {s_trades:<6} | {s_win_rate:>5.1f}% | ${s_pnl:>8.2f}")

    print("\n--- Current Open Positions ---")
    print(f"Active Positions (Snapshot): {unrealized_data['count']}")
    # print(f"Snapshot Unrealized ROE Sum: {unrealized_data['total_pnl_pct'] or 0:.2f}%")
    print(f"Last Recorded Unrealized P&L: ${trades_unrealized['total_pnl'] or 0:.2f}")

    total_profit = total_realized + (trades_unrealized['total_pnl'] or 0)
    print("-" * 50)
    print(f"TOTAL ESTIMATED PROFIT: ${total_profit:.2f}")
    print("=" * 50 + "\n")

    conn.close()

if __name__ == "__main__":
    get_report()
