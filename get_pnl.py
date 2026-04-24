import sqlite3

db_path = 'data/apex_hunter.db'
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# 1. Realized P&L (Closed Trades)
cursor.execute("SELECT SUM(pnl_amount) as total_pnl, SUM(pnl_percent) as total_pnl_pct, COUNT(*) as count FROM trades WHERE status = 'CLOSED'")
realized = cursor.fetchone()

# 2. Unrealized P&L (Open Trades - Estimated)
cursor.execute("SELECT SUM(pnl_amount) as total_pnl, COUNT(*) as count FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
unrealized = cursor.fetchone()

# 3. Strategy breakdown
cursor.execute("SELECT strategy, SUM(pnl_amount) as pnl, COUNT(*) as count FROM trades GROUP BY strategy")
strategies = cursor.fetchall()

print("=== PERFORMANCE SUMMARY ===")
print(f"Closed Trades: {realized['count'] if realized else 0}")
print(f"Realized P&L: ${realized['total_pnl'] if realized and realized['total_pnl'] else 0:.2f}")

print(f"\nOpen Trades: {unrealized['count'] if unrealized else 0}")
print(f"Last Recorded Unrealized P&L: ${unrealized['total_pnl'] if unrealized and unrealized['total_pnl'] else 0:.2f}")

print("\n--- Strategy Breakdown ---")
for s in strategies:
    print(f" {s['strategy']}: ${s['pnl'] if s['pnl'] else 0:.2f} ({s['count']} trades)")

conn.close()
