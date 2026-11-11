#!/usr/bin/env python3
"""
hourly_paper_audit.py — Analyzes paper trading performance over the last hour:
1. Paper trades entered / closed in the last 60 minutes
2. Cross-references against Binance top 20 24h gainers
3. Analyzes rejection reasons for top gainers vs overall universe
"""

import os, sys, time, sqlite3, json, datetime
import urllib.request, urllib.parse

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(REPO_DIR, 'data/apex_hunter.db')
ACT_PATH = os.path.join(REPO_DIR, 'data/activity_log.db')

def get_top_gainers():
    url = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        valid = []
        for t in data:
            sym = t['symbol']
            if not sym.endswith('USDT'): continue
            vol = float(t.get('quoteVolume', 0))
            pct = float(t.get('priceChangePercent', 0))
            if vol >= 1_000_000:
                valid.append({
                    'symbol': sym.replace('USDT', '/USDT:USDT'),
                    'raw_symbol': sym,
                    'pct': pct,
                    'vol_m': vol / 1e6
                })
        valid.sort(key=lambda x: x['pct'], reverse=True)
        return valid[:20]
    except Exception as e:
        print("Error fetching tickers:", e)
        return []

def run_audit(lookback_minutes=60):
    cutoff_ts = int((time.time() - lookback_minutes * 60) * 1000)
    cutoff_iso = datetime.datetime.fromtimestamp(cutoff_ts / 1000, tz=datetime.timezone.utc).isoformat()
    
    print("=" * 80)
    print(f"📊 APEX HUNTER — 1-HOUR PAPER AUDIT ({datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})")
    print("=" * 80)
    
    # 1. Closed Trades in Window
    trades_in_window = []
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT symbol, side, entry_price, exit_price, pnl_amount, pnl_percent, reason, entry_time, exit_time 
                FROM trades 
                WHERE entry_time >= ?
                ORDER BY entry_time DESC
            """, (cutoff_iso,))
            trades_in_window = cur.fetchall()
        except Exception as e:
            print("Trades query error:", e)
        conn.close()
    
    print(f"\n[1] TRADES EXECUTED IN LAST {lookback_minutes}m: {len(trades_in_window)}")
    if trades_in_window:
        closed = [t for t in trades_in_window if t[4] is not None]
        tot_pnl = sum((t[4] or 0.0) for t in closed)
        wins = [t for t in closed if (t[4] or 0.0) > 0]
        losses = [t for t in closed if (t[4] or 0.0) <= 0]
        wr = (len(wins) / len(closed) * 100) if closed else 0.0
        print(f"    Closed: {len(closed)} | Open: {len(trades_in_window) - len(closed)} | Win Rate: {wr:.1f}% ({len(wins)}W / {len(losses)}L) | Net PnL: ${tot_pnl:+.2f} USDT")
        print(f"    {'Symbol':18} | {'Side':4} | {'Entry':10} | {'Exit':10} | {'PnL%':7} | {'Reason':15}")
        print("    " + "-" * 70)
        for t in trades_in_window[:10]:
            p_exit = f"{t[3]:10.4f}" if t[3] is not None else "OPEN      "
            p_pnl = f"{t[5]:+6.2f}%" if t[5] is not None else "  --- "
            reason = str(t[6] or "OPEN")[:15]
            print(f"    {t[0]:18} | {t[1]:4} | {t[2]:10.4f} | {p_exit} | {p_pnl} | {reason}")
    else:
        print("    No paper trades opened or closed yet in this 1-hour window.")

    # 2. Rejections & Signals in Activity Log
    rejections = {}
    if os.path.exists(ACT_PATH):
        conn = sqlite3.connect(ACT_PATH)
        cur = conn.cursor()
        try:
            # check rejections table
            cur.execute("""
                SELECT reason, count(*) 
                FROM rejections 
                WHERE timestamp >= ?
                GROUP BY reason
                ORDER BY count(*) DESC
            """, (cutoff_iso.replace('T', ' ')[:19],))
            rejections = dict(cur.fetchall())
        except Exception:
            pass
        conn.close()
        
    print(f"\n[2] TOP REJECTION REASONS ACROSS UNIVERSE:")
    if rejections:
        for r, cnt in list(rejections.items())[:8]:
            print(f"    - {r:35}: {cnt} times")
    else:
        print("    (Rejections recorded primarily in sweep_summary JSON)")

    # 3. Top 20 24h Gainers vs Bot Awareness
    top_gainers = get_top_gainers()
    print(f"\n[3] TODAY'S TOP 20 GAINERS VS BOT STATUS:")
    print(f"    {'Symbol':20} | {'24h %':8} | {'Vol($M)':8} | {'Status / Gate'}")
    print("    " + "-" * 65)
    
    # Check if any top gainer traded
    traded_symbols = {t[0] for t in trades_in_window}
    for g in top_gainers:
        s = g['symbol']
        raw = g['raw_symbol']
        status = "WATCHING (Awaiting >=65% wall)"
        if s in traded_symbols:
            status = "✅ TRADED"
        print(f"    {s:20} | {g['pct']:+7.2f}% | {g['vol_m']:7.1f}M | {status}")

    print("\n" + "=" * 80)

if __name__ == '__main__':
    run_audit(60)
