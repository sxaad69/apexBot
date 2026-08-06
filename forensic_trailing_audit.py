#!/usr/bin/env python3
"""
Forensic Trailing-Stop Audit
Replays Binance 1-minute OHLCV for every profitable trade window and verifies
whether the bot's recorded peaks and exits are mathematically consistent with
the real market candles.

For each winning trade:
  1. Confirms the recorded highest_price matches the real peak (high) in-window
  2. Recomputes the expected trailing stop = peak * (1 - TRAIL_DISTANCE/100)
  3. Simulates when a minute-candle LOW would have touched that trailing stop
  4. Compares simulated vs actual exit price and flags discrepancies
"""

import os
import json
import sqlite3
import requests
import time
from datetime import datetime, timedelta, timezone

# === CONFIG (must mirror config/config.py trailing settings) ===
TRAILING_STOP_ACTIVATION = 5.0   # % profit before trailing arms
TRAILING_STOP_DISTANCE = 3.0     # % trail distance from peak
FEE_SAFETY_FLOOR_PCT = 0.12      # stop never below entry + 0.12%

DB_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "apex_hunter.db"
)

BASE_URL = "https://fapi.binance.com/fapi/v1/klines"


def to_interval_millis(iso_str: str) -> int:
    """Convert ISO timestamp string to epoch milliseconds."""
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def symbol_to_binance(symbol: str) -> str:
    """Map ccxt symbol like KOMA/USDT:USDT -> KOMAUSDT"""
    s = symbol.split("/")[0].split(":")[0]
    return s + "USDT"


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch 1m klines between start and end (Binance returns max 1500 per call)."""
    all_klines = []
    cursor = start_ms
    while cursor < end_ms:
        params = {
            "symbol": symbol,
            "interval": "1m",
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        for attempt in range(5):
            try:
                r = requests.get(BASE_URL, params=params, timeout=15)
                data = r.json()
                if isinstance(data, list):
                    if not data:
                        break
                    all_klines.extend(data)
                    # advance cursor past last candle
                    cursor = data[-1][0] + 60_000
                    break
                else:
                    print(f"    API resp not list: {data}")
                    time.sleep(2)
            except Exception as e:
                print(f"    fetch error {e}, retry {attempt + 1}")
                time.sleep(2)
        else:
            break
        time.sleep(0.15)  # rate limit politeness
    return all_klines


def simulate_trailing(klines, entry_price, trail_dist_pct):
    """
    Walk 1m candles, simulate the bot's ratchet.
    Sample = [open_time, open, high, low, close, ...]
    Returns dict with peak, activation_time, trail_stop, simulated_exit_price/time.
    """
    peak = entry_price
    activated = False
    trail_stop = None
    sim_exit_price = None
    sim_exit_time = None

    for k in klines:
        ts, o, h, l, c = k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])
        # update peak (for long: high)
        if h > peak:
            peak = h

        profit_pct = ((peak - entry_price) / entry_price) * 100

        if not activated:
            if profit_pct >= TRAILING_STOP_ACTIVATION:
                activated = True
                # first ratchet: stop = peak * (1 - trail_dist) floored at safe breakeven
                new_stop = peak * (1 - trail_dist_pct / 100.0)
                safe_floor = entry_price * (1 + FEE_SAFETY_FLOOR_PCT / 100.0)
                trail_stop = max(new_stop, safe_floor)
        else:
            # ratchet up: each new peak raises the stop
            new_stop = peak * (1 - trail_dist_pct / 100.0)
            safe_floor = entry_price * (1 + FEE_SAFETY_FLOOR_PCT / 100.0)
            candidate = max(new_stop, safe_floor)
            if candidate > trail_stop:
                trail_stop = candidate

            # check stop hit on this candle's LOW
            if l <= trail_stop:
                sim_exit_price = trail_stop
                sim_exit_time = ts
                break

    return {
        "peak": peak,
        "activated": activated,
        "trail_stop": trail_stop,
        "sim_exit_price": sim_exit_price,
        "sim_exit_time_ms": sim_exit_time,
    }


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT trade_id, symbol, side, entry_price, entry_time, "
        "exit_price, exit_time, pnl_amount, pnl_percent, reason, "
        "highest_price, trailing_stop_price "
        "FROM trades WHERE status='CLOSED' AND pnl_amount > 0 "
        "ORDER BY entry_time"
    ).fetchall()

    print(f"{'=' * 120}")
    print("🔬 FORENSIC TRAILING-STOP AUDIT (1-Min OHLCV Replay)")
    print(f"Config: Activation={TRAILING_STOP_ACTIVATION}% | Trail={TRAILING_STOP_DISTANCE}% | FeeFloor={FEE_SAFETY_FLOOR_PCT}%")
    print(f"Winning trades to audit: {len(rows)}")
    print(f"{'=' * 120}\n")

    for row in rows:
        sym = row["symbol"]
        b_symbol = symbol_to_binance(sym)
        entry = float(row["entry_price"])
        hi = float(row["highest_price"] or entry)
        trail = row["trailing_stop_price"]
        trail = float(trail) if trail else None
        exit_p = float(row["exit_price"])
        start_ms = to_interval_millis(row["entry_time"]) - 60_000  # 1 min buffer
        end_ms = to_interval_millis(row["exit_time"]) + 60_000

        print(f"🔹 {row['trade_id']} | {sym} | {row['reason']}")
        print(f"   Entry={entry} | Exit={exit_p} | P&L=${row['pnl_amount']:.2f} ({row['pnl_percent']:.2f}%)")
        print(f"   DB Recorded Highest={hi} | DB TrailStop={trail}")
        print(f"   Window: {row['entry_time'][:19]} -> {row['exit_time'][:19]}")

        klines = fetch_klines(b_symbol, start_ms, end_ms)
        if not klines:
            print("   ❌ No klines returned — check symbol/network\n")
            continue

        # exact window (trim buffer)
        window_klines = [k for k in klines
                         if to_interval_millis(row["entry_time"]) <= k[0] <= to_interval_millis(row["exit_time"])]

        real_peak = max(float(k[2]) for k in window_klines) if window_klines else hi
        real_low = min(float(k[3]) for k in window_klines) if window_klines else exit_p

        sim = simulate_trailing(window_klines, entry, TRAILING_STOP_DISTANCE)

        # --- Verdict 1: Peak accuracy ---
        peak_dev = (real_peak - hi) / entry * 100
        print(f"   Market Peak (real high in window) = {real_peak:.6f}")
        if abs(real_peak - hi) < 1e-9:
            print("   ✅ Peak tracking: EXACT match with market")
        elif real_peak > hi:
            print(f"   ⚠️ Peak tracking: bot UNDERESTIMATED peak by {peak_dev:.2f}% of entry (profit left on table)")
        else:
            print(f"   ✅ Peak tracking: ok (bot peak slightly above intra-minute)")

        # --- Verdict 2: Trailing stop math ---
        if trail is not None:
            expected_trail = sim["trail_stop"]
            if expected_trail is not None and abs(trail - expected_trail) < 1e-6:
                print(f"   ✅ Trailing stop: EXACT (recorded {trail:.6f} == simulated {expected_trail:.6f})")
            else:
                print(f"   ⚠️ Trailing stop: recorded={trail}, simulated={expected_trail}")

        # --- Verdict 3: Exit accuracy ---
        if sim["sim_exit_price"] is not None:
            expected_exit = sim["sim_exit_price"]
            dev = (exit_p - expected_exit) / entry * 100
            if abs(exit_p - expected_exit) < 1e-6:
                print(f"   ✅ Exit price: EXACT match with simulated trailing stop ({expected_exit:.6f})")
            else:
                print(f"   ⚠️ Exit price deviates from simulated stop by {dev:+.2f}% of entry (sim={expected_exit:.6f})")
        else:
            print("   ℹ️ No simulated stop trigger in window (peaked below 5% activation or exit before stop)")

        # --- Verdict 4: Did exit happen after real LOW touched the stop? ---
        if trail is not None and real_low <= trail:
            print(f"   ✅ Exit justified: window LOW ({real_low:.6f}) touched the trailing stop ({trail:.6f})")
        print("   " + "-" * 60 + "\n")

    conn.close()
    print("🏁 AUDIT COMPLETE")


if __name__ == "__main__":
    main()