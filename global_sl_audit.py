import sqlite3
import os
import pandas as pd

def get_all_trades():
    db_files = [
        '/home/ubuntu/apexBot/data/apex_hunter.db',
        '/home/ubuntu/apexBot/backup_dbs/apex_hunter.db',
        '/home/ubuntu/apexBot/backup_dbs/apex_hunter_v14.db',
        '/home/ubuntu/apexBot/backup_dbs/apex_hunter_V1.db'
    ]
    
    all_dfs = []
    for db in db_files:
        if os.path.exists(db):
            try:
                conn = sqlite3.connect(db)
                df = pd.read_sql_query("SELECT strategy, size, leverage, pnl_percent, pnl_amount, status FROM trades WHERE status IN ('CLOSED', 'closed')", conn)
                conn.close()
                all_dfs.append(df)
            except Exception:
                continue
    
    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)

df = get_all_trades()

if df.empty:
    print("No trades found.")
    exit()

def get_capped_pnl(df, sl_cap):
    def apply_cap(row):
        pnl_pct = row['pnl_percent']
        if sl_cap is not None and pnl_pct < -abs(sl_cap):
            return row['size'] * (-abs(sl_cap) / 100.0)
        return row['pnl_amount']
    return df.apply(apply_cap, axis=1).sum()

strategies = df['strategy'].unique()

print("\n--- GLOBAL STOP LOSS ROE AUDIT (ALL STRATEGIES) ---")
print(f"{'STRATEGY':<30} | {'ACTUAL':<10} | {'10% SL':<10} | {'5% SL':<10} | {'DELTA'}")
print("-" * 85)

summary = []

for strat in strategies:
    strat_df = df[df['strategy'] == strat]
    actual = strat_df['pnl_amount'].sum()
    sl_10 = get_capped_pnl(strat_df, 10.0)
    sl_5 = get_capped_pnl(strat_df, 5.0)
    delta = sl_5 - actual
    
    print(f"{strat[:30]:<30} | ${actual:>8.2f} | ${sl_10:>8.2f} | ${sl_5:>8.2f} | +${delta:>8.2f}")
    
    summary.append({
        'strategy': strat,
        'actual': actual,
        'sl_10': sl_10,
        'sl_5': sl_5,
        'delta': delta
    })

print("-" * 85)
total_actual = sum(s['actual'] for s in summary)
total_sl5 = sum(s['sl_5'] for s in summary)
print(f"{'TOTAL PORTFOLIO':<30} | ${total_actual:>8.2f} | ${sum(s['sl_10'] for s in summary):>8.2f} | ${total_sl5:>8.2f} | +${total_sl5 - total_actual:>8.2f}")
