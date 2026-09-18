#!/usr/bin/env python3
"""
MOMENTUM AUDIT — Momentum-Gated Entry Ledger (A6)
==================================================
Audits every A6 entry that passed via the momentum override
(bar-move surge >= delta_price_momentum_gate with imbalance < wall threshold).
Builds the per-trade signal DNA table and slices P&L by bar%, RSI, ADX,
imbalance, volume-ratio, session, regime.

P&L source is the EXCHANGE (Binance income API), not the bot's DB pnl_percent:
realized income is matched to each trade by [entry_time-10s, exit_time+10s],
plus phones unrealized for still-open positions from live marks. Commissions
are netted separately so the real cost picture is visible.

The signal DNA that the entry gate saw lives in the trades metadata JSON.
Momentum params (delta_price_momentum_gate, momentum_min_rsi, momentum_min_adx,
momentum_sl_percent, momentum_max_roe) and indicators (rsi_14, volume_ratio,
adx) are persisted from commit that enabled the audit (f2e1c1a); older rows
may show '?' where they were not captured yet.

Usage (run from the bot venv with live .env loaded):
  python audit/momentum_audit.py                 # all momentum trades (default: since --days)
  python audit/momentum_audit.py --days 7
  python audit/momentum_audit.py --from 2026-09-17 --to 2026-09-18
  python audit/momentum_audit.py --bar-split     # bar%<9 vs bar%>=9 P&L split (default)
  python audit/momentum_audit.py --no-split
  python audit/momentum_audit.py --symbol SOON

INITIAL FINDING (Sep 18 2026, 21 trades): bar% < 9% at entry = +$7.46 net
vs bar% >= 9% = -$2.85 net. All trades over -$0.20 loss had bar% >= 9%.
Needs more data before the gate is changed — do NOT treat as a rule yet.
"""

import argparse
import collections
import datetime
import json
import os
import sqlite3

try:
    import ccxt
except ImportError:
    ccxt = None
try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

DB_PATH = os.environ.get("APEX_DB_PATH", "data/apex_hunter.db")
INCOME_SINCE = "2026-09-17T00:00:00Z"


def load_exchange():
    if load_dotenv:
        load_dotenv()
    if ccxt is None:
        return None
    key = os.environ.get("BINANCE_API_KEY")
    secret = os.environ.get("BINANCE_API_SECRET")
    if not key or not secret:
        return None
    return ccxt.binance({
        'apiKey': key,
        'secret': secret,
        'options': {'defaultType': 'future'},
        'enableRateLimit': True,
    })


def fetch_momentum_trades(from_dt=None, to_dt=None, symbol=None):
    db = sqlite3.connect(DB_PATH)
    q = ("SELECT trade_id, datetime(entry_time), datetime(exit_time), symbol, entry_price, "
         "exit_price, leverage, size, pnl_percent, status, reason, metadata "
         "FROM trades WHERE json_extract(metadata, '$.momentum_gated')=1 ")
    conds = []
    if from_dt:
        conds.append("entry_time >= datetime(?)")
    if to_dt:
        conds.append("entry_time <= datetime(?) ")
    if symbol:
        conds.append("symbol LIKE ?")
    if conds:
        q += "AND " + " AND ".join(conds)
    q += " ORDER BY entry_time"
    params = []
    if from_dt:
        params.append(from_dt)
    if to_dt:
        params.append(to_dt)
    if symbol:
        params.append(symbol + "%")
    return db.execute(q, params).fetchall()


def fetch_income(ex):
    inc = {}
    if ex is None:
        return inc
    try:
        rows = ex.fapiPrivateGetIncome({'startTime': int(ex.parse8601(INCOME_SINCE)), 'limit': 1000})
    except Exception:
        return inc
    for i in rows:
        base = (i.get('symbol') or '').replace('USDT', '')
        inc.setdefault(base, []).append(i)
    return inc


def fetch_marks(ex, bases):
    marks = {}
    if ex is None or not bases:
        return marks
    syms = [b + '/USDT:USDT' for b in bases]
    try:
        for k, t in ex.fetch_tickers(syms).items():
            marks[k] = t['last']
    except Exception:
        pass
    return marks


