import sqlite3
import os
import pandas as pd

def get_trades(db_path):
    if not os.path.exists(db_path):
        return pd.DataFrame()
    try:
        conn = sqlite3.connect(db_path)
        # Check if trades table exists
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades';")
        if not cursor.fetchone():
            conn.close()
            return pd.DataFrame()
            
        # Get column names to handle variations
        cursor.execute("PRAGMA table_info(trades);")
        columns = [col[1] for col in cursor.fetchall()]
        
        query_cols = ["strategy", "status", "reason"]
        if "pnl_amount" in columns:
            query_cols.append("pnl_amount")
        else:
            # Fallback if pnl_amount is missing (shouldn't happen in our bot dbs)
            query_cols.append("0 as pnl_amount")
            
        df = pd.read_sql_query(f"SELECT {', '.join(query_cols)} FROM trades", conn)
        conn.close()
        return df
    except Exception as e:
        print(f"Error reading {db_path}: {e}")
        return pd.DataFrame()

db_files = [
    '/home/ubuntu/apexBot/data/apex_hunter.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter_v14.db',
    '/home/ubuntu/apexBot/backup_dbs/apex_hunter_V1.db'
]

all_dfs = []
for db in db_files:
    df = get_trades(db)
    if not df.empty:
        all_dfs.append(df)

if not all_dfs:
    print("No trades found in any database.")
else:
    full_df = pd.concat(all_dfs, ignore_index=True)
    
    # Filter for CLOSED trades
    closed_df = full_df[full_df['status'].isin(['CLOSED', 'closed'])]
    
    if closed_df.empty:
        print("No closed trades found.")
    else:
        # Aggregate by strategy
        summary = closed_df.groupby('strategy').agg(
            total_pnl=('pnl_amount', 'sum'),
            trade_count=('pnl_amount', 'count'),
            avg_pnl=('pnl_amount', 'mean'),
            sl_hits=('reason', lambda x: (x.str.lower() == 'stop_loss').sum()),
            tp_hits=('reason', lambda x: (x.str.lower() == 'take_profit').sum())
        ).sort_values(by='total_pnl')

        print("\n--- STRATEGY PERFORMANCE SUMMARY ---")
        print(summary.to_string())

        bleeding = summary.head(1)
        if not bleeding.empty and bleeding['total_pnl'].iloc[0] < 0:
            print(f"\n❌ THE MOST BLEEDING STRATEGY IS: {bleeding.index[0]}")
            print(f"Total Loss: ${bleeding['total_pnl'].iloc[0]:.2f} across {bleeding['trade_count'].iloc[0]} trades.")
        else:
            print("\n✅ All strategies are currently profitable or no losses recorded.")
