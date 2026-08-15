#!/usr/bin/env python3
import os
import sys
import time
from typing import List, Set

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from database.sqlite_manager import SQLiteManager
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger
from core.trade_manager import TradeManager


def _algo_symbol(symbol: str) -> str:
    """Normalize a ccxt symbol ('BTC/USDT:USDT') to raw Binance format ('BTCUSDT')."""
    return symbol.replace('/', '').split(':')[0]


def _cancel_open_algo_orders(exchange, logger, symbols: List[str], db=None) -> int:
    """Cancel ALL open Algo API orders (SL/TP/trailing) for the given symbols.

    Binance migrated conditional orders to the Algo API (2025-12-09); standard
    cancel_all_orders does NOT touch them. We query GET /fapi/v1/openAlgoOrders
    per symbol and DELETE each algoId. Also cancels DB-tracked SL/TP IDs that may
    not yet appear in the open-algo list.
    """
    cancelled = 0
    raw = exchange.exchange

    # Belt-and-suspenders: also cancel DB-tracked algo IDs (sl_order_id/tp_order_id)
    tracked_ids: Set[str] = set()
    if db is not None:
        try:
            for t in db.get_trades(status='OPEN'):
                for key in ('sl_order_id', 'tp_order_id'):
                    oid = t.get(key)
                    if oid:
                        tracked_ids.add(str(oid))
        except Exception as e:
            logger.warning(f"⚠️ Could not read DB algo IDs for purge: {e}")

    for symbol in symbols:
        algo_sym = _algo_symbol(symbol)
        algo_ids: Set[str] = set()
        try:
            resp = raw.fapiPrivateGetOpenAlgoOrders({'symbol': algo_sym})
            orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
            for o in orders:
                if isinstance(o, dict) and o.get('algoId') is not None:
                    algo_ids.add(str(o['algoId']))
        except Exception as e:
            logger.warning(f"⚠️ openAlgoOrders query failed for {symbol}: {e}")

        # Merge DB-tracked ids for this symbol
        if db is not None:
            try:
                for t in db.get_trades(status='OPEN'):
                    if t.get('symbol') != symbol:
                        continue
                    for key in ('sl_order_id', 'tp_order_id'):
                        oid = t.get(key)
                        if oid:
                            algo_ids.add(str(oid))
            except Exception:
                pass

        for algo_id in sorted(algo_ids):
            try:
                raw.fapiPrivateDeleteAlgoOrder({'symbol': algo_sym, 'algoId': algo_id})
                cancelled += 1
                logger.info(f"  🗑️  Canceled algo order {algo_id} for {symbol}")
            except Exception as e:
                logger.debug(f"  ⚠️  Algo {algo_id} for {symbol} may already be gone: {e}")

    return cancelled


