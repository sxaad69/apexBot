#!/usr/bin/env python3
"""
six_hour_gainers_audit.py — Audit the Top Gainers over the last 6 hours on Binance Futures
and determine whether Apex Hunter traded them, blocked them, or missed them.

Usage:
    python3 audit/six_hour_gainers_audit.py
"""
import collections
import datetime
import json
import os
import sqlite3
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_DB = os.path.join(REPO_DIR, 'data/apex_hunter.db')
ACT_DB = os.path.join(REPO_DIR, 'data/activity_log.db')

# Setup HTTP Proxy (for Oracle Ashburn or local environments)
proxy_url = os.environ.get('HTTP_PROXY') or 'http://127.0.0.1:8888'
try:
    proxy_handler = urllib.request.ProxyHandler({'http': proxy_url, 'https': proxy_url})
    opener = urllib.request.build_opener(proxy_handler)
    # Test proxy
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ping", headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=5) as resp:
        pass
    USE_PROXY = True
except Exception:
    opener = urllib.request.build_opener()
    USE_PROXY = False


def fetch_all_tickers():
    """Fetch 24hr tickers for all futures pairs."""
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/ticker/24hr", headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def fetch_exchange_info():
    """Fetch exchange info to identify pure crypto perpetual pairs."""
    req = urllib.request.Request("https://fapi.binance.com/fapi/v1/exchangeInfo", headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())
    
    valid_symbols = set()
    for s in data.get('symbols', []):
        sym = s.get('symbol', '')
        if s.get('contractType') == 'PERPETUAL' and s.get('status') == 'TRADING' and s.get('quoteAsset') == 'USDT':
            # Skip TradFi synthetics
            if s.get('underlyingType') == 'COIN' and 'TradFi' not in s.get('underlyingSubType', []):
                valid_symbols.add(sym)
    return valid_symbols


def get_symbol_6h_candle(symbol):
    """Fetch 1h klines and calculate 6h return."""
    try:
        url = f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval=1h&limit=7"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=8) as resp:
            klines = json.loads(resp.read().decode())
        if len(klines) >= 7:
            open_6h = float(klines[0][1])
            cur_price = float(klines[-1][4])
            high_6h = max(float(k[2]) for k in klines)
            low_6h = min(float(k[3]) for k in klines)
            pct_6h = ((cur_price - open_6h) / open_6h) * 100.0
            return {
                'symbol': symbol,
                'pct_6h': pct_6h,
                'open_6h': open_6h,
                'cur_price': cur_price,
                'high_6h': high_6h,
                'low_6h': low_6h
            }
    except Exception:
        pass
    return None


def get_bot_records(six_hours_ago_iso):
    """Query bot DBs for trades, active positions, rejections, and sweeps."""
    trades_map = {}
    
    # 1. Closed and open trades in apex_hunter.db
    if os.path.exists(TRADE_DB):
        conn = sqlite3.connect(TRADE_DB)
        c = conn.cursor()
        c.execute("""SELECT trade_id, symbol, strategy, entry_price, exit_price, pnl_amount, pnl_percent, reason, status, entry_time 
                     FROM trades WHERE entry_time >= ?""", (six_hours_ago_iso,))
        for r in c.fetchall():
            sym = r[1].split('/')[0].split(':')[0].upper()
            trades_map[sym] = {
                'trade_id': r[0],
                'strategy': 'A8' if 'A8' in r[2] else ('A9' if 'A9' in r[2] else 'A6'),
                'entry_price': r[3],
                'exit_price': r[4],
                'pnl_amount': r[5],
                'pnl_percent': r[6],
                'reason': r[7],
                'status': r[8],
                'entry_time': r[9]
            }
            
        c.execute("SELECT symbol, strategy, entry_price, leverage FROM active_positions")
        for r in c.fetchall():
            sym = r[0].split(':')[-2].split('/')[0].upper()
            if sym not in trades_map:
                trades_map[sym] = {
                    'strategy': 'A8' if 'A8' in r[1] else ('A9' if 'A9' in r[1] else 'A6'),
                    'entry_price': r[2],
                    'status': 'OPEN',
                    'reason': 'active_holding'
                }
        conn.close()

    # 2. Rejections from activity_log.db
    rejections_map = collections.defaultdict(list)
    if os.path.exists(ACT_DB):
        conn = sqlite3.connect(ACT_DB)
        c = conn.cursor()
        try:
            c.execute("""SELECT symbol, strategy, reason, layer, timestamp 
                         FROM rejections WHERE timestamp >= ?""", (six_hours_ago_iso,))
            for r in c.fetchall():
                sym = r[0].split('/')[0].split(':')[0].upper()
                rejections_map[sym].append((r[2], r[3], r[4]))
        except Exception:
            pass

        # 3. Check sweep_summary rejections
        try:
            c.execute("""SELECT metadata FROM activity_log WHERE type='sweep_summary' AND timestamp >= ?""", (six_hours_ago_iso,))
            for r in c.fetchall():
                try:
                    d = json.loads(r[0])
                    for strat, reasons in (d.get('strategy_rejections') or {}).items():
                        if isinstance(reasons, dict):
                            for reason, sym_dict in reasons.items():
                                if isinstance(sym_dict, dict):
                                    for s in sym_dict.keys():
                                        base = s.split('/')[0].upper()
                                        rejections_map[base].append((reason, strat, 'sweep'))
                except Exception:
                    pass
        except Exception:
            pass
        conn.close()

    return trades_map, rejections_map


