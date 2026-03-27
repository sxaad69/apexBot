import sqlite3
import os
import pandas as pd

def get_trades(db_path):
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query("SELECT strategy, size, leverage, pnl_percent, pnl_amount, status, reason FROM trades WHERE strategy LIKE '%A5%' AND status IN ('CLOSED', 'closed')", conn)
        conn.close()
        return df
    except Exception as e:
        return pd.DataFrame()

db_files = [
    '/home/ubuntu/apexBot/data/apex_hunter.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter_v14.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter_V1.db'
]

all_dfs = [get_trades(db) for db in db_files]
df = pd.concat(all_dfs, ignore_index=True)

if df.empty:
    print("No A5 trades found.")
    exit()

def get_total_pnl(df, sl_cap):
    def apply_cap(row):
        pnl_pct = row['pnl_percent']
        if sl_cap is not None and pnl_pct < -abs(sl_cap):
            return row['size'] * (-abs(sl_cap) / 100.0)
        return row['pnl_amount']
    return df.apply(apply_cap, axis=1).sum()

print("\n--- A5 Strategy (Microstructure): STOP LOSS DEPTH AUDIT ---")
print(f"Total Trades: {len(df)}")
print("-" * 50)
print(f"1. ACTUAL TOTAL (NO CAP):   ${df['pnl_amount'].sum():,.2f}")
print(f"2. 15% SL ROE CAP:          ${get_total_pnl(df, 15.0):,.2f}")
print(f"3. 10% SL ROE CAP:          ${get_total_pnl(df, 10.0):,.2f}")
print(f"4. 5% SL ROE CAP:           ${get_total_pnl(df, 5.0):,.2f}")
print("-" * 50)

# Identify trade counts for caps
print(f"Trades saved by 15% Cap: {(df['pnl_percent'] < -15.0).sum()}")
print(f"Trades saved by 10% Cap: {(df['pnl_percent'] < -10.0).sum()}")
print(f"Trades saved by 5% Cap:  {(df['pnl_percent'] < -5.0).sum()}")
