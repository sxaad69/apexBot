import sqlite3
import os

DB_PATH = 'data/apex_hunter.db'

def check_trailing():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute('''
            SELECT symbol, side, entry_price, highest_price, trailing_stop_price, exit_price, pnl_percent, status, reason
            FROM trades 
            WHERE trailing_stop_price IS NOT NULL OR trailing_stop_active = 1
            ORDER BY rowid DESC LIMIT 20
        ''')
        trades = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        conn.close()
        return

    if not trades:
        print("No trades found where trailing stop was activated.")
        return

    print(f"{'Symbol':<15} | {'Side':<5} | {'Entry':<12} | {'Highest':<12} | {'Trail Stop':<12} | {'Exit':<12} | {'PNL%':<8} | {'Reason'}")
    print("-" * 105)

    for t in trades:
        entry = t['entry_price'] or 0
        highest = t['highest_price'] or 0
        trail = t['trailing_stop_price'] or 0
        exit_p = t['exit_price'] or 0
        pnl = t['pnl_percent'] or 0
        reason = str(t['reason'])[:15] if t['reason'] else t['status']
        
        print(f"{t['symbol']:<15} | {t['side'].upper():<5} | {entry:<12.6f} | {highest:<12.6f} | {trail:<12.6f} | {exit_p:<12.6f} | {pnl:>7.2f}% | {reason}")

    conn.close()

if __name__ == "__main__":
    check_trailing()
