#!/usr/bin/env python3
"""
APEX FORENSICS - Unified Trading Bot Audit Tool
=================================================
Three analysis modes in a single tool:

  MODE 1: Exit Forensics (default)
    For each closed trade: TP/SL validation, ride quality, dollar capture,
    forward-looking post-exit analysis (4h window), trailing stop effectiveness.

  MODE 2: Top Gainers Retrospective (--toppers)
    Market's real top gainers/losers during the bot's runtime window,
    cross-referenced against traded symbols and watchlist.

  MODE 3: Missed Alpha Analysis (--missed-alpha)
    For signals the bot rejected (strategy/risk filters), measures whether
    they would have been profitable in the following 4 hours.

  Use --all to run all three modes sequentially.

Usage:
  python audit/apex_forensics.py --from 2026-08-06 --to 2026-08-07
  python audit/apex_forensics.py --days 7
  python audit/apex_forensics.py --summary-only
  python audit/apex_forensics.py --filter-exit RATCHET_LIQUIDATION
  python audit/apex_forensics.py --toppers --from 2026-08-07
  python audit/apex_forensics.py --missed-alpha --from 2026-08-07
  python audit/apex_forensics.py --all --from 2026-08-07
  python audit/apex_forensics.py --settle              # daily settle → permanent JSON + cache purge (systemd timer)
  python audit/apex_forensics.py --fetch-only --days 1 # pre-fetch OHLCV into the cache only
  python audit/apex_forensics.py --purge-cache         # delete all cached OHLCV files

OHLCV is cached per symbol+interval in data/ohlcv_cache (watermark-based: only
missing deltas are fetched). Reports are written to data/reports/forensics_report_YYYY-MM-DD.json.
"""

import sqlite3
import requests
import time
import argparse
import json
import os
import random
import threading
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import concurrent.futures

DB_PATH = "data/apex_hunter.db"
LOG_DB_PATH = "data/activity_log.db"
CACHE_DIR = "data/ohlcv_cache"
REPORTS_DIR = "data/reports"
BINANCE = "https://fapi.binance.com/fapi/v1/klines"
BINANCE_BASE = "https://fapi.binance.com/fapi/v1"

# Rate limiting (global token bucket — protects Binance from hammering)
RATE_LIMIT_QPS = 10.0   # max requests per second
MAX_WORKERS = 10        # exploration threadpool
SETTLE_WORKERS = 8      # settle threadpool (t3.micro friendly)

# Top Gainers config
FUTURES_AUTO_TOP_N = 100          # bot scans top N by 24h volume
FUTURES_AUTO_MIN_VOLUME = 500000  # bot min volume filter
SCAN_POOL = 300                   # how many top-volume symbols we fetch klines for

