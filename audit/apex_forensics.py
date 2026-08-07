#!/usr/bin/env python3
"""
APEX FORENSICS - Comprehensive Exit Analysis
=============================================
Combines TP/SL validation, ride quality, and trailing stop analysis
into a single forensic tool.

For each closed trade:
  1. Fetches 1m OHLCV from entry to exit
  2. Validates TP/SL touches vs actual exit reason
  3. Measures ride quality (how much of the move was banked in $ terms)
  4. Analyzes trailing stop effectiveness
  5. Groups results by exit type with actionable recommendations

Usage:
  python apex_forensics.py --from 2026-08-06 --to 2026-08-07
  python apex_forensics.py --days 7
  python apex_forensics.py --summary-only
  python apex_forensics.py --filter-exit trailing_stop
  python apex_forensics.py --top 10
"""

import sqlite3
import requests
import time
import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from collections import defaultdict

DB_PATH = "data/apex_hunter.db"
CACHE_DIR = "data/forensics_cache"
BINANCE = "https://fapi.binance.com/fapi/v1/klines"

# In-memory cache loaded from disk
_cache = {}

def _load_cache():
    global _cache
    if os.path.exists(CACHE_DIR):
        for f in os.listdir(CACHE_DIR):
            if f.endswith(".json"):
                key = f[:-5]
                try:
                    with open(os.path.join(CACHE_DIR, f)) as fh:
                        _cache[key] = json.load(fh)
                except:
                    pass

def _save_cache(key, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(os.path.join(CACHE_DIR, f"{key}.json"), "w") as f:
        json.dump(data, f)

def to_binance(db_sym):
    return db_sym.replace("/USDT:USDT", "USDT")

def fetch_ohlcv(symbol, start_ms, end_ms, interval="1m", use_cache=True):
    """Fetch OHLCV candles with disk caching and configurable interval"""
    cache_key = f"{symbol}_{interval}_{start_ms}_{end_ms}"
    if use_cache and cache_key in _cache:
        return _cache[cache_key]

    interval_ms = {"1m": 60000, "5m": 300000, "15m": 900000}
    step = interval_ms.get(interval, 60000)

    klines = []
    cursor = start_ms
    while cursor < end_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": cursor, "endTime": end_ms, "limit": 1000}
        for attempt in range(3):
            try:
                r = requests.get(BINANCE, params=params, timeout=10)
                data = r.json()
                if isinstance(data, list):
                    if not data:
                        if use_cache:
                            _cache[cache_key] = klines
                            _save_cache(cache_key, klines)
                        return klines
                    klines.extend(data)
                    cursor = data[-1][0] + step
                    break
            except:
                time.sleep(1)
        else:
            break
        time.sleep(0.12)

    if use_cache:
        _cache[cache_key] = klines
        _save_cache(cache_key, klines)
    return klines


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


def main():
    parser = argparse.ArgumentParser(description="APEX Forensics - Comprehensive Exit Analysis")
    parser.add_argument("--from", dest="from_date", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="to_date", help="End date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, help="Number of days to analyze")
    parser.add_argument("--summary-only", action="store_true", help="Show only summary, skip trade details")
    parser.add_argument("--filter-exit", help="Filter by exit reason (e.g., trailing_stop, TAKE_PROFIT)")
    parser.add_argument("--top", type=int, help="Show only top N trades by PnL")
    parser.add_argument("--limit", type=int, help="Limit number of trades to analyze")
    
    args = parser.parse_args()

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
        return

    print(f"{'='*120}")
    print(f"  APEX FORENSICS - Comprehensive Exit Analysis ({len(trades)} trades)")
    if args.from_date:
        print(f"  Period: {args.from_date} to {args.to_date or 'now'}")
    elif args.days:
        print(f"  Period: Last {args.days} days")
    if args.filter_exit:
        print(f"  Filter: Exit reason = {args.filter_exit}")
    print(f"{'='*120}")

    # Load OHLCV cache from disk
    _load_cache()
    cached_count = len(_cache)
    if cached_count > 0:
        print(f"  📦 Loaded {cached_count} cached OHLCV files (skipping API calls for cached data)")

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


if __name__ == "__main__":
    main()