def main():
    print("=" * 80)
    print("🎯 APEX HUNTER — 6-HOUR TOP GAINERS & BOT ATTRIBUTION AUDIT")
    print(f"Timestamp (UTC): {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # 1. Exchange metadata
    print("Step 1: Fetching valid pure crypto perpetual contracts...")
    valid_symbols = fetch_exchange_info()
    print(f"Identified {len(valid_symbols)} pure crypto linear contracts.")

    # 2. 24h tickers
    print("Step 2: Scanning 24h liquidity pools...")
    tickers = fetch_all_tickers()
    ticker_dict = {}
    for t in tickers:
        s = t.get('symbol', '')
        if s in valid_symbols and float(t.get('quoteVolume', 0)) >= 1000000:
            ticker_dict[s] = float(t.get('quoteVolume', 0))

    symbols_to_check = list(ticker_dict.keys())
    print(f"Screening {len(symbols_to_check)} pairs with 24h volume >= $1M...")

    # 3. Parallel fetch 6h klines
    print("Step 3: Calculating 6-hour price change across all contracts...")
    t0 = time.time()
    candle_results = []
    with ThreadPoolExecutor(max_workers=20) as executor:
        for res in executor.map(get_symbol_6h_candle, symbols_to_check):
            if res:
                candle_results.append(res)
    print(f"Calculated 6-hour returns for {len(candle_results)} symbols in {time.time() - t0:.2f}s.")

    # Sort descending by 6-hour % change
    candle_results.sort(key=lambda x: x['pct_6h'], reverse=True)
    top_gainers = candle_results[:25]

    # 4. Cross reference with Bot Trades & Rejections
    six_hours_ago = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%S')
    trades_map, rejections_map = get_bot_records(six_hours_ago)

    print("\n" + "=" * 80)
    print("🏆 TOP 25 GAINERS IN THE LAST 6 HOURS VS APEX HUNTER EXECUTION")
    print("=" * 80)
    print("| Rank | Symbol | 6h Gain % | 24h Vol ($M) | Bot Execution Status | Attribution / Gate Details |")
    print("|:---:|:---|:---:|:---:|:---|:---|")

    traded_count = 0
    blocked_count = 0
    missed_count = 0

    for i, g in enumerate(top_gainers, 1):
        sym_full = g['symbol']
        base = sym_full.replace('USDT', '')
        pct_6h = g['pct_6h']
        vol_m = ticker_dict.get(sym_full, 0) / 1000000.0

        if base in trades_map:
            traded_count += 1
            t_info = trades_map[base]
            strat = t_info.get('strategy', 'BOT')
            st = t_info.get('status', 'OPEN')
            if st == 'OPEN':
                status_str = f"🟢 **TRADED (OPEN)**"
                cur_px = g['cur_price']
                ep = t_info.get('entry_price', cur_px)
                roe = ((cur_px - ep) / ep) * 200.0 # 2x lev
                detail = f"{strat} active | Entry ${ep:.4f} | Live ROE {roe:+.2f}%"
            elif t_info.get('reason') == 'take_profit':
                pnl = t_info.get('pnl_percent', 0)
                status_str = f"🎯 **TRADED (TP)**"
                detail = f"{strat} Take-Profit Hit! Net: +{pnl:.2f}%"
            elif t_info.get('reason') == 'stop_loss' and (t_info.get('pnl_percent') or 0) > 0:
                pnl = t_info.get('pnl_percent', 0)
                status_str = f"🎯 **TRADED (TRAILING)**"
                detail = f"{strat} Trailing Stop Lock! Net: +{pnl:.2f}%"
            else:
                pnl = t_info.get('pnl_percent', 0)
                status_str = f"🔴 **TRADED (SL)**"
                detail = f"{strat} Stopped out: {pnl:.2f}% ({t_info.get('reason')})"
        elif base in rejections_map:
            blocked_count += 1
            reasons = [r[0] for r in rejections_map[base]]
            most_common = collections.Counter(reasons).most_common(1)[0][0]
            status_str = f"⚠️ **BLOCKED**"
            detail = f"Gate: {most_common}"
        else:
            missed_count += 1
            status_str = f"⚪ **NOT WATCHED**"
            detail = "Outside Top-60 Breakout Funnel"

        print(f"| #{i:02d} | **{base}** | **+{pct_6h:.2f}%** | ${vol_m:.1f}M | {status_str} | {detail} |")

    print("=" * 80)
    print(f"Summary: Traded: {traded_count} / 25 | Blocked by Risk/Strategy: {blocked_count} / 25 | Not Watched: {missed_count} / 25")
    print("=" * 80)


if __name__ == '__main__':
    main()