# Interval step sizes (ms per candle)
INTERVAL_MS = {"1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000}


# --- Cache v2: watermark-based incremental OHLCV store -----------------------
# One file per symbol+interval: data/ohlcv_cache/{SYMBOL}_{INTERVAL}.json
# { "symbol": ..., "interval": ..., "updated_at": ..., "fetched_until_ms": ..., "candles": [...] }
# A request is a pure disk read when the file fully covers [start_ms, end_ms].
# Otherwise only the missing delta is fetched, merged, deduped, and saved.
# ----------------------------------------------------------------------------

_cache_lock = threading.Lock()
_rate_lock = threading.Lock()
_next_request_ok = 0.0


def _rate_wait():
    """Token-bucket rate limiter (global, thread-safe, jittered)."""
    global _next_request_ok
    with _rate_lock:
        now = time.time()
        wait = _next_request_ok - now
        if wait > 0:
            time.sleep(wait + random.uniform(0, 0.02))
        _next_request_ok = max(now, _next_request_ok) + (1.0 / RATE_LIMIT_QPS)


def _load_cache_entry(path):
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return None


def _save_cache_entry(path, entry):
    with _cache_lock:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(entry, fh)
        os.replace(tmp, path)  # atomic — no partial files


def _merge_candles(existing, new):
    """Merge candle lists, dedupe on open_time, keep sorted."""
    d = {c[0]: c for c in existing}
    for c in new:
        d[c[0]] = c
    return sorted(d.values(), key=lambda c: c[0])


def _fetch_range_raw(symbol, interval, start_ms, end_ms):
    """Fetch raw klines for [start_ms, end_ms) from Binance, paginated.
    Returns (candles, verified_until_ms). A checked-but-empty range advances
    verified_until_ms to end_ms so we never re-ask Binance for it."""
    step = INTERVAL_MS.get(interval, 60000)
    klines = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        ok = False
        for attempt in range(4):
            try:
                _rate_wait()
                r = requests.get(BINANCE, params=params, timeout=15)
                if r.status_code == 429:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                data = r.json()
                if isinstance(data, list):
                    if not data:
                        cursor = end_ms  # checked, nothing there
                    else:
                        klines.extend(data)
                        cursor = data[-1][0] + step
                    ok = True
                    break
                time.sleep(0.5 * (attempt + 1))  # error payload
            except Exception:
                time.sleep(1.0 * (attempt + 1))
        if not ok:
            break  # give up on this cursor; keep what we have
    return klines, cursor


def fetch_ohlcv(symbol, start_ms, end_ms, interval="1m", use_cache=True):
    """Fetch OHLCV candles with incremental watermark caching.
    Same signature as before — callers unchanged."""
    cache_path = os.path.join(CACHE_DIR, f"{symbol}_{interval}.json")
    entry = _load_cache_entry(cache_path) if use_cache else None

    if entry is None:
        # Cold start
        klines, verified = _fetch_range_raw(symbol, interval, start_ms, end_ms)
        if use_cache:
            fetched_until = max(verified, (klines[-1][0] + INTERVAL_MS.get(interval, 60000)) if klines else 0)
            _save_cache_entry(cache_path, {
                "symbol": symbol,
                "interval": interval,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "fetched_until_ms": fetched_until,
                "candles": klines,
            })
        return klines

    candles = entry.get("candles") or []
    fetched_until = entry.get("fetched_until_ms") or 0

    # Fully covered → pure disk read
    if candles and candles[0][0] <= start_ms and fetched_until >= end_ms:
        return [c for c in candles if start_ms <= c[0] < end_ms]
    if not candles and fetched_until >= end_ms:
        return []  # previously checked this empty range

    new_candles = list(candles)

    # Missing head (earlier than first stored candle)
    if candles and start_ms < candles[0][0]:
        head, _ = _fetch_range_raw(symbol, interval, start_ms, candles[0][0])
        new_candles = _merge_candles(new_candles, head)

    # Missing tail (beyond watermark)
    tail_start = max(fetched_until, start_ms)
    if end_ms > tail_start:
        tail, verified = _fetch_range_raw(symbol, interval, tail_start, end_ms)
        new_candles = _merge_candles(new_candles, tail)
        fetched_until = max(fetched_until, verified)

    entry["candles"] = new_candles
    entry["fetched_until_ms"] = fetched_until
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_cache_entry(cache_path, entry)

    if not new_candles:
        return []
    return [c for c in new_candles if start_ms <= c[0] < end_ms]


def purge_cache():
    """Delete all cached OHLCV files (used after a successful settle)."""
    if os.path.exists(CACHE_DIR):
        removed = 0
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                try:
                    os.remove(os.path.join(CACHE_DIR, f))
                    removed += 1
                except OSError:
                    pass
        return removed
    return 0


def cache_file_count():
    if not os.path.exists(CACHE_DIR):
        return 0
    return sum(1 for f in os.listdir(CACHE_DIR) if f.endswith(".json"))


def to_binance(db_sym):
    return db_sym.replace("/USDT:USDT", "USDT")


def audit_trade(t):
    """Comprehensive audit of a single trade"""
    sym = t["symbol"]
    b_sym = to_binance(sym)
    entry = float(t["entry_price"])
    exit_p = float(t["exit_price"]) if t["exit_price"] else 0
    tp = float(t["take_profit"]) if t["take_profit"] else None
    sl = float(t["stop_loss"]) if t["stop_loss"] else None
    peak_db = float(t["highest_price"]) if t["highest_price"] else entry
    trail = float(t["trailing_stop_price"]) if t["trailing_stop_price"] else None
    reason = t["reason"] or "UNKNOWN"
    exit_pnl = float(t["pnl_percent"]) if t["pnl_percent"] else 0
    side = (t["side"] or "buy").lower()

    # Parse timestamps
    try:
        entry_str = t["entry_time"].split(".")[0]
        exit_str = t["exit_time"].split(".")[0] if t["exit_time"] else entry_str
        entry_dt = datetime.fromisoformat(entry_str).replace(tzinfo=timezone.utc)
        exit_dt = datetime.fromisoformat(exit_str).replace(tzinfo=timezone.utc)
    except:
        return None

    entry_ms = int(entry_dt.timestamp() * 1000)
    exit_ms = int(exit_dt.timestamp() * 1000)
    post_exit_ms = exit_ms + (4 * 3600 * 1000)  # 4 hours after exit

    # Fetch 1m candles during trade (precision for TP/SL)
    klines = fetch_ohlcv(b_sym, entry_ms, exit_ms, interval="1m")
    if not klines:
        return {"sym": sym, "error": "No OHLCV"}
    
    # Fetch 5m candles 4h after exit (5x fewer API calls, still accurate for peaks)
    post_exit_klines = fetch_ohlcv(b_sym, exit_ms, post_exit_ms, interval="5m")

    # Replay candles to find real peak/low
    real_peak = entry
    real_low = entry
    tp_hit = False
    sl_hit = False

    for i, k in enumerate(klines):
        h = float(k[2])
        l = float(k[3])
        if h > real_peak:
            real_peak = h
        if l < real_low:
            real_low = l
        if tp and h >= tp and not tp_hit:
            tp_hit = True
        if sl and l <= sl and not sl_hit:
            sl_hit = True

    # Analyze post-exit candles (what we missed)
    post_exit_high = exit_p
    post_exit_low = exit_p
    if post_exit_klines:
        for k in post_exit_klines:
            h = float(k[2])
            l = float(k[3])
            if h > post_exit_high:
                post_exit_high = h
            if l < post_exit_low:
                post_exit_low = l

    findings = {
        "sym": sym,
        "entry": entry,
        "exit": exit_p,
        "exit_pnl": round(exit_pnl, 2),
        "side": side,
        "reason": reason,
        "tp": tp,
        "sl": sl,
        "trail": trail,
        "peak_db": peak_db,
        "real_peak": real_peak,
        "real_low": real_low,
        "candles": len(klines),
        "post_exit_high": post_exit_high,
        "post_exit_low": post_exit_low,
        "post_exit_candles": len(post_exit_klines) if post_exit_klines else 0,
    }

    # 1. TP Analysis
    if tp:
        peak_profit_pct = ((real_peak - entry) / entry) * 100
        tp_dist_pct = ((tp - entry) / entry) * 100
        findings["peak_profit_pct"] = round(peak_profit_pct, 2)
        findings["tp_dist_pct"] = round(tp_dist_pct, 2)

        if tp_hit:
            findings["tp_status"] = f"TP REACHED (peak {peak_profit_pct:.1f}% vs TP at {tp_dist_pct:.1f}%)"
            if "TAKE_PROFIT" in reason or "TP" in reason:
                findings["tp_status"] += " + EXITED AT TP ✅"
            elif "RATCHET" in reason:
                findings["tp_status"] += " + EXITED BY RATCHET (ratchet locked before TP)"
            elif "TRAILING" in reason:
                findings["tp_status"] += f" ⚠️ EXITED BY TRAILING (trailing triggered at worse price than TP)"
            else:
                findings["tp_status"] += f" ⚠️ EXITED BY {reason} (not TP)"

            if exit_pnl < tp_dist_pct * 0.9:
                findings["tp_status"] += f" ⚠️ EXIT WORSE THAN TP (got {exit_pnl:.1f}% vs {tp_dist_pct:.1f}%)"
                findings["tp_exit_worse"] = True
            else:
                findings["tp_exit_worse"] = False
        else:
            findings["tp_status"] = f"TP NOT REACHED (peak {peak_profit_pct:.1f}% vs TP at {tp_dist_pct:.1f}%)"
            findings["tp_exit_worse"] = False
    else:
        findings["tp_exit_worse"] = False

    # 2. SL Analysis
    if sl:
        max_loss_pct = ((real_low - entry) / entry) * 100 if side == "buy" else ((entry - real_low) / entry) * 100
        sl_dist_pct = ((entry - sl) / entry) * 100
        findings["max_loss_pct"] = round(max_loss_pct, 2)
        findings["sl_dist_pct"] = round(sl_dist_pct, 2)

        if sl_hit:
            findings["sl_status"] = f"SL REACHED (low {max_loss_pct:.1f}% vs SL at -{sl_dist_pct:.1f}%)"
            if "STOP" in reason:
                findings["sl_status"] += " + EXITED AT SL ✅"
            else:
                findings["sl_status"] += f" ⚠️ EXIT REASON WAS {reason} (not SL)"
        else:
            findings["sl_status"] = f"SL NOT REACHED (min draw {max_loss_pct:.1f}% vs SL at -{sl_dist_pct:.1f}%)"

    # 3. Peak capture (WSS tracking accuracy)
    if real_peak > 0:
        capture = (peak_db / real_peak) * 100
        findings["peak_capture"] = round(capture, 1)

    # 4. Ride quality (dollar capture)
    if side == "buy":
        peak_pnl = ((real_peak - entry) / entry) * 100
    else:
        peak_pnl = ((entry - real_low) / entry) * 100
    
    if peak_pnl > 0:
        ride_quality = (exit_pnl / peak_pnl) * 100
    else:
        ride_quality = 100 if exit_pnl >= 0 else 0
    
    findings["peak_pnl"] = round(peak_pnl, 2)
    findings["ride_quality"] = round(ride_quality, 1)
    findings["missed_pnl"] = round(peak_pnl - exit_pnl, 2)

    # 5. Better exit?
    missed = peak_pnl - exit_pnl
    findings["missed_profit_pct"] = round(missed, 2)
    if missed > 2.0:
        findings["better_exit"] = f"YES - missed +{missed:.1f}% (peak was +{peak_pnl:.1f}% vs exit +{exit_pnl:.1f}%)"
    else:
        findings["better_exit"] = f"No (peak +{peak_pnl:.1f}% vs exit +{exit_pnl:.1f}%)"

    # 6. Trailing Stop Effectiveness (Forward-Looking)
    # What happened AFTER we exited?
    if side == "buy":
        post_exit_pnl = ((post_exit_high - entry) / entry) * 100
        continuation = ((post_exit_high - exit_p) / entry) * 100
        adverse_move = ((post_exit_low - exit_p) / entry) * 100  # How much it dropped after exit
    else:
        post_exit_pnl = ((entry - post_exit_low) / entry) * 100
        continuation = ((exit_p - post_exit_low) / entry) * 100
        adverse_move = ((exit_p - post_exit_high) / entry) * 100  # How much it pumped after exit
    
    # Total move available (peak during trade OR post-exit high)
    if side == "buy":
        total_peak_available = max(real_peak, post_exit_high)
        total_peak_pnl = ((total_peak_available - entry) / entry) * 100
    else:
        total_peak_available = min(real_low, post_exit_low)
        total_peak_pnl = ((entry - total_peak_available) / entry) * 100
    
    # Exit quality: what % of total available move did we capture?
    if total_peak_pnl > 0:
        exit_quality = (exit_pnl / total_peak_pnl) * 100
    else:
        exit_quality = 100 if exit_pnl >= 0 else 0
    
    findings["post_exit_pnl"] = round(post_exit_pnl, 2)
    findings["continuation"] = round(continuation, 2)
    findings["adverse_move"] = round(adverse_move, 2)
    findings["total_peak_pnl"] = round(total_peak_pnl, 2)
    findings["exit_quality"] = round(exit_quality, 1)
    findings["early_exit"] = continuation > 2.0
    findings["good_exit"] = adverse_move < -2.0  # Price moved against us after exit

    return findings


# ============================================================================
# MODE 2: Top Gainers Retrospective
# ============================================================================

def fetch_tickers():
    """Fetch 24h ticker data from Binance futures (rate-limited)."""
    _rate_wait()
    try:
        r = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=20)
        if r.status_code == 429:
            time.sleep(5)
            _rate_wait()
            r = requests.get(f"{BINANCE_BASE}/ticker/24hr", timeout=20)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def compute_window_change(symbol, start_ms, end_ms):
    """Return (symbol, pct_change, open_price) or None using cached 1h OHLCV."""
    kl = fetch_ohlcv(symbol, start_ms, end_ms, interval="1h")
    if not kl:
        return None
    first_open = float(kl[0][1])
    last_close = float(kl[-1][4])
    if first_open <= 0:
        return None
    pct = (last_close - first_open) / first_open * 100
    return (symbol, round(pct, 2), round(first_open, 8))


