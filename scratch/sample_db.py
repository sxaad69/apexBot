import sqlite3
import json

def get_samples(db_path, query_map):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    results = {}
    for name, query in query_map.items():
        try:
            cursor = conn.cursor()
            cursor.execute(query)
            rows = cursor.fetchall()
            results[name] = [dict(r) for r in rows]
        except Exception as e:
            results[name] = f"Error: {e}"
    conn.close()
    return results

# Activity Log DB
activity_queries = {
    "General Logs": "SELECT * FROM activity_log WHERE type != 'sweep_summary' ORDER BY id DESC LIMIT 2",
    "Sweep Summaries": "SELECT * FROM activity_log WHERE type = 'sweep_summary' ORDER BY id DESC LIMIT 2",
    "Market Analysis": "SELECT * FROM market_analysis ORDER BY id DESC LIMIT 2",
    "Strategy Signals": "SELECT * FROM strategy_signals ORDER BY id DESC LIMIT 2",
    "Rejections": "SELECT * FROM rejections ORDER BY id DESC LIMIT 2"
}

# Main DB Backup
main_queries = {
    "Metrics": "SELECT * FROM metrics ORDER BY id DESC LIMIT 2",
    "Portfolio Ratchets": "SELECT * FROM portfolio_ratchets ORDER BY id DESC LIMIT 2"
}

print("=== SAMPLES FROM activity_log.db ===")
res_act = get_samples('data/activity_log.db', activity_queries)
print(json.dumps(res_act, indent=2))

print("\n=== SAMPLES FROM apex_hunter.db.bak_20260423_200156 ===")
res_main = get_samples('data/apex_hunter.db.bak_20260423_200156', main_queries)
print(json.dumps(res_main, indent=2))
