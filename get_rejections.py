import sqlite3
import json

conn = sqlite3.connect('data/activity_log.db')
cursor = conn.cursor()
cursor.execute("SELECT metadata FROM activity_log WHERE type = 'sweep_summary' ORDER BY timestamp DESC LIMIT 1;")
row = cursor.fetchone()

if row:
    data = json.loads(row[0])
    rejections = data.get('strategy_rejections', {})
    print(f"Sweep Timestamp: {data.get('timestamp')}")
    print(f"Total Symbols Scanned: {data.get('symbols_scanned')}")
    
    for strat, reasons in rejections.items():
        print(f"\n--- {strat} ---")
        total_strat_rejections = 0
        for reason, symbols in reasons.items():
            print(f"  [{reason}]: {len(symbols)} coins (Sample: {', '.join(symbols[:3])})")
            total_strat_rejections += len(symbols)
        print(f"  TOTAL REJECTED: {total_strat_rejections}")
else:
    print("No sweep summary found.")

conn.close()
