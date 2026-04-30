import sqlite3
import os
import json
from datetime import datetime

DB_PATH = 'data/apex_hunter.db'

def calculate_sl_deviations():
    if not os.path.exists(DB_PATH):
        print(f"Error: Database {DB_PATH} not found.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT trade_id, symbol, side, entry_price, stop_loss, leverage, metadata FROM trades WHERE status='CLOSED' ORDER BY exit_time DESC LIMIT 50")
        trades = cursor.fetchall()
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        conn.close()
        return

    print(f"{'Symbol':<15} | {'Side':<5} | {'Entry Price':<12} | {'Stop Loss':<12} | {'Lev':<3} | {'Actual SL ROE':<15} | {'Max Target ROE':<15} | {'Deviation (ROE)':<15}")
    print("-" * 105)

    for trade in trades:
        symbol = trade['symbol']
        side = trade['side']
        entry_price = trade['entry_price']
        stop_loss = trade['stop_loss']
        leverage = trade['leverage'] or 1
        
        # Parse metadata to see if strategy provided target_sl_roe
        meta = {}
        try:
            if trade['metadata']:
                meta = json.loads(trade['metadata'])
        except:
            pass

        if not entry_price or not stop_loss:
            continue
            
        # Target ROE (Assume 10% as per current config, though past trades might have used 5%)
        # Let's check metadata if it logged target SL ROE
        target_sl_roe = meta.get('stop_loss_roe', 10.0)

        # Calculate actual ROE percentage of the set stop loss vs actual entry price
        if side == 'buy':
            price_drop_pct = (entry_price - stop_loss) / entry_price
        else:
            price_drop_pct = (stop_loss - entry_price) / entry_price
            
        actual_sl_roe = price_drop_pct * leverage * 100
        
        deviation = actual_sl_roe - target_sl_roe

        print(f"{symbol:<15} | {side.upper():<5} | {entry_price:<12.6f} | {stop_loss:<12.6f} | {leverage:<3.0f} | {actual_sl_roe:>14.2f}% | {target_sl_roe:>14.2f}% | {deviation:>14.2f}%")

    conn.close()

if __name__ == "__main__":
    calculate_sl_deviations()
