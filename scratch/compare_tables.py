import sqlite3, json

main_db = '/home/ubuntu/apexBot/data/apex_hunter.db'
log_db  = '/home/ubuntu/apexBot/data/activity_log.db'

# ── TABLE 1: trades (what market_analysis is meant to complement) ─────────────
conn = sqlite3.connect(main_db)
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("SELECT COUNT(*) as n FROM trades")
trade_count = c.fetchone()['n']
c.execute("SELECT COUNT(*) as n FROM trades WHERE status='OPEN'")
open_count = c.fetchone()['n']
c.execute("SELECT COUNT(*) as n FROM trades WHERE status='CLOSED'")
closed_count = c.fetchone()['n']
conn.close()

# ── TABLE 2: rejections ───────────────────────────────────────────────────────
conn2 = sqlite3.connect(log_db)
conn2.row_factory = sqlite3.Row
c2 = conn2.cursor()
c2.execute("SELECT COUNT(*) as n FROM rejections")
rejection_count = c2.fetchone()['n']

c2.execute("SELECT COUNT(DISTINCT symbol) as n FROM rejections")
unique_rejected_symbols = c2.fetchone()['n']

c2.execute("SELECT reason, COUNT(*) as cnt FROM rejections GROUP BY reason ORDER BY cnt DESC LIMIT 10")
rejection_breakdown = c2.fetchall()

# ── TABLE 3: market_analysis ──────────────────────────────────────────────────
c2.execute("SELECT COUNT(*) as n FROM market_analysis")
analysis_count = c2.fetchone()['n']

c2.execute("SELECT COUNT(DISTINCT symbol) as n FROM market_analysis")
unique_analysis_symbols = c2.fetchone()['n']

# What unique columns does market_analysis actually store vs rejections?
c2.execute("SELECT indicators FROM market_analysis ORDER BY id DESC LIMIT 3")
sample_analysis = c2.fetchall()

# What does rejections actually store?
c2.execute("SELECT symbol, strategy, reason, layer FROM rejections ORDER BY id DESC LIMIT 5")
sample_rejections = c2.fetchall()

conn2.close()

# ── REPORT ────────────────────────────────────────────────────────────────────
print("=" * 65)
print("   DATA PROOF: market_analysis vs rejections vs trades")
print("=" * 65)

print(f"\n📊 TABLE SIZES:")
print(f"  trades          : {trade_count} rows  ({open_count} OPEN, {closed_count} CLOSED)")
print(f"  rejections      : {rejection_count} rows  ({unique_rejected_symbols} unique symbols)")
print(f"  market_analysis : {analysis_count} rows  ({unique_analysis_symbols} unique symbols)")

print(f"\n📋 REJECTIONS TABLE — What it stores (last 5 rows):")
print(f"  {'SYMBOL':<20} {'STRATEGY':<25} {'LAYER':<25} {'REASON'}")
print("  " + "-"*90)
for r in sample_rejections:
    print(f"  {str(r['symbol']):<20} {str(r['strategy']):<25} {str(r['layer']):<25} {str(r['reason'])[:40]}")

print(f"\n  Top Rejection Reasons:")
for r in rejection_breakdown:
    print(f"    {r['reason']:<40} {r['cnt']:>5} times")

print(f"\n📋 MARKET ANALYSIS TABLE — What it actually stores (last 3 rows):")
for i, row in enumerate(sample_analysis):
    try:
        data = json.loads(row['indicators'])
        keys = list(data.keys())
        print(f"  Row {i+1} keys: {keys}")
        # Show what unique info it has vs rejections
        unique_keys = [k for k in keys if k not in ('symbol', 'strategy', 'reason', 'layer', 'timestamp', 'side', 'entry_price', 'confidence')]
        print(f"  Unique info (not in rejections): {unique_keys}")
    except:
        print(f"  Row {i+1}: [empty or unparseable]")

print(f"\n🔍 OVERLAP ANALYSIS:")
print(f"  Every swept symbol ends up in EITHER:")
print(f"    a) trades       (signal fired + risk approved → OPEN)")
print(f"    b) rejections   (risk/strategy rejected it, reason logged)")
print(f"  market_analysis adds: per-sweep AGGREGATE COUNTS only")
print(f"  (total_analyses, volume_rejections, adx_rejections, etc.)")
print(f"\n  trades + rejections = complete audit trail of every symbol decision")
print(f"  market_analysis     = hourly aggregate summaries (counts, not decisions)")
print("=" * 65)
