#!/usr/bin/env python3
"""
A6 ENTRY AUDIT — Momentum-Gated vs Wall-Based Entry Ledger
===========================================================
Audits every A6 entry (momentum-gated OR wall-based) with the same
per-trade signal-DNA columns and exchange-truth P&L:

  TYPE     MOM = momentum-gated (bar-move surge w/o wall) | WALL = buy wall >= 0.65
  REGIME   market regime at entry (strong_trend / moderate_trend / ...)
  ADX      trend strength at entry (Phase 27 gate: >=25 long)
  RSI      RSI(14) at entry (momentum gate: >=70)
  IMB%     orderbook imbalance at entry (wall confirmation / momentum override)
  bar%     intrabar move of the current 15m candle at entry (momentum override >= 2%)
  VOLr     volume ratio vs 20-bar average (conf bonus if >1.2)
  SESSION  asia / europe / us_peak / us_late
  TIER     BASE / HOT / BASE_MOMENTUM / HOT_MOMENTUM (HOT = confidence >= 0.90)
  CONF     confidence score at entry
  P&L$     exchange income matched to the trade window (realized), + live mark
           uPnL for OPEN positions — NOT the bot's DB pnl_percent
  COMM$    Binance commissions in the same window

P&L is built from the Binance income API (paginated since the audit start),
matching REALIZED_PNL/COMMISSION rows to each trade by [entry_time-10s,
exit_time+10s]. OPEN rows add live unrealized from the current mark price.
Older rows may show '?' for RSI/VOLr where the signal DNA was not persisted
yet (full persistence landed in 95bb937; momentum rows since Sep 17 22:56 UTC).

Usage (run from the bot venv with live .env loaded):
  python audit/momentum_audit.py                     # momentum entries only (default)
  python audit/momentum_audit.py --type all          # every A6 entry
  python audit/momentum_audit.py --type wall         # wall-based entries only
  python audit/momentum_audit.py --days 14           # last 14 days
  python audit/momentum_audit.py --from 2026-08-01 --to 2026-09-18
  python audit/momentum_audit.py --symbol SOON
  python audit/momentum_audit.py --no-split          # skip bar% split block

INITIAL FINDING (Sep 18 2026, momentum entries only): bar% < 9% at entry =
+$8.10 net (16 trades) vs bar% >= 9% = -$2.70 net (5 trades). NOT a rule yet —
needs more data before any A6_MOMENTUM_BAR_MAX_PCT gate. The --bar-split block
is printed for the momentum subset by design (bar% is meaningless for walls).
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
INCOME_START = "2026-07-01T00:00:00Z"   # cover all-time A6 entries


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


def fetch_trades(trade_type, from_dt=None, to_dt=None, symbol=None):
    db = sqlite3.connect(DB_PATH)
    q = ("SELECT trade_id, datetime(entry_time), datetime(exit_time), symbol, entry_price, "
         "exit_price, leverage, size, pnl_percent, status, reason, metadata, confidence "
         "FROM trades WHERE strategy = 'A6: Orderbook WSS' ")
    conds = []
    if trade_type == 'momentum':
        conds.append("json_extract(metadata, '$.momentum_gated')=1")
    elif trade_type == 'wall':
        conds.append("(json_extract(metadata, '$.momentum_gated') != 1 OR metadata IS NULL)")
    if from_dt:
        conds.append("entry_time >= datetime(?)")
    if to_dt:
        conds.append("entry_time <= datetime(?)")
    if symbol:
        conds.append("symbol LIKE ?")
    if conds:
        q += "AND " + " AND ".join(conds)
    q += " ORDER BY entry_time"
    params = [p for p in (from_dt, to_dt, symbol + '%' if symbol else None) if p is not None]
    return db.execute(q, params).fetchall()


def fetch_income(ex):
    """Paginated Binance income since INCOME_START, keyed by base symbol."""
    out = collections.defaultdict(list)
    if ex is None:
        return out
    start_ms = int(ex.parse8601(INCOME_START))
    while True:
        try:
            rows = ex.fapiPrivateGetIncome({'startTime': start_ms, 'limit': 1000})
        except Exception:
            break
        if not rows:
            break
        for i in rows:
            base = (i.get('symbol') or '').replace('USDT', '')
            out[base].append(i)
        last_ms = max(i['time'] for i in rows)
        if len(rows) < 1000 or last_ms <= start_ms:
            break
        start_ms = last_ms + 1
    return out


def fetch_marks(ex, bases):
    marks = {}
    if ex is None or not bases:
        return marks
    try:
        for k, t in ex.fetch_tickers([b + '/USDT:USDT' for b in bases]).items():
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
    is_open = status == 'OPEN'
    if is_open:
        mark = marks.get(base + '/USDT:USDT')
        if mark:
            pnl += float(size or 0) * lev * (mark - ep) / ep
    return pnl, comm, is_open and bool(marks.get(base + '/USDT:USDT'))


def bar_split(trades, ex_inc, marks):
    low, high, missing = [], [], 0
    for tid, et, xt, sym, ep, xp, lev, size, dbp, st, reason, md, confcol in trades:
        m = json.loads(md) if md else {}
        bar = m.get('bar_move_pct')
        pnl, _, _ = trade_pnl(sym.split('/')[0], et, xt, ep, lev, size, st, ex_inc, marks)
        if isinstance(bar, float) and bar < 1:
            pct = bar * 100
        elif isinstance(bar, (int, float)):
            pct = bar
        else:
            missing += 1
            continue
        (low if pct < 9 else high).append(pnl)
    return low, high, missing


def main():
    ap = argparse.ArgumentParser(description='A6 entry audit (momentum vs wall).')
    ap.add_argument('--type', choices=['momentum', 'wall', 'all'], default='momentum')
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--from', dest='from_dt', help='window start YYYY-MM-DD (overrides --days)')
    ap.add_argument('--to', dest='to_dt', help='window end YYYY-MM-DD')
    ap.add_argument('--symbol', help='filter by base symbol (e.g. SOON)')
    ap.add_argument('--no-split', action='store_true')
    args = ap.parse_args()

    from_dt = args.from_dt or (datetime.date.today() - datetime.timedelta(days=args.days)).isoformat()
    to_dt = args.to_dt or '2099-12-31'

    trades = fetch_trades(args.type, from_dt, to_dt, args.symbol)
    if not trades:
        print("No %s A6 entries in %s..%s" % (args.type, from_dt, args.to_dt or 'now'))
        return

    ex = load_exchange()
    ex_inc = fetch_income(ex)
    open_bases = [r[3].split('/')[0] for r in trades if r[9] == 'OPEN']
    marks = fetch_marks(ex, open_bases)

    print('%-5s %-9s %-13s %-8s %-5s %-5s %-7s %-6s %-5s %-8s %-12s %-5s %10s %7s | %s' % (
        'TYP', 'SYMBOL', 'ENTERED', 'REGIME', 'ADX', 'RSI', 'IMB%', 'bar%', 'VOLr', 'SESSION', 'TIER', 'CONF', 'P&L$', 'COMM$', 'EXIT'))
    tot = 0.0
    comm_tot = 0.0
    for tid, et, xt, sym, ep, xp, lev, size, dbp, st, reason, md, confcol in trades:
        base = sym.split('/')[0]
        m = json.loads(md) if md else {}
        ind = m.get('indicators', {}) or {}
        is_mom = bool(m.get('momentum_gated'))
        typ = 'MOM' if is_mom else 'WALL'
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
        conf = confcol if confcol is not None else m.get('confidence')
        conf_s = ('%.2f' % conf) if isinstance(conf, (int, float)) else '?'
        regime = m.get('regime', '?')
        session = m.get('session', '?')
        tier = m.get('tier', '?')
        pnl, comm, has_mark = trade_pnl(base, et, xt, ep, lev, size, st, ex_inc, marks)
        tot += pnl
        comm_tot += comm
        note = ' (u)' if has_mark else ''
        print('%-5s %-9s %-13s %-8s %-5s %-5s %-7s %-6s %-5s %-8s %-12s %-5s %10.4f %7.4f | %s%s' % (
            typ, base, et[:16], regime, adx_s, rsi_s, imb_s, bar_s, vr_s, session, tier, conf_s,
            pnl, comm, reason or 'OPEN', note))
    print('%-5s %-9s %-13s %-8s %-5s %-5s %-7s %-6s %-5s %-8s %-12s %-5s %10.4f %7.4f |' % (
        '', 'NET', '', '', '', '', '', '', '', '', '', '', tot, comm_tot))
    print('NET P&L (realized + unrealized for OPEN) = %+.4f USD over %d %s A6 entries in %s..%s'
          % (tot + comm_tot, len(trades), args.type, from_dt, args.to_dt or 'now'))

    if not args.no_split and args.type != 'wall':
        low, high, missing = bar_split(trades, ex_inc, marks)
        print('\n(bar%% split is momentum-relevant; walls key on IMB instead)')
        print('bar%%<9 : %d trades sum %+7.4f USD' % (len(low), sum(low)))
        print('bar%%>=9: %d trades sum %+7.4f USD' % (len(high), sum(high)))
        if missing:
            print('skipped %d trades with no bar_move_pct' % missing)


if __name__ == '__main__':
    main()