def trade_pnl(base, et, xt, ep, lev, size, status, ex_inc, marks):
    et_ms = int(datetime.datetime.strptime(et, '%Y-%m-%d %H:%M:%S')
                .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
    xt_ms = int(datetime.datetime.strptime(xt, '%Y-%m-%d %H:%M:%S')
                .replace(tzinfo=datetime.timezone.utc).timestamp() * 1000) if xt else 2 ** 53
    pnl = 0.0
    comm = 0.0
    for i in ex_inc.get(base, []):
        t = i['time']
        if et_ms - 10000 <= t <= xt_ms + 10000:
            if i['incomeType'] == 'REALIZED_PNL':
                pnl += float(i['income'])
            elif i['incomeType'] == 'COMMISSION':
                comm += float(i['income'])
    if status == 'OPEN':
        mark = marks.get(base + '/USDT:USDT')
        if mark:
            pnl += float(size or 0) * lev * (mark - ep) / ep
            pnl_note = 'OPEN'
        else:
            pnl_note = 'OPEN*'
    else:
        pnl_note = ''
    return pnl, comm, pnl_note


def bar_split(trades, ex_inc, marks):
    low, high = [], []
    for tid, et, xt, sym, ep, xp, lev, size, dbp, st, reason, md in trades:
        m = json.loads(md) if md else {}
        bar = m.get('bar_move_pct')
        pnl, _, _ = trade_pnl(sym.split('/')[0], et, xt, ep, lev, size, st, ex_inc, marks)
        if isinstance(bar, float) and bar < 1:
            pct = bar * 100
        elif isinstance(bar, (int, float)):
            pct = bar
        else:
            pct = None
        if pct is None:
            continue
        (low if pct < 9 else high).append(pnl)
    return low, high


def main():
    ap = argparse.ArgumentParser(description='Momentum-gated entry audit (A6).')
    ap.add_argument('--days', type=int, default=7, help='audit window: last N days')
    ap.add_argument('--from', dest='from_dt', help='window start YYYY-MM-DD (overrides --days)')
    ap.add_argument('--to', dest='to_dt', help='window end YYYY-MM-DD')
    ap.add_argument('--symbol', help='filter by base symbol (e.g. SOON)')
    ap.add_argument('--no-split', action='store_true', help='skip bar% split summary')
    args = ap.parse_args()

    if args.from_dt:
        from_dt = args.from_dt
    else:
        from_dt = (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    to_dt = args.to_dt or '2099-12-31'

    trades = fetch_momentum_trades(from_dt, to_dt, args.symbol)
    if not trades:
        print(f"No momentum-gated trades in window {from_dt}..{to_dt}")
        return

    ex = load_exchange()
    ex_inc = fetch_income(ex)
    open_bases = [r[3].split('/')[0] for r in trades if r[9] == 'OPEN']
    marks = fetch_marks(ex, open_bases)

    print('%-9s %-8s %-6s %-6s %-7s %-7s %-7s %-8s %-9s %-6s %10s %7s | %s' % (
        'SYMBOL', 'REGIME', 'ADX', 'RSI', 'IMB%', 'bar%', 'VOLr', 'SESSION', 'TIER', 'CONF', 'P&L$', 'COMM$', 'EXIT'))
    tot = 0.0
    comm_tot = 0.0
    for tid, et, xt, sym, ep, xp, lev, size, dbp, st, reason, md in trades:
        base = sym.split('/')[0]
        m = json.loads(md) if md else {}
        ind = m.get('indicators', {}) or {}
        imb = ind.get('imbalance', 0)
        imb_s = '%+.1f%%' % (imb * 100 if abs(imb) <= 1 else imb)
        bar = m.get('bar_move_pct', 0)
        bar_s = '%.1f%%' % (bar * 100 if abs(bar) <= 1 else bar)
        adx = ind.get('adx', '?')
        adx_s = ('%.0f' % adx) if isinstance(adx, (int, float)) else '?'
        rsi = ind.get('rsi_14', m.get('rsi', '?'))
        rsi_s = ('%.0f' % rsi) if isinstance(rsi, (int, float)) else '?'
        vr = ind.get('volume_ratio', '?')
        vr_s = ('%.1f' % vr) if isinstance(vr, (int, float)) else '?'
        conf = m.get('confidence', '?')
        conf_s = ('%.2f' % conf) if isinstance(conf, (int, float)) else '?'
        regime = m.get('regime', '?')
        session = m.get('session', '?')
        tier = m.get('tier', '?')
        pnl, comm, note = trade_pnl(base, et, xt, ep, lev, size, st, ex_inc, marks)
        tot += pnl
        comm_tot += comm
        print('%-9s %-8s %-6s %-6s %-7s %-7s %-7s %-8s %-9s %-6s %10.4f %7.4f | %s%s' % (
            base, regime, adx_s, rsi_s, imb_s, bar_s, vr_s, session, tier, conf_s,
            pnl, comm, reason or 'OPEN', (' (u)' if note == 'OPEN' else '')))
    print('%-9s %-8s %-6s %-6s %-7s %-7s %-7s %-8s %-9s %-6s %10.4f %7.4f |' % (
        'NET ', '', '', '', '', '', '', '', '', '', tot, comm_tot))
    print('NET P&L (realized + unrealized for OPEN) = %+.4f USD over %d momentum trades '
          'in %s..%s' % (tot + comm_tot, len(trades), from_dt, args.to_dt or 'now'))

    if not args.no_split:
        low, high = bar_split(trades, ex_inc, marks)
        print('\nbar%%<9 : %d trades sum %+7.4f USD' % (len(low), sum(low)))
        print('bar%%>=9: %d trades sum %+7.4f USD' % (len(high), sum(high)))


if __name__ == '__main__':
    main()