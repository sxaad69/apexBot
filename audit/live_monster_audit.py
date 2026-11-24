import os, sys, time, json, sqlite3, ccxt
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv('/home/ubuntu/apexBot/.env')

print("=" * 80)
print(f"  ⚡ APEX HUNTER V14 — LIVE MONSTER AUDIT | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
print("=" * 80)

# 1. Systemd Status
print("\n[1. MACHINE & SERVICE STATUS]")
os.system("systemctl is-active apex-bot")

# Initialize CCXT via local SOCKS proxy
ex = ccxt.binanceusdm({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_API_SECRET'),
    'proxies': {'http': 'http://127.0.0.1:8888', 'https': 'http://127.0.0.1:8888'},
    'options': {'defaultType': 'future'}
})

# 2. Binance Account State & Net Cash Flow
print("\n[2. EXCHANGE CASH FLOW & EQUITY GROUND TRUTH]")
acc = ex.fapiPrivateV3GetAccount()
total_wallet = float(acc.get("totalWalletBalance", 0))
total_margin = float(acc.get("totalMarginBalance", 0))
total_upnl = float(acc.get("totalUnrealizedProfit", 0))

# Fetch all income records from starting session baseline
incomes = []
start_time = 1795392000000 # Nov 23 00:00 UTC approx
now_ms = int(time.time() * 1000)
curr_start = start_time

while curr_start < now_ms:
    try:
        batch = ex.fapiPrivateGetIncome({"startTime": curr_start, "limit": 1000})
        if not batch: break
        incomes.extend(batch)
        curr_start = int(batch[-1]["time"]) + 1
        if len(batch) < 1000: break
    except Exception as e:
        print(f"Income fetch warning: {e}")
        break

realized_pnl = sum(float(i["income"]) for i in incomes if i["incomeType"] == "REALIZED_PNL")
commissions = sum(float(i["income"]) for i in incomes if i["incomeType"] == "COMMISSION")
funding_fees = sum(float(i["income"]) for i in incomes if i["incomeType"] == "FUNDING_FEE")
net_exchange_cash = realized_pnl + commissions + funding_fees

print(f"  Wallet Balance:        ${total_wallet:.4f} USDT")
print(f"  Margin Equity:         ${total_margin:.4f} USDT (Unrealized: {total_upnl:+.4f} USDT)")
print(f"  Realized Trading PnL:  {realized_pnl:+.4f} USDT")
print(f"  Commissions (Taker):   {commissions:+.4f} USDT")
print(f"  Funding Fees:          {funding_fees:+.4f} USDT")
print(f"  NET REALIZED CASH:     {net_exchange_cash:+.4f} USDT")
fee_drag = abs(commissions) / realized_pnl * 100 if realized_pnl > 0 else 0.0
print(f"  Fee Drag on Gains:     {fee_drag:.1f}%")

# 3. Active Positions Quality & Liquidity Audit
print("\n[3. ACTIVE POSITIONS QUALITY & VOLUME HEALTH]")
positions = [p for p in acc.get("positions", []) if abs(float(p.get("positionAmt", 0))) > 0]
tickers = {t['symbol']: t for t in ex.fapiPublicGetTicker24hr()}

if not positions:
    print("  No active open positions (0/3 slots used).")
else:
    for p in positions:
        sym = p.get("symbol")
        amt = float(p.get("positionAmt", 0))
        entry = float(p.get("entryPrice", 0) or 0)
        upnl = float(p.get("unrealizedProfit", 0))
        notional = float(p.get("notional", 0))
        tick = tickers.get(sym, {})
        vol_24h = float(tick.get("quoteVolume", 0))
        high_24h = float(tick.get("highPrice", 0))
        last_px = float(tick.get("lastPrice", 0))
        dist_high = ((high_24h - last_px) / high_24h * 100) if high_24h > 0 else 999.0
        
        # Microcap check
        quality_flag = "✅ INSTITUTIONAL LEADER" if vol_24h >= 25_000_000 else "🚨 MICROCAP LEAK (< $25M)!"
        print(f"  {sym:<12}: Notional=${notional:.2f} | uPnL={upnl:+.4f} USDT | 24h Vol=${vol_24h/1e6:5.1f}M | Dist24hHigh={dist_high:4.1f}% | {quality_flag}")

# 4. Resting Exchange Algo Orders Defense
print("\n[4. RESTING EXCHANGE ALGO ORDERS (3-BRACKET DEFENSE)]")
algo_orders = ex.fapiPrivateGetOpenAlgoOrders() if hasattr(ex, "fapiPrivateGetOpenAlgoOrders") else []
print(f"  Total Resting Algo Orders: {len(algo_orders)}")
for o in algo_orders:
    print(f"  - {o.get('symbol'):<12} {o.get('orderType'):<22} side={o.get('side'):<4} triggerPx={o.get('triggerPrice')} callback={o.get('callbackRate')}")

# 5. Win / Loss Asymmetry & DB Reconciliation
print("\n[5. WIN/LOSS ASYMMETRY & DB HEALTH]")
conn = sqlite3.connect('/home/ubuntu/apexBot/data/apex_hunter.db')
c = conn.cursor()
c.execute("SELECT COUNT(*), AVG(pnl_amount), SUM(pnl_amount) FROM trades WHERE status != 'OPEN' AND pnl_amount > 0")
w_cnt, w_avg, w_sum = c.fetchone()
c.execute("SELECT COUNT(*), AVG(pnl_amount), SUM(pnl_amount) FROM trades WHERE status != 'OPEN' AND pnl_amount <= 0")
l_cnt, l_avg, l_sum = c.fetchone()

w_avg = w_avg or 0.0
l_avg = l_avg or 0.0
w_cnt = w_cnt or 0
l_cnt = l_cnt or 0
asymmetry = abs(w_avg / l_avg) if l_avg != 0 else 999.0

print(f"  Winners: {w_cnt} trades | Avg Win: +${w_avg:.2f} USDT | Total: +${w_sum or 0:.2f} USDT")
print(f"  Losers:  {l_cnt} trades | Avg Loss: -${abs(l_avg):.2f} USDT | Total: -${abs(l_sum or 0):.2f} USDT")
print(f"  Win/Loss Asymmetry Ratio (Avg Win / Avg Loss): {asymmetry:.2f}x")
if asymmetry < 1.0:
    print("  ⚠️ ALERT: Average Loss > Average Win. Breakeven & fast-cut engine active to reverse this.")
else:
    print("  ✅ HEALTHY: Average Win >= Average Loss.")

# 6. Recent Closed Trades (Last 30 Minutes)
thirty_min_ago = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
c.execute('SELECT trade_id, symbol, strategy, entry_price, exit_price, pnl_amount, pnl_percent, reason, exit_time FROM trades WHERE exit_time > ? ORDER BY exit_time DESC', (thirty_min_ago,))
recent_trades = c.fetchall()
print(f"\n[6. TRADES CLOSED IN LAST 30 MINUTES: {len(recent_trades)}]")
for t in recent_trades:
    print(f"  {t[1]:<12} ({t[2][:10]}): in={t[3]}, out={t[4]}, pnl={t[5]:+.4f} USDT ({t[6]:+.2f}%), reason={t[7]}")

# 7. Top 5 Real Breakout Leaders (> $25M volume)
print("\n[7. TOP BREAKOUT OPPORTUNITY RECALL (> $25M VOL)]")
valid_tickers = []
for sym, t in tickers.items():
    vol = float(t.get('quoteVolume', 0))
    chg = float(t.get('priceChangePercent', 0))
    if vol >= 25_000_000 and sym.endswith('USDT'):
        high = float(t.get('highPrice', 0))
        last = float(t.get('lastPrice', 0))
        dist_high = ((high - last) / high * 100) if high > 0 else 999.0
        valid_tickers.append((sym, chg, vol, last, dist_high))

valid_tickers.sort(key=lambda x: x[1], reverse=True)
for s, ch, v, lp, dh in valid_tickers[:5]:
    print(f"  {s:<12}: 24h={ch:+6.2f}% | Vol=${v/1e6:5.1f}M | Price={lp:<10} | Dist24hHigh={dh:4.1f}%")

print("=" * 80)
