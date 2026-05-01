import sqlite3
import ccxt
from datetime import datetime, timedelta

DB_PATH = '/home/ubuntu/apexBot/data/apex_hunter.db'

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# ─── SECTION 1: TOTAL SUMMARY ───────────────────────────────────────────────
c.execute('SELECT pnl_amount, pnl_percent, reason FROM trades WHERE status="CLOSED"')
all_closed = c.fetchall()

total = len(all_closed)
if total == 0:
    print("No closed trades found.")
    exit()

winners = [t for t in all_closed if (t['pnl_amount'] or 0) > 0]
losers  = [t for t in all_closed if (t['pnl_amount'] or 0) <= 0]
total_pnl = sum((t['pnl_amount'] or 0) for t in all_closed)
avg_win   = sum((t['pnl_amount'] or 0) for t in winners) / len(winners) if winners else 0
avg_loss  = sum((t['pnl_amount'] or 0) for t in losers)  / len(losers)  if losers  else 0

reasons = {}
for t in all_closed:
    r = t['reason'] or 'unknown'
    reasons[r] = reasons.get(r, 0) + 1

print("=" * 60)
print("       APEXBOT — ALL-TIME PERFORMANCE SUMMARY")
print("=" * 60)
print(f"  Total Closed Trades : {total}")
print(f"  Winners             : {len(winners)}  ({len(winners)/total*100:.1f}%)")
print(f"  Losers              : {len(losers)}  ({len(losers)/total*100:.1f}%)")
print(f"  Total Net PnL       : ${total_pnl:.2f}")
print(f"  Avg Win             : ${avg_win:.2f}")
print(f"  Avg Loss            : ${avg_loss:.2f}")
ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 0
print(f"  Reward:Risk Ratio   : {ratio:.2f}x")
print()
print("  EXIT REASON BREAKDOWN:")
for reason, count in sorted(reasons.items(), key=lambda x: x[1], reverse=True):
    pnl_for_reason = sum((t['pnl_amount'] or 0) for t in all_closed if (t['reason'] or 'unknown') == reason)
    print(f"    {reason:<20} {count:>4} trades  |  PnL: ${pnl_for_reason:.2f}")
print("=" * 60)

# ─── SECTION 2: MATHEMATICAL VERIFICATION ───────────────────────────────────
print()
print("=" * 60)
print("   MATHEMATICAL VERIFICATION (Last 10 Closed Trades)")
print("=" * 60)
print(f"  {'SYMBOL':<22} {'SIDE':<5} {'REASON':<14} {'ENTRY':>8} {'EXIT':>8} {'LEV':>4} {'EXPECTED $':>10} {'ACTUAL $':>10} {'OK?':>5}")
print("-" * 105)

c.execute('''
    SELECT symbol, side, entry_price, exit_price, leverage, size, pnl_amount, pnl_percent, reason
    FROM trades
    WHERE status="CLOSED" AND entry_price IS NOT NULL AND exit_price IS NOT NULL
    ORDER BY exit_time DESC
    LIMIT 10
''')
sample = c.fetchall()

all_match = True
for t in sample:
    entry   = t['entry_price']
    exit_p  = t['exit_price']
    lev     = t['leverage'] or 1
    margin  = t['size'] or 30.0
    side    = t['side']
    reason  = t['reason'] or '?'
    actual  = t['pnl_amount'] or 0

    if side == 'buy':
        price_move_pct = (exit_p - entry) / entry
    else:
        price_move_pct = (entry - exit_p) / entry

    expected_pnl = margin * price_move_pct * lev
    delta = abs(expected_pnl - actual)
    ok = "✅" if delta < 0.10 else "❌"
    if delta >= 0.10:
        all_match = False

    sym = t['symbol'].replace('/USDT:USDT', '').replace('/USDT', '')
    print(f"  {sym:<22} {side:<5} {reason:<14} {entry:>8.5f} {exit_p:>8.5f} {lev:>4}x {expected_pnl:>10.2f} {actual:>10.2f} {ok:>5}")

print("-" * 105)
if all_match:
    print("  ✅ All PnL calculations match the mathematical formula within $0.10 tolerance.")
else:
    print("  ⚠️  Some trades have a variance > $0.10 — may be due to slippage or fee rounding.")
print("=" * 60)

# ─── SECTION 3: SL vs TP PRICE ACCURACY ─────────────────────────────────────
print()
print("=" * 60)
print("   SL / TP PRICE VERIFICATION")
print("   (Did exit_price respect the direction of the SL/TP?)")
print("=" * 60)
print(f"  {'SYMBOL':<22} {'SIDE':<5} {'REASON':<14} {'SL TARGET':>10} {'TP TARGET':>10} {'EXIT PRICE':>11} {'VALID?':>7}")
print("-" * 90)

c.execute('''
    SELECT symbol, side, entry_price, exit_price, stop_loss, take_profit, reason
    FROM trades
    WHERE status="CLOSED" AND entry_price IS NOT NULL AND exit_price IS NOT NULL
    ORDER BY exit_time DESC
    LIMIT 10
''')
sl_tp_trades = c.fetchall()

for t in sl_tp_trades:
    side    = t['side']
    reason  = t['reason'] or '?'
    exit_p  = t['exit_price']
    sl      = t['stop_loss']
    tp      = t['take_profit']
    valid   = "?"

    if reason in ('stop_loss', 'trailing_stop'):
        if side == 'buy':
            # For a long: exit should be AT or BELOW the stop loss
            valid = "✅" if (sl is None or exit_p <= sl * 1.01) else "❌"
        else:
            # For a short: exit should be AT or ABOVE the stop loss
            valid = "✅" if (sl is None or exit_p >= sl * 0.99) else "❌"
    elif reason == 'take_profit':
        if side == 'buy':
            valid = "✅" if (tp is None or exit_p >= tp * 0.99) else "❌"
        else:
            valid = "✅" if (tp is None or exit_p <= tp * 1.01) else "❌"
    else:
        valid = "N/A"

    sym = t['symbol'].replace('/USDT:USDT', '').replace('/USDT', '')
    sl_str = f"{sl:.5f}" if sl else "N/A"
    tp_str = f"{tp:.5f}" if tp else "N/A"
    print(f"  {sym:<22} {side:<5} {reason:<14} {sl_str:>10} {tp_str:>10} {exit_p:>11.5f} {valid:>7}")

print("=" * 60)
conn.close()
