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
                df = pd.read_sql_query("SELECT strategy, side, size, leverage, entry_price, highest_price, lowest_price, pnl_amount, status, reason FROM trades WHERE status IN ('CLOSED', 'closed') AND pnl_amount > 0", conn)
                conn.close()
                all_dfs.append(df)
            except Exception:
                continue
    
    if not all_dfs:
        return pd.DataFrame()
    return pd.concat(all_dfs, ignore_index=True)

df = get_all_trades()

if df.empty:
    print("No winners found to audit.")
    exit()

# Audit Criteria
sl_cap_roe = 5.0 # 5% ROE

def would_be_stopped(row):
    leverage = row['leverage'] or 1.0 # Default to 1
    entry = row['entry_price']
    highest = row['highest_price'] or entry
    lowest = row['lowest_price'] or entry
    
    # 5% ROE Price move threshold
    threshold_pct = sl_cap_roe / leverage
    
    if row['side'] == 'buy':
        # Long: Stop hits if lowest < entry - threshold
        stop_price = entry * (1 - (threshold_pct / 100.0))
        return lowest <= stop_price
    else:
        # Short: Stop hits if highest > entry + threshold
        stop_price = entry * (1 + (threshold_pct / 100.0))
        return highest >= stop_price

df['stopped_out_early'] = df.apply(would_be_stopped, axis=1)

print("\n--- SAFETY AUDIT: WOULD WE KILL OUR WINNERS WITH 5% SL? ---")
print(f"Total Winning Trades Audited: {len(df)}")
print("-" * 60)

for strat in df['strategy'].unique():
    strat_df = df[df['strategy'] == strat]
    total_wins = len(strat_df)
    killed_wins = strat_df['stopped_out_early'].sum()
    pct_killed = (killed_wins / total_wins) * 100 if total_wins > 0 else 0
    
    print(f"{strat[:30]:<30}: {killed_wins:>2} out of {total_wins:>2} winners would be KILLED ({pct_killed:>4.1f}%)")

print("-" * 60)
total_killed = df['stopped_out_early'].sum()
print(f"TOTAL IMPACT: {total_killed} out of {len(df)} winners would be LOST ({ (total_killed/len(df))*100:.1f}%)")