def get_traded_symbols(start_ms, end_ms):
    """Return {base_symbol: [trade_rows]} from the DB within [start_ms, end_ms]."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    start_str = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
    end_str = datetime.fromtimestamp(end_ms / 1000).strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT symbol, side, entry_time, exit_time, pnl_amount, pnl_percent, status, reason "
        "FROM trades WHERE entry_time >= ? AND entry_time <= ? ORDER BY entry_time",
        (start_str, end_str),
    ).fetchall()
    conn.close()
    traded = {}
    for r in rows:
        base = r["symbol"].split("/")[0]
        traded.setdefault(base, []).append(dict(r))
    return traded


def run_toppers_analysis(args):
    """Analyze top market gainers during the trading period"""
    print("\n" + "="*120)
    print("MODE 2: TOP GAINERS RETROSPECTIVE")
    print("="*120)
    
    # Determine time window (UTC-consistent)
    start_ms, end_ms = resolve_window_ms(args)

    
    print(f"Window: {datetime.fromtimestamp(start_ms/1000).strftime('%Y-%m-%d')} to {datetime.fromtimestamp(end_ms/1000).strftime('%Y-%m-%d')}")
    
    # Get top volume symbols
    print("\nFetching top 300 symbols by volume...")
    tickers = fetch_tickers()
    if not tickers:
        print("⚠️ Failed to fetch tickers")
        return
    
    # Filter and sort by volume
    volume_sorted = []
    for t in tickers:
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
    top_n_set = {p['symbol'] for p in volume_sorted[:FUTURES_AUTO_TOP_N]}
    print(f"Total USDT perps above ${FUTURES_AUTO_MIN_VOLUME:,}: {len(volume_sorted)}")
    
    # Fetch klines for scan pool
    scan_symbols = [p['symbol'] for p in volume_sorted[:SCAN_POOL]]
    print(f"Computing window % change for {len(scan_symbols)} symbols (1h OHLCV)...")
    
    # Parallel fetch
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {executor.submit(compute_window_change, s, start_ms, end_ms): s for s in scan_symbols}
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                results.append(res)
    
    if not results:
        print("⚠️ No data fetched")
        return
    
    results.sort(key=lambda x: x[1], reverse=True)
    gainers = results[:20]
    losers = results[-10:]
    
    # Get traded symbols
    traded = get_traded_symbols(start_ms, end_ms)
    print(f"Bot traded symbols: {len(traded)}")
    
    # Flag function
    def flag(symbol):
        base = symbol[:-4] if symbol.endswith("USDT") else symbol
        if base in traded:
            trades = traded[base]
            pnl = sum(t['pnl_amount'] or 0 for t in trades)
            return f"✅ TRADED ({len(trades)}x, P&L ${pnl:+.2f})"
        if symbol in top_n_set:
            return "👀 WATCHED (top-100 vol) — no trade"
        return "❌ MISSED (below top-100 by volume)"
    
    # Count coverage
    traded_count = sum(1 for sym, _, _ in gainers if flag(sym).startswith("✅"))
    watched_count = sum(1 for sym, _, _ in gainers if "WATCHED" in flag(sym))
    missed_count = sum(1 for sym, _, _ in gainers if "MISSED" in flag(sym))
    
    print(f"\nTOP-20 COVERAGE: {traded_count} traded | {watched_count} watched | {missed_count} missed")
    
    print("\n" + "-"*120)
    print("🏆 TOP 20 GAINERS over the bot's runtime window")
    print("-"*120)
    print(f"{'#':<4}{'SYMBOL':<14}{'%Change':>9}{'Open $':>16}  {'Bot Status'}")
    print("-"*120)
    for i, (sym, pct, opn) in enumerate(gainers, 1):
        print(f"{i:<4}{sym:<14}{pct:>+8.2f}%{opn:>16.6f}  {flag(sym)}")
    
    print("\n" + "-"*120)
    print("🔻 TOP 10 LOSERS over the bot's runtime window")
    print("-"*120)
    print(f"{'#':<4}{'SYMBOL':<14}{'%Change':>9}{'Open $':>16}  {'Bot Status'}")
    print("-"*120)
    for i, (sym, pct, opn) in enumerate(losers, 1):
        print(f"{i:<4}{sym:<14}{pct:>+8.2f}%{opn:>16.6f}  {flag(sym)}")
    
    print("\n" + "="*120)

    return {
        "scan_pool": len(scan_symbols),
        "analyzed": len(results),
        "top_gainers": [{"symbol": s, "pct_change": p, "open": o, "status": flag(s)} for s, p, o in gainers],
        "top_losers": [{"symbol": s, "pct_change": p, "open": o, "status": flag(s)} for s, p, o in losers],
        "coverage": {"traded": traded_count, "watched": watched_count, "missed": missed_count},
    }


# ============================================================================
# MODE 3: Missed Alpha Analysis (rejected signals)
# ============================================================================

def _parse_ts_ms(ts_str):
    """Parse SQLite/ISO timestamp to epoch ms (UTC). Returns None on failure."""
    if not ts_str:
        return None
    try:
        return int(datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except ValueError:
        try:
            return int(datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp() * 1000)
        except Exception:
            return None


def analyze_rejection_fx(rej):
    """Measure a rejected signal against the following 4h move (cached 1m OHLCV)."""
    symbol = to_binance(rej.get("symbol", ""))
    start_ms = _parse_ts_ms(rej.get("timestamp"))
    if not symbol or not start_ms:
        return None
    end_ms = start_ms + 4 * 3600 * 1000

    kl = fetch_ohlcv(symbol, start_ms, end_ms, interval="1m")
    if not kl:
        return None

    highs = [float(c[2]) for c in kl]
    lows = [float(c[3]) for c in kl]

    entry = float(rej.get("entry_price") or 0)
    if entry <= 0:
        entry = float(kl[0][1])  # strategy skip → use first candle open
    if entry <= 0:
        return None

    side = (rej.get("side") or "all").lower()
    if side == "buy":
        potential_pnl = (max(highs) - entry) / entry * 100
        max_draw = (min(lows) - entry) / entry * 100
    elif side == "sell":
        potential_pnl = (entry - min(lows)) / entry * 100
        max_draw = (entry - max(highs)) / entry * 100
    else:
        # Direction unknown → best of both legs
        long_pnl = (max(highs) - entry) / entry * 100
        short_pnl = (entry - min(lows)) / entry * 100
        if long_pnl >= short_pnl:
            potential_pnl = long_pnl
            max_draw = (min(lows) - entry) / entry * 100
        else:
            potential_pnl = short_pnl
            max_draw = (entry - max(highs)) / entry * 100

    return {
        "symbol": rej.get("symbol"),
        "timestamp": rej.get("timestamp"),
        "strategy": rej.get("strategy"),
        "side": side,
        "reason": rej.get("reason"),
        "layer": rej.get("layer"),
        "confidence": rej.get("confidence"),
        "potential_pnl": round(potential_pnl, 2),
        "max_drawdown": round(max_draw, 2),
    }


def run_missed_alpha_analysis(args):
    """Analyze rejected signals and measure foregone profit in the 4h after each."""
    print("\n" + "="*120)
    print("MODE 3: MISSED ALPHA ANALYSIS (REJECTED SIGNALS)")
    print("="*120)

    if not os.path.exists(LOG_DB_PATH):
        print("⚠️ activity_log.db not found — skipping Mode 3")
        return {"error": "no log db"}

    # Window filters
    where = []
    params = []
    if args.from_date:
        where.append("timestamp >= ?")
        params.append(args.from_date)
    if args.to_date:
        where.append("timestamp <= ?")
        params.append(args.to_date + " 23:59:59")

    conn = sqlite3.connect(LOG_DB_PATH)
    conn.row_factory = sqlite3.Row
    if where:
        rows = conn.execute(f"SELECT * FROM rejections WHERE {' AND '.join(where)} ORDER BY timestamp DESC", params).fetchall()
    else:
        rows = conn.execute("SELECT * FROM rejections ORDER BY timestamp DESC").fetchall()
    conn.close()

    if not rows:
        print("ℹ️ No rejections found in database.")
        return {"error": "no rejections"}

    print(f"Found {len(rows)} rejected signals. Measuring 4h alpha for each...")

    results = []
    for rej in rows:
        r = analyze_rejection_fx(dict(rej))
        if r:
            results.append(r)

    if not results:
        print("⚠️ No analyzable rejections (missing OHLCV data).")
        return {"error": "no data"}

    results.sort(key=lambda x: x["potential_pnl"], reverse=True)
    winners = [r for r in results if r["potential_pnl"] > 1.0]
    missed_total = sum(r["potential_pnl"] for r in winners)

    print(f"\nAnalyzed {len(results)} rejections | {len(winners)} moved >1% in 4h | total missed alpha: {missed_total:+.2f}%\n")

    # Impact maps by category and layer
    impact = {"STRATEGY": {}, "RISK": {}, "OTHER": {}}
    for r in results:
        category = "STRATEGY" if "STRATEGY" in str(r["reason"]) else ("RISK" if "RISK" in str(r["reason"]) else "OTHER")
        layer = r["layer"] or "unknown"
        stats = impact[category].setdefault(layer, {"count": 0, "winners": 0, "missed_alpha": 0.0})
        stats["count"] += 1
        if r["potential_pnl"] > 1.0:
            stats["winners"] += 1
            stats["missed_alpha"] += r["potential_pnl"]

    print("-"*120)
    print(f"{'Timestamp':<20} | {'Type':<9} | {'Layer/Filter':<20} | {'Symbol':<14} | {'Side':<5} | {'Max ROI':>8} | {'Max Draw':>8}")
    print("-"*120)
    for r in results[:20]:
        e_type = "STRATEGY" if "STRATEGY" in str(r["reason"]) else ("RISK" if "RISK" in str(r["reason"]) else "OTHER")
        print(f"{str(r['timestamp']):<20} | {e_type:<9} | {str(r['layer']):<20} | {str(r['symbol']):<14} | {r['side'].upper():<5} | {r['potential_pnl']:>+7.2f}% | {r['max_drawdown']:>+7.2f}%")

    for category in ["STRATEGY", "RISK", "OTHER"]:
        print(f"\n🛡️ {category} FRICTION ANALYSIS")
        print("-"*80)
        print(f"{'Layer/Filter':<30} | {'Events':<10} | {'Winners':<8} | {'Avg Missed ROI':>14}")
        print("-"*80)
        cat_stats = impact[category]
        if not cat_stats:
            print("   No events found.")
            continue
        for layer, stats in sorted(cat_stats.items(), key=lambda x: x[1]["missed_alpha"], reverse=True):
            avg_alpha = (stats["missed_alpha"] / stats["winners"]) if stats["winners"] > 0 else 0
            print(f"{layer:<30} | {stats['count']:<10} | {stats['winners']:<8} | {avg_alpha:>+13.2f}%")

    print("\n💡 STRATEGIC ADVISORY:")
    strat_bottleneck = max(impact["STRATEGY"].items(), key=lambda x: x[1]["missed_alpha"], default=(None, None))[0]
    if strat_bottleneck:
        print(f"⚠️  {strat_bottleneck} is causing the most strategy silence — consider loosening ADX/ATR thresholds.")
    risk_bottleneck = max(impact["RISK"].items(), key=lambda x: x[1]["missed_alpha"], default=(None, None))[0]
    if risk_bottleneck:
        print(f"⚠️  {risk_bottleneck} is your primary risk veto — review max positions/correlation tightness.")
    if not strat_bottleneck and not risk_bottleneck:
        print("✅ Bot is well-tuned. No significant missed alpha found.")

    print("\n" + "="*120)

    return {
        "analyzed": len(results),
        "winners": len(winners),
        "missed_total_pct": round(missed_total, 2),
        "top_events": results[:20],
        "friction": {c: impact[c] for c in impact},
    }


# ============================================================================
# Settle pipeline & cache warmers
# ============================================================================

def resolve_window_ms(args):
    """Resolve analysis window to (start_ms, end_ms) using args, defaulting to 7d."""
    if args.from_date:
        start_ms = int(datetime.strptime(args.from_date, "%Y-%m-%d").timestamp() * 1000)
    elif args.days:
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.days)).timestamp() * 1000)
    else:
        start_ms = int((datetime.now(timezone.utc) - timedelta(days=7)).timestamp() * 1000)
    if args.to_date:
        end_ms = int(datetime.strptime(args.to_date, "%Y-%m-%d").timestamp() * 1000) + 86400000
    else:
        end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return start_ms, end_ms


def run_fetch_only(args):
    """Pre-fetch OHLCV into the cache without running any analysis."""
    print("\n⏳ FETCH-ONLY: warming OHLCV cache...")
    start_ms, end_ms = resolve_window_ms(args)
    start_str = datetime.fromtimestamp(start_ms / 1000).strftime("%Y-%m-%d")
    end_str = datetime.fromtimestamp(end_ms / 1000).strftime("%Y-%m-%d")
    print(f"   Window: {start_str} → {end_str}")

    # 1) Scan-pool symbols: 1h klines (Mode 2 needs these)
    tickers = fetch_tickers()
    scan_syms = []
    if tickers:
        volume_sorted = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if any(x in sym for x in ["BUSD", "EUR", "GBP", "AUD", "USDC"]):
                continue
            try:
                vol = float(t.get("quoteVolume", 0) or 0)
            except (ValueError, TypeError):
                continue
            if vol >= FUTURES_AUTO_MIN_VOLUME:
                volume_sorted.append((sym, vol))
        volume_sorted.sort(key=lambda x: x[1], reverse=True)
        scan_syms = [s for s, _ in volume_sorted[:SCAN_POOL]]
        print(f"   Fetching 1h OHLCV for {len(scan_syms)} scan-pool symbols...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            list(ex.map(lambda s: fetch_ohlcv(s, start_ms, end_ms, "1h"), scan_syms))

    # 2) Traded symbols: 1m + 5m klines (Modes 1 & 3 need these)
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM trades WHERE entry_time >= ? AND entry_time <= ?",
        (start_str, end_str),
    ).fetchall()
    conn.close()
    traded_syms = [to_binance(r[0]) for r in rows]
    for interval in ("1m", "5m"):
        print(f"   Fetching {interval} OHLCV for {len(traded_syms)} traded symbols...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            list(ex.map(lambda s, iv=interval: fetch_ohlcv(s, start_ms, end_ms, iv), traded_syms))

    print(f"   ✅ Cache warmed: {cache_file_count()} files in {CACHE_DIR}")


def run_settle(args):
    """Daily settle pipeline: run all 3 modes, write a permanent JSON report, purge cache."""
    print("\n" + "="*120)
    print("⚙️  DAILY SETTLE PIPELINE")
    print("="*120)

    if args.from_date:
        start_date = args.from_date
    else:
        start_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    if args.to_date:
        end_date = args.to_date
    else:
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Normalize mode windows to the settle period so all 3 modes agree
    import copy
    mode_args = copy.copy(args)
    mode_args.from_date = start_date
    mode_args.to_date = end_date

    os.makedirs(REPORTS_DIR, exist_ok=True)
    report_path = os.path.join(REPORTS_DIR, f"forensics_report_{end_date}.json")

    payload = {
        "report_date": end_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": {"from": start_date, "to": end_date},
        "modes": {},
        "summary": {},
    }

    mode1 = run_exit_forensics(mode_args)
    mode2 = run_toppers_analysis(mode_args)
    mode3 = run_missed_alpha_analysis(mode_args)

    payload["modes"]["exit_forensics"] = mode1 if isinstance(mode1, dict) else {}
    payload["modes"]["top_gainers"] = mode2 if isinstance(mode2, dict) else {}
    payload["modes"]["missed_alpha"] = mode3 if isinstance(mode3, dict) else {}

    # Cross-mode executive summary
    summary = {}
    if isinstance(mode1, dict) and mode1.get("results"):
        r = mode1["results"]
        summary["trades_audited"] = len(r)
        summary["avg_exit_pnl"] = round(sum(x["exit_pnl"] for x in r) / len(r), 2)
        summary["missed_continuation_total"] = round(sum(x.get("continuation", 0) for x in r if x.get("continuation", 0) > 0), 2)
    if isinstance(mode2, dict) and mode2.get("top_gainers"):
        summary["top_gainers"] = mode2["top_gainers"][:10]
        summary["top_losers"] = mode2["top_losers"][:5]
        summary["coverage"] = mode2.get("coverage")
    if isinstance(mode3, dict) and "analyzed" in mode3:
        summary["missed_alpha"] = {k: mode3.get(k) for k in ("analyzed", "winners", "missed_total_pct")}
    payload["summary"] = summary

    # Atomic write
    tmp = report_path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2, default=str)
    os.replace(tmp, report_path)

    removed = purge_cache()
    print("\n" + "="*120)
    print("✅ SETTLE COMPLETE")
    print(f"   Report:   {report_path}")
    print(f"   Cache:    {removed} files purged")
    print(f"   Summary:  {json.dumps(summary, indent=2, default=str)}")
    print("="*120)


def main():
    parser = argparse.ArgumentParser(description="APEX Forensics - Comprehensive Exit Analysis")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Number of days to analyze")
    parser.add_argument("--summary-only", action="store_true", help="Show only summary, skip trade details")
    parser.add_argument("--filter-exit", help="Filter by exit reason (e.g., trailing_stop, TAKE_PROFIT)")
    parser.add_argument("--top", type=int, help="Show only top N trades by PnL")
    parser.add_argument("--limit", type=int, help="Limit number of trades to analyze")
    parser.add_argument("--toppers", action="store_true", help="Run Top Gainers Retrospective analysis")
    parser.add_argument("--missed-alpha", action="store_true", help="Run Missed Alpha Analysis (rejected signals)")
    parser.add_argument("--all", action="store_true", help="Run all three analysis modes sequentially")
    parser.add_argument("--settle", action="store_true", help="Run full daily settle pipeline and write permanent JSON report")
    parser.add_argument("--purge-cache", action="store_true", help="Delete all cached OHLCV files")
    parser.add_argument("--fetch-only", action="store_true", help="Pre-fetch market data into cache without running analysis")
    
    args = parser.parse_args()

    # --- Utility / settle entry points ---
    if args.purge_cache:
        removed = purge_cache()
        print(f"🧹 Purged {removed} cached OHLCV files from {CACHE_DIR}")
        return

    if args.settle:
        run_settle(args)
        return

    if args.fetch_only:
        run_fetch_only(args)
        return

    # --- Mode dispatch ---
    run_mode1 = args.all or not (args.toppers or args.missed_alpha)
    if args.all or args.toppers:
        run_toppers_analysis(args)
    if args.all or args.missed_alpha:
        run_missed_alpha_analysis(args)
    if run_mode1:
        run_exit_forensics(args)


# ============================================================================
# MODE 1: Exit Forensics
# ============================================================================

def run_exit_forensics(args):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Build query
    query = """
        SELECT * FROM trades
        WHERE status = 'CLOSED'
        AND exit_time IS NOT NULL
    """
    params = []
    
    if args.from_date:
        query += " AND entry_time >= ?"
        params.append(args.from_date)
    elif args.days:
        start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        query += " AND entry_time >= ?"
        params.append(start_date)
    else:
        # Default: last 7 days
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        query += " AND entry_time >= ?"
        params.append(start_date)
    
    if args.to_date:
        query += " AND entry_time <= ?"
        params.append(args.to_date)
    
    if args.filter_exit:
        query += " AND reason = ?"
        params.append(args.filter_exit)
    
    query += " ORDER BY exit_time DESC"
    
    if args.limit:
        query += " LIMIT ?"
        params.append(args.limit)

    cursor.execute(query, params)
    trades = cursor.fetchall()
    conn.close()

    if not trades:
        print("No closed trades found for the specified period.")
        return {"error": "no closed trades"}

    print(f"{'='*120}")
    print(f"  APEX FORENSICS - Comprehensive Exit Analysis ({len(trades)} trades)")
    if args.from_date:
        print(f"  Period: {args.from_date} to {args.to_date or 'now'}")
    elif args.days:
        print(f"  Period: Last {args.days} days")
    if args.filter_exit:
        print(f"  Filter: Exit reason = {args.filter_exit}")
    print(f"{'='*120}")

    # OHLCV cache (v2 watermark store) is on disk and reused automatically
    cached_count = cache_file_count()
    if cached_count > 0:
        print(f"  📦 {cached_count} cached OHLCV files on disk (skipping API calls for covered ranges)")

    # Analyze all trades
    results = []
    failed = 0
    for i, t in enumerate(trades, 1):
        if not args.summary_only and i % 10 == 0:
            print(f"  Analyzing {i}/{len(trades)}...")
        r = audit_trade(dict(t))
        if r and "error" not in r:
            results.append(r)
        else:
            failed += 1
    
    if failed > 0:
        print(f"  ⚠️ {failed} trades failed to analyze (no OHLCV data)")

    if args.top:
        results = sorted(results, key=lambda x: x["exit_pnl"], reverse=True)[:args.top]

    # Print individual trade details
    if not args.summary_only:
        for r in results:
            print(f"\n  {'─'*110}")
            print(f"  {r['sym']:<20} | {r['side']:<4} | Entry: {r['entry']:<12.6f} | Exit: {r['exit']:<12.6f} | PnL: {r['exit_pnl']:+.2f}% | Reason: {r['reason']}")
            print(f"  Candles: {r['candles']} | Real Peak: {r['real_peak']:.6f} | Real Low: {r['real_low']:.6f}")

            if "tp_status" in r:
                tp_emoji = "✅" if "✅" in r["tp_status"] else "ℹ️"
                print(f"  {tp_emoji} TP:  {r['tp_status']}")

            if "sl_status" in r:
                sl_emoji = "✅" if "✅" in r["sl_status"] else "ℹ️"
                print(f"  {sl_emoji} SL:  {r['sl_status']}")

            if "peak_capture" in r:
                cap_emoji = "✅" if r["peak_capture"] >= 95 else "⚠️"
                print(f"  {cap_emoji} Peak Capture: {r['peak_capture']}%")

            print(f"  📊 Ride Quality: {r['ride_quality']:.1f}% (Peak: {r['peak_pnl']:+.2f}%, Banked: {r['exit_pnl']:+.2f}%, Missed: {r['missed_pnl']:+.2f}%)")
            
            if r.get("post_exit_candles", 0) > 0:
                print(f"  🚀 Post-Exit (4h): High {r['post_exit_pnl']:+.2f}% | Continuation: {r['continuation']:+.2f}% | Adverse: {r['adverse_move']:+.2f}%")
                quality_emoji = "✅" if r["exit_quality"] >= 70 else "⚠️" if r["exit_quality"] >= 40 else "❌"
                print(f"  {quality_emoji} Exit Quality: {r['exit_quality']:.1f}% of total move ({r['total_peak_pnl']:+.2f}% available)")
                if r.get("early_exit"):
                    print(f"  ⚠️ EARLY EXIT: Price continued {r['continuation']:+.2f}% after we exited")
                elif r.get("good_exit"):
                    print(f"  ✅ WELL-TIMED EXIT: Price moved {r['adverse_move']:+.2f}% against us after exit")

            be_emoji = "⚠️" if "YES" in r["better_exit"] else "✅"
            print(f"  {be_emoji} Better Exit: {r['better_exit']}")

    # Summary statistics
    print(f"\n{'='*120}")
    print(f"  SUMMARY ({len(results)} trades audited)")
    print(f"{'='*120}")

    # TP/SL stats
    tp_reached = sum(1 for r in results if "tp_status" in r and "REACHED" in r["tp_status"])
    tp_reached_but_missed = sum(1 for r in results if r.get("tp_exit_worse", False))
    sl_reached = sum(1 for r in results if "sl_status" in r and "REACHED" in r["sl_status"])
    good_captures = sum(1 for r in results if r.get("peak_capture", 0) >= 95)
    better_exits = sum(1 for r in results if "YES" in r.get("better_exit", ""))

    print(f"  TP reached:          {tp_reached}/{len(results)}")
    print(f"  TP reached but exit worse: {tp_reached_but_missed}")
    print(f"  SL reached:          {sl_reached}/{len(results)}")
    print(f"  Peak capture >95%:   {good_captures}/{len(results)}")
    print(f"  Better exit existed: {better_exits}/{len(results)} (>2% missed)")

    # Ride quality stats
    ride_qualities = [r["ride_quality"] for r in results if "ride_quality" in r]
    if ride_qualities:
        total_ride = sum(ride_qualities)
        avg_ride = total_ride / len(ride_qualities)
        good_rides = sum(1 for rq in ride_qualities if rq >= 70)
        poor_rides = sum(1 for rq in ride_qualities if rq < 40)
        
        total_missed = sum(r["missed_pnl"] for r in results if r.get("missed_pnl", 0) > 0)
        total_banked = sum(r["exit_pnl"] for r in results)
        total_peak = sum(r["peak_pnl"] for r in results if r.get("peak_pnl", 0) > 0)
        
        money_capture = (total_banked / total_peak * 100) if total_peak > 0 else 0

        print(f"\n  RIDE QUALITY & DOLLAR CAPTURE:")
        print(f"  Avg ride quality:      {avg_ride:.1f}%")
        print(f"  Good rides (>=70%):    {good_rides}/{len(ride_qualities)}")
        print(f"  Poor rides (<40%):     {poor_rides}/{len(ride_qualities)}")
        print(f"  ")
        print(f"  💰 MONEY TERMS:")
        print(f"  Total peak available:  {total_peak:+.2f}%")
        print(f"  Total actually banked: {total_banked:+.2f}%")
        print(f"  Total left on table:   {total_missed:+.2f}%")
        print(f"  Dollar capture rate:   {money_capture:.1f}%")

    # Forward-looking exit analysis
    post_exit_results = [r for r in results if r.get("post_exit_candles", 0) > 0]
    if post_exit_results:
        exit_qualities = [r["exit_quality"] for r in post_exit_results]
        avg_exit_quality = sum(exit_qualities) / len(exit_qualities)
        early_exits = sum(1 for r in post_exit_results if r.get("early_exit"))
        good_exits = sum(1 for r in post_exit_results if r.get("good_exit"))
        total_continuation = sum(r["continuation"] for r in post_exit_results if r["continuation"] > 0)
        total_avoided = sum(r["adverse_move"] for r in post_exit_results if r["adverse_move"] < 0)
        
        print(f"\n  🚀 FORWARD-LOOKING EXIT ANALYSIS (4h post-exit):")
        print(f"  Avg exit quality:      {avg_exit_quality:.1f}%")
        print(f"  Early exits (>2% continuation): {early_exits}/{len(post_exit_results)} ({early_exits/len(post_exit_results)*100:.1f}%)")
        print(f"  Well-timed exits (>2% avoided loss): {good_exits}/{len(post_exit_results)} ({good_exits/len(post_exit_results)*100:.1f}%)")
        print(f"  Total missed continuation: {total_continuation:+.2f}%")
        print(f"  Total avoided loss: {total_avoided:+.2f}%")
        
        # Trailing stop specific analysis
        trail_exits = [r for r in post_exit_results if "TRAILING" in r["reason"] or "trailing" in r["reason"].lower()]
        if trail_exits:
            trail_quality = sum(r["exit_quality"] for r in trail_exits) / len(trail_exits)
            trail_early = sum(1 for r in trail_exits if r.get("early_exit"))
            trail_continuation = sum(r["continuation"] for r in trail_exits if r["continuation"] > 0)
            
            print(f"\n  🎯 TRAILING STOP ANALYSIS:")
            print(f"  Trades: {len(trail_exits)}")
            print(f"  Avg exit quality: {trail_quality:.1f}%")
            print(f"  Early exits: {trail_early}/{len(trail_exits)} ({trail_early/len(trail_exits)*100:.1f}%)")
            print(f"  Total missed: {trail_continuation:+.2f}%")
            
            if trail_quality < 50:
                print(f"  ⚠️ TRAILING STOPS TOO TIGHT - Consider widening trail distance")
            elif trail_early / len(trail_exits) > 0.6:
                print(f"  ⚠️ MOST TRAILING EXITS ARE PREMATURE - Price continues after exit")

    # Group by exit reason
    print(f"\n  EXIT REASON BREAKDOWN:")
    exit_groups = defaultdict(list)
    for r in results:
        exit_groups[r["reason"]].append(r)
    
    for reason, group in sorted(exit_groups.items(), key=lambda x: -len(x[1])):
        count = len(group)
        avg_pnl = sum(r["exit_pnl"] for r in group) / count
        avg_ride = sum(r["ride_quality"] for r in group if "ride_quality" in r) / count if group else 0
        wins = sum(1 for r in group if r["exit_pnl"] > 0)
        print(f"  {reason:<20} {count:3d} trades | Avg PnL: {avg_pnl:+6.2f}% | Win Rate: {wins/count*100:5.1f}% | Avg Ride: {avg_ride:5.1f}%")

    print(f"{'='*120}")

    # Structured output for settle pipeline
    summary = {
        "trades_analyzed": len(results),
        "failed": failed,
        "avg_exit_pnl": round(sum(r["exit_pnl"] for r in results) / len(results), 2) if results else 0,
        "win_rate": round(sum(1 for r in results if r["exit_pnl"] > 0) / len(results) * 100, 1) if results else 0,
        "tp_reached": tp_reached,
        "sl_reached": sl_reached,
        "good_captures": good_captures,
        "better_exits": better_exits,
    }
    return {"summary": summary, "results": results}


if __name__ == "__main__":
    main()