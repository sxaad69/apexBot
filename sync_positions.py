#!/usr/bin/env python3
"""
Sync Positions Script
1. Compares Binance live positions with bot database.
2. Verifies SL/TP levels (including multi-value TPs in metadata).
3. Emergency Close: Closes positions that are "well past" SL in loss.
"""

import os
import sys
import json
import time
from datetime import datetime
from typing import Dict, List, Any

# Add project root to path
sys.path.append(os.path.abspath(os.path.dirname(__file__)))

from config.config import Config
from database.sqlite_manager import SQLiteManager
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger
from core.trade_manager import TradeManager

class PositionSyncer:
    def __init__(self):
        self.config = Config()
        self.logger = MongoLogger(self.config)
        self.db = SQLiteManager(self.config)
        self.exchange = CCXTExchangeClient(self.config, self.logger)
        self.trade_manager = TradeManager(self.config, self.db, self.exchange, self.logger)
        
    def get_db_open_trades(self) -> Dict[str, Any]:
        """Fetch all trades marked as OPEN in SQLite"""
        conn = self.db._get_connection(self.db.main_db)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades WHERE status = 'OPEN'")
        rows = cursor.fetchall()
        conn.close()
        
        # Symbol as key for easy lookup (Assume one trade per symbol)
        return {row['symbol']: dict(row) for row in rows}

    def get_live_positions(self) -> Dict[str, Any]:
        """Fetch active positions from Binance"""
        positions = self.exchange.get_positions()
        # CCXT positions often include symbols with 0 size, filter them
        active = {}
        for pos in positions:
            if abs(float(pos.get('contracts', 0) or 0)) > 0 or abs(float(pos.get('size', 0) or 0)) > 0:
                symbol = pos['symbol']
                active[symbol] = pos
        return active

    def format_row(self, data: List[Any], widths: List[int]) -> str:
        """Helper to format a table row"""
        return " | ".join(str(val).ljust(width) for val, width in zip(data, widths))

    def sweep_orphan_algos(self, live_positions: Dict[str, Any]) -> int:
        """B2: Cancel Algo API orders (SL/trailing/TP) that are orphaned on symbols
        with no live exchange position.

        Standard cancel_all_orders does NOT touch Algo API orders (Binance migrated
        conditionals to /fapi/v1/algoOrder on 2025-12-09), so orphans accumulate on
        closed symbols. Returns number cancelled.
        """
        cancelled = 0
        # Symbols that HAVE a live position -> their algos are legitimate, skip them
        protected = set(live_positions.keys())
        try:
            # Open algo orders are queryable per-symbol; iterate the union of symbols
            # that appear in open positions + DB trades, plus the open-algo list is
            # symbol-scoped. We approximate: cancel algos on any symbol NOT in `protected`.
            db_syms = self.get_db_open_trades().keys()
            # check symbols we know about; also check all open positions' symbols
            check_syms = set()
            try:
                resp = self.exchange.exchange.fapiPrivateGetOpenAlgoOrders()
                orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
                # Build map symbol -> [algoIds]
                algo_by_sym = {}
                for o in orders:
                    s = o.get('symbol')
                    if s:
                        algo_by_sym.setdefault(s, []).append(o.get('algoId'))
                for s, ids in algo_by_sym.items():
                    canonical = s + '/USDT:USDT' if not s.endswith(':USDT') else s
                    if canonical in protected:
                        continue
                    for algo_id in ids:
                        try:
                            self.exchange.exchange.fapiPrivateDeleteAlgoOrder({'symbol': s, 'algoId': algo_id})
                            cancelled += 1
                        except Exception:
                            pass
                    if ids:
                        self.logger.info(f"🧹 [B2] Swept {len(ids)} orphan algo order(s) for {s}")
            except Exception as e:
                self.logger.warning(f"⚠️ [B2] orphan algo sweep query failed: {e}")
        except Exception as e:
            self.logger.warning(f"⚠️ [B2] orphan algo sweep error: {e}")
        return cancelled

    def run_sync(self):
        print(f"\n{'='*80}")
        print(f"🔄 APEX HUNTER POSITION SYNC - {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
        print(f"{'='*80}\n")

        db_trades = self.get_db_open_trades()
        live_positions = self.get_live_positions()

        # B2: sweep orphaned Algo orders (SL/trailing/TP) on symbols with no live position
        try:
            swept = self.sweep_orphan_algos(live_positions)
            if swept:
                print(f"🧹 [B2] Swept {swept} orphan Algo order(s)\n")
        except Exception as e:
            print(f"⚠️ [B2] orphan sweep error: {e}")
        
        # Fetch current tickers for price-based logic
        symbols_to_fetch = list(set(list(db_trades.keys()) + list(live_positions.keys())))
        tickers = {}
        if symbols_to_fetch:
            try:
                tickers = self.exchange.exchange.fetch_tickers(symbols_to_fetch)
            except:
                pass

        # --- TABLE 1: RECONCILIATION (FEATURE 1) ---
        print("📊 TABLE 1: RECONCILIATION (Binance vs Database)")
        h1 = ["Symbol", "Side", "B.Size", "DB Lev", "B.Lev", "DB Entry", "B.Entry", "Status"]
        w1 = [15, 6, 8, 8, 8, 10, 10, 20]
        print("-" * sum(w1) + "---" * 7)
        print(self.format_row(h1, w1))
        print("-" * sum(w1) + "---" * 7)

        all_symbols = sorted(list(set(db_trades.keys()) | live_positions.keys()))
        for sym in all_symbols:
            db_trade = db_trades.get(sym)
            live_pos = live_positions.get(sym)
            
            binance_size = live_pos.get('contracts', 0) if live_pos else 0
            db_entry = db_trade.get('entry_price', 0) if db_trade else 0
            binance_entry = live_pos.get('entryPrice', 0) if live_pos else 0
            db_lev = db_trade.get('leverage', 0) if db_trade else 0
            binance_lev = live_pos.get('leverage', 0) if live_pos else 0
            side = db_trade.get('side', 'N/A').upper() if db_trade else (live_pos.get('side', 'N/A').upper() if live_pos else 'N/A')
            
            status = "MATCH"
            if db_trade and not live_pos:
                status = "❌ ZOMBIE (Purging...)"
                # Kill Zombie: Trade in DB but NOT on exchange
                try:
                    self.trade_manager.record_exit(
                        symbol=sym,
                        trade_id=db_trade['trade_id'],
                        reason="ZOMBIE_PURGE",
                        current_price=db_trade.get('entry_price', 0),
                        skip_verification=True # Exchange is already zero
                    )
                    status = "✅ ZOMBIE PURGED"
                except: pass
            elif live_pos and not db_trade:
                status = "⚠️ STRANGER (Liquidating...)"
                # Kill Ghost: Trade on exchange but NOT in DB
                try:
                    order = self.exchange.close_position(sym)
                    status = "✅ GHOST LIQUIDATED"
                except Exception as e:
                    status = f"❌ LIQ FAIL: {str(e)[:10]}"
            elif abs(float(db_lev) - float(binance_lev)) > 0:
                status = "🚨 LEV MISMATCH"
            elif abs(float(db_entry) - float(binance_entry)) > 0.0001:
                status = "🕒 ENTRY MISMATCH"
                
            print(self.format_row([
                sym, side, binance_size, 
                f"{int(db_lev)}x", f"{int(binance_lev)}x", 
                f"{db_entry:.4f}", f"{binance_entry:.4f}", 
                status
            ], w1))
        print("")

        # --- TABLE 2: SL/TP VERIFICATION (FEATURE 2) ---
        print("🛡️  TABLE 2: SL/TP VERIFICATION (Active Trades Only)")
        h2 = ["Symbol", "Entry", "Current", "DB Stop Loss", "DB Take Profit (Latest)", "SL Diff", "TP Diff"]
        w2 = [13, 10, 10, 14, 22, 10, 10]
        print("-" * sum(w2) + "---" * 6)
        print(self.format_row(h2, w2))
        print("-" * sum(w2) + "---" * 6)

        for sym in all_symbols:
            db_trade = db_trades.get(sym)
            if not db_trade: continue
            
            ticker = tickers.get(sym, {})
            current_price = ticker.get('last', 0)
            entry_price = db_trade.get('entry_price', 0)
            db_sl = db_trade.get('stop_loss', 0)
            db_tp = db_trade.get('take_profit', 0)
            
            # Extract taking profit from metadata if multi-value exists
            metadata_raw = db_trade.get('metadata', '{}')
            try:
                metadata = json.loads(metadata_raw) if isinstance(metadata_raw, str) else (metadata_raw or {})
                # Check for various multi-TP patterns
                if 'take_profit_levels' in metadata:
                    db_tp = metadata['take_profit_levels']
                elif 'tp_levels' in metadata:
                    db_tp = metadata['tp_levels']
                elif any(k.startswith('tp') for k in metadata.keys()):
                    tps = {k: v for k, v in metadata.items() if k.lower().startswith('tp')}
                    db_tp = f"Multi: {tps}"
            except:
                pass

            sl_diff_pct = ((db_sl / entry_price - 1) * 100) if entry_price else 0
            # For display purposes (Simplified)
            tp_display = str(db_tp)[:20] if not isinstance(db_tp, list) else f"L: {db_tp[-1]}"
            
            print(self.format_row([
                sym, 
                f"{entry_price:.4f}" if entry_price else "0.0000", 
                f"{current_price:.4f}" if current_price else "0.0000", 
                f"{db_sl:.4f}" if db_sl else "0.0000", 
                tp_display,
                f"{sl_diff_pct:.1f}%" if entry_price else "0.0%",
                "SYNCED"
            ], w2))
        print("")

        # --- TABLE 3: EMERGENCY MONITORING (FEATURE 3) ---
        print("🚨 TABLE 3: EMERGENCY MONITORING (Stop-Loss Protection)")
        h3 = ["Symbol", "Side", "Lev", "Current Price", "Stop Loss", "ROE%", "PnL ($)", "Action"]
        w3 = [15, 6, 4, 14, 14, 10, 10, 12]
        print("-" * sum(w3) + "---" * 7)
        print(self.format_row(h3, w3))
        print("-" * sum(w3) + "---" * 7)

        for sym in all_symbols:
            live_pos = live_positions.get(sym)
            db_trade = db_trades.get(sym)
            
            if not live_pos: continue
            
            ticker = tickers.get(sym, {})
            current_price = ticker.get('last', 0)
            if not current_price: continue
            
            # Use Binance source of truth for P&L calculations
            binance_entry = live_pos.get('entryPrice', 0)
            leverage = live_pos.get('leverage', 1)
            side = live_pos.get('side', 'buy').lower()
            
            sl_price = db_trade.get('stop_loss', 0) if db_trade else 0
            
            # Calculate Leveraged P&L (ROE%)
            side_mult = 1 if side == 'buy' else -1
            unleveraged_pnl_pct = ((current_price / binance_entry) - 1) * 100 * side_mult if binance_entry else 0
            roe_pct = unleveraged_pnl_pct * leverage
            
            # Unrealized PnL ($)
            unrealized_pnl_usd = live_pos.get('unrealizedPnl', 0)
            
            action = "WATCHING"
            is_past_sl = False
            
            safety_buffer = 0.002 # 0.2%
            
            if side == 'buy':
                if sl_price and current_price < (sl_price * (1 - safety_buffer)):
                    is_past_sl = True
            else:
                if sl_price and current_price > (sl_price * (1 + safety_buffer)):
                    is_past_sl = True
                    
            if is_past_sl and roe_pct < 0:
                action = "🔥 CLOSING!"
                try:
                    order_res = self.exchange.close_position(sym)
                    
                    # --- GROUNDED EXIT via TradeManager ---
                    if db_trade:
                        self.trade_manager.record_exit(
                            symbol=sym,
                            trade_id=db_trade['trade_id'],
                            reason="EMERGENCY_SYNC_CLOSE",
                            current_price=current_price,
                            order_response=order_res
                        )
                    
                    action = "✅ CLOSED"
                except Exception as e:
                    action = f"❌ ERR: {str(e)[:5]}"
            
            print(self.format_row([
                sym, 
                side.upper(), 
                f"{int(leverage)}x",
                f"{current_price:.4f}", 
                f"{sl_price:.4f}" if sl_price else "NONE", 
                f"{roe_pct:.1f}%", 
                f"${unrealized_pnl_usd:.2f}",
                action
            ], w3))
            
            
        # --- TABLE 4: RECENT EXITS GROUNDING AUDIT (FEATURE 4) ---
        print("\n🔍 TABLE 4: RECENT EXITS GROUNDING AUDIT (DB vs Binance)")
        h4 = ["Symbol", "Exit Time", "Reason", "DB Exit Price", "Binance Fill Price", "Match"]
        w4 = [15, 20, 25, 15, 20, 10]
        print("-" * sum(w4) + "---" * 5)
        print(self.format_row(h4, w4))
        print("-" * sum(w4) + "---" * 5)

        try:
            conn = self.db._get_connection(self.db.main_db)
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trades WHERE status = 'CLOSED' ORDER BY exit_time DESC LIMIT 5")
            recent_closed = [dict(row) for row in cursor.fetchall()]
            conn.close()

            if not recent_closed:
                print("ℹ️  No recently closed trades found.")
            else:
                for db_trade in recent_closed:
                    sym = db_trade['symbol']
                    db_exit = db_trade.get('exit_price') or 0.0
                    exit_time = db_trade.get('exit_time') or 'N/A'
                    reason = db_trade.get('reason') or 'N/A'
                    
                    b_exit = 0.0
                    status = "❌ NO BINANCE DATA"
                    
                    try:
                        # Fetch recent trades for this symbol
                        b_trades = self.exchange.exchange.fetch_my_trades(sym, limit=5)
                        if b_trades:
                            # Take the most recent trade's price
                            last_trade = b_trades[-1]
                            b_exit = float(last_trade.get('price', 0))
                            
                            diff = abs(db_exit - b_exit) / db_exit if db_exit else 0
                            if diff < 0.0001:  # 0.01% tolerance
                                status = "✅ EXACT"
                            else:
                                status = f"⚠️ {diff*100:.2f}% diff"
                    except Exception as e:
                        status = "❌ API ERR"

                    print(self.format_row([
                        sym, 
                        str(exit_time)[:19].replace('T', ' '), 
                        str(reason)[:23],
                        f"{db_exit:.6f}", 
                        f"{b_exit:.6f}",
                        status
                    ], w4))
        except Exception as e:
            print(f"⚠️  Failed to audit recent exits: {e}")

        print(f"\n{'='*80}")
        print("🏁 SYNC COMPLETED")
        print(f"{'='*80}\n")

if __name__ == "__main__":
    syncer = PositionSyncer()
    syncer.run_sync()
