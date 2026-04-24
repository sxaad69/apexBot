import sqlite3
import json

def get_counts(db_path, query_map):
    conn = sqlite3.connect(db_path)
    results = {}
    for name, query in query_map.items():
        try:
            cursor = conn.cursor()
            cursor.execute(query)
            count = cursor.fetchone()[0]
            results[name] = count
        except Exception as e:
            results[name] = f"Error: {e}"
    conn.close()
    return results

activity_queries = {
    "1. General Logs": "SELECT COUNT(*) FROM activity_log WHERE type != 'sweep_summary'",
    "2. Sweep Summaries": "SELECT COUNT(*) FROM activity_log WHERE type = 'sweep_summary'",
    "3. Market Analysis": "SELECT COUNT(*) FROM market_analysis",
    "4. Strategy Signals": "SELECT COUNT(*) FROM strategy_signals",
    "5. Rejections": "SELECT COUNT(*) FROM rejections"
}

main_queries = {
    "6. Performance Metrics": "SELECT COUNT(*) FROM metrics",
    "7. Portfolio Ratchets": "SELECT COUNT(*) FROM portfolio_ratchets"
}

print("=== RECORD COUNTS FROM DB BACKUPS ===")
res_act = get_counts('data/activity_log.db.bak_20260423_203223', activity_queries)
res_main = get_counts('data/apex_hunter.db.bak_20260423_200156', main_queries)

all_results = {**res_act, **res_main}
for k, v in sorted(all_results.items()):
    print(f"{k}: {v:,}")