def emergency_liquidate():
    """
    NUCLEAR OPTION: Closes all open positions, cancels all open orders (including
    Algo API SL/TP/trailing), and purges orphan DB trades. Synchronizes with SQLite.
    """
    config = Config()
    logger = MongoLogger(config)
    db = SQLiteManager(config)
    exchange = CCXTExchangeClient(config, logger)
    raw = exchange.exchange  # raw ccxt instance (bypasses rate-limited wrapper)
    tm = TradeManager(config, db, exchange, logger)

    print("\n" + "!" * 80)
    print("⚠️  EMERGENCY LIQUIDATION INITIATED ⚠️")
    print("!" * 80 + "\n")

    try:
        # ---- Gather symbols from exchange + DB ----
        positions = exchange.exchange.fetch_positions()
        active_positions = [p for p in positions if float(p.get('contracts', 0) or 0) != 0]
        db_trades = db.get_trades(status='OPEN')
        db_symbols = set(t['symbol'] for t in db_trades if t.get('symbol'))
        exchange_symbols = set(p['symbol'] for p in active_positions)

        all_symbols = sorted(exchange_symbols | db_symbols)
        print(f"🔍 Exchange positions: {len(active_positions)} | DB OPEN trades: {len(db_trades)} | unique symbols: {len(all_symbols)}")

        # ---- Phase 1: Cancel ALL open Algo orders ----
        print("\n🔪 Phase 1: Canceling ALL open Algo API orders (SL/TP/trailing)...")
        algo_cancelled = _cancel_open_algo_orders(exchange, logger, all_symbols, db=db)
        print(f"  ✅ Canceled {algo_cancelled} algo orders")

        # ---- Phase 2: Close positions + regular orders ----
        print(f"\n🔥 Phase 2: Closing {len(active_positions)} open positions...")
        closed = 0
        failed = 0
        for pos in active_positions:
            symbol = pos['symbol']
            side = pos['side']
            contracts = pos['contracts']
            print(f"🔥 Processing {symbol} ({contracts} contracts {side})...")
            try:
                # Cancel regular (non-algo) orders first
                try:
                    exchange.exchange.cancel_all_orders(symbol)
                    logger.info(f"  🛡️  Canceled regular orders for {symbol}")
                except Exception as e:
                    logger.warning(f"  ⚠️  cancel_all_orders failed for {symbol}: {e}")

                # Market close — use DIRECT ccxt call, NOT the exchange.close_position()
                # wrapper. The wrapper goes through get_positions() which is gated by the
                # rate limiter (weight=5, 5s timeout); during a rapid multi-symbol purge
                # the limiter exhausts, get_positions returns [] and the wrapper silently
                # no-ops, leaving positions open. Direct create_market_order is reliable.
                close_side = 'sell' if str(side).lower() == 'long' else 'buy'
                try:
                    order = raw.create_market_order(
                        symbol, close_side, float(contracts),
                        params={'reduceOnly': True},
                    )
                except Exception as close_e:
                    order = {}
                    raise
                closed += 1
                print(f"  ✅ Closed {side.upper()} {symbol}")

                # Ground the exit in DB
                matching_trade = next((t for t in db_trades if t['symbol'] == symbol), None)
                if matching_trade:
                    tm.record_exit(
                        symbol=symbol,
                        trade_id=matching_trade['trade_id'],
                        reason="EMERGENCY_LIQUIDATION",
                        current_price=float(pos.get('info', {}).get('markPrice', 0) or 0),
                        order_response=order,
                        skip_verification=True,
                    )
                    print(f"  ✅ Grounded in Database.")
                else:
                    print(f"  ✅ Position closed (no matching trade in DB).")
            except Exception as close_e:
                failed += 1
                print(f"  ❌ Failed to clean/close {symbol}: {close_e}")

        # ---- Phase 3: Purge orphan DB trades (no remaining exchange position) ----
        print(f"\n🧹 Phase 3: Purging orphan DB trades...")
        # Re-fetch positions to see what's actually left
        remaining = exchange.exchange.fetch_positions()
        remaining_syms = set(p['symbol'] for p in remaining if float(p.get('contracts', 0) or 0) != 0)
        purged = 0
        for t in db.get_trades(status='OPEN'):
            sym = t.get('symbol')
            if sym not in remaining_syms:
                try:
                    db.close_trade(t['trade_id'], {
                        'exit_price': 0.0,
                        'reason': 'EMERGENCY_LIQUIDATION',
                        'pnl_amount': 0.0,
                        'pnl_percent': 0.0,
                        'metadata': {'emergency_purge': True},
                    })
                    purged += 1
                    print(f"  🧹 Purged orphan DB trade {sym} ({t['trade_id']})")
                except Exception as e:
                    print(f"  ❌ Failed to purge {sym}: {e}")

        # ---- Summary ----
        print("\n" + "=" * 80)
        print("🏁 EMERGENCY LIQUIDATION COMPLETE")
        print("=" * 80)
        print(f"  Positions closed:  {closed}  ({failed} failed)")
        print(f"  Algo orders cancelled: {algo_cancelled}")
        print(f"  Orphan DB trades purged: {purged}")
        remaining_open = [p for p in remaining if float(p.get('contracts', 0) or 0) != 0]
        print(f"  Remaining exchange positions: {len(remaining_open)}")
        print(f"  Remaining DB OPEN trades: {len(db.get_trades(status='OPEN'))}")
        print("=" * 80 + "\n")

    except Exception as e:
        print(f"🚨 CRITICAL ERROR during liquidation: {e}")


if __name__ == "__main__":
    emergency_liquidate()
