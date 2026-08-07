#!/usr/bin/env python3
"""
Top Gainers Retrospective
Computes the top gainers/losers over the bot's exact runtime window
(Aug 5 22:25 -> Aug 6 18:54 UTC) using Binance Futures 1h OHLCV, then
cross-references against:
  - the bot's actual traded symbols (from SQLite)
  - the bot's watchlist approximation (top-N by 24h volume)

Answers: "While the bot was running, which were today's real top gainers,
and did the bot catch them or miss them?"
"""

import os
import json
import sys
import time
import sqlite3
import requests
import concurrent.futures
from datetime import datetime, timezone

# === Runtime window (from DB first entry -> last exit) ===
WINDOW_START = "2026-08-05T22:25:00"
WINDOW_END = "2026-08-07T17:00:00"

# === Config mirrors ===
FUTURES_AUTO_TOP_N = 100          # bot scans top N by 24h volume
FUTURES_AUTO_MIN_VOLUME = 500000  # bot min volume filter
SCAN_POOL = 300                   # how many top-volume symbols we fetch klines for

BASE = "https://fapi.binance.com/fapi/v1"
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "apex_hunter.db")


def to_ms(iso_str: str) -> int:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch 1h klines between start/end for one symbol."""
    params = {
        "symbol": symbol,
        "interval": "1h",
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": 1000,
    }
    for attempt in range(4):
        try:
            r = requests.get(f"{BASE}/klines", params=params, timeout=15)
            data = r.json()
            if isinstance(data, list):
                return data
            time.sleep(1.5)
        except Exception as e:
            print(f"  fetch err {symbol}: {e}", file=sys.stderr)
            time.sleep(1.5)
    return []


def compute_window_change(symbol: str, start_ms: int, end_ms: int):
    """Return (symbol, pct_change_over_window, open_price) or None."""
    kl = fetch_klines(symbol, start_ms, end_ms)
    if not kl:
        return None
    # first candle open, last candle close within window
    first_open = float(kl[0][1])
    last_close = float(kl[-1][4])
    if first_open <= 0:
        return None
    pct = (last_close - first_open) / first_open * 100
    return (symbol, round(pct, 2), round(first_open, 8))


def get_traded_symbols() -> dict:
    """symbol -> trade summary from DB"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, side, ROUND(entry_price,6) entry, entry_time, "
        "ROUND(pnl_amount,2) pnl, ROUND(pnl_percent,2) pnl_pct, status, reason "
        "FROM trades ORDER BY entry_time"
    ).fetchall()
    conn.close()
    traded = {}
    for r in rows:
        s = r['symbol'].split('/')[0]
        traded.setdefault(s, []).append(dict(r))
    return traded


def main():
    start_ms = to_ms(WINDOW_START) - 3600_000  # buffer 1h
    end_ms = to_ms(WINDOW_END) + 3600_000

    print("=" * 110)
    print("📈 TOP GAINERS RETROSPECTIVE — Bot Runtime Window")
    print(f"   Window: {WINDOW_START} -> {WINDOW_END} UTC")
    print(f"   Scan pool: top {SCAN_POOL} USDT perps by 24h volume (bot scans top {FUTURES_AUTO_TOP_N})")
    print("=" * 110)

    # 1. Get tickers -> volume rank
    print("\n🔄 Fetching tickers for volume ranking...")
    tick = requests.get(f"{BASE}/ticker/24hr", timeout=20).json()
    volume_sorted = []
    for t in tick:
        sym = t.get('symbol', '')
        if not sym.endswith('USDT'):
            continue
        if any(x in sym for x in ['BUSD', 'EUR', 'GBP', 'AUD', 'USDC']):
            continue
        try:
            vol = float(t.get('quoteVolume', 0) or 0)
            last = float(t.get('lastPrice', 0) or 0)
        except (ValueError, TypeError):
            continue
        if vol < FUTURES_AUTO_MIN_VOLUME:
            continue
        volume_sorted.append({'symbol': sym, 'volume': vol, 'last': last})
    volume_sorted.sort(key=lambda x: x['volume'], reverse=True)
    top_100 = {p['symbol'] for p in volume_sorted[:FUTURES_AUTO_TOP_N]}
    print(f"   Total USDT perps above ${FUTURES_AUTO_MIN_VOLUME:,}: {len(volume_sorted)}")

    # 2. Fetch klines for scan pool
    scan_symbols = [p['symbol'] for p in volume_sorted[:SCAN_POOL]]
    print(f"🕒 Computing window % change for {len(scan_symbols)} symbols (1h OHLCV)...")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(compute_window_change, s, start_ms, end_ms): s for s in scan_symbols}
        done = 0
        for f in concurrent.futures.as_completed(futs):
            res = f.result()
            if res:
                results.append(res)
            done += 1
            if done % 50 == 0 or done == len(scan_symbols):
                print(f"      scanned {done}/{len(scan_symbols)}", end='\r')
    print(f"\n   Computed for {len(results)} symbols")

    results.sort(key=lambda x: x[1], reverse=True)
    gainers = results[:20]
    losers = results[-10:]

    traded = get_traded_symbols()
    print(f"   Bot traded symbols: {len(traded)}\n")

    def flag(symbol):
        # Normalize: Binance "ACEUSDT" -> DB "ACE"
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        has_trade = base in traded
        if has_trade:
            trades = traded[base]
            pnl = sum(t['pnl'] or 0 for t in trades)
            return f"✅ TRADED ({len(trades)}x, P&L ${pnl:+.2f})"
        if symbol in top_100:
            return "👀 WATCHED (top-100 vol) — no trade"
        return "❌ MISSED (below top-100 by volume)"

    print("\n" + "=" * 110)
    print("🏆 TOP 20 GAINERS over the bot's runtime window")
    print("=" * 110)
    print(f"{'#':<3}{'SYMBOL':<14}{'%Change':>9}{'Open $':>16}  {'Bot Status'}")
    print("-" * 110)
    for i, (sym, pct, opn) in enumerate(gainers, 1):
        print(f"{i:<3}{sym:<14}{pct:>+8.2f}%{opn:>16.6f}  {flag(sym)}")

    print("\n" + "=" * 110)
    print("🔻 TOP 10 LOSERS over the bot's runtime window")
    print("=" * 110)
    print(f"{'#':<3}{'SYMBOL':<14}{'%Change':>9}{'Open $':>16}  {'Bot Status'}")
    print("-" * 110)
    for i, (sym, pct, opn) in enumerate(losers, 1):
        print(f"{i:<3}{sym:<14}{pct:>+8.2f}%{opn:>16.6f}  {flag(sym)}")

    print("\n" + "=" * 110)
    print("🏁 COMPLETE")
    print("=" * 110)


if __name__ == "__main__":
    main()