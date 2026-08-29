"""
Sync Mixin — extracted from main.py ApexHunterBot (pure move, no logic change).

Holds:
  - _sync_open_trades   (startup reconciliation of DB trades vs exchange positions)

Mixin design: ApexHunterBot inherits this, so all self.* references resolve to
the bot instance exactly as before. Method names/signatures are unchanged.
"""

from datetime import datetime


class SyncMixin:

    def _sync_open_trades(self):
        """
        Exchange-First Architecture: Startup Phase.
        1. Grabs Global Position State from Exchange (The absolute truth).
        2. Drops DB Ghost Trades that aren't on the exchange.
        3. ADOPTS Exchange trades that aren't in the DB.
        4. Hydrates synced memory.
        """
        try:
            # 1. Fetch Global Exchange State ONCE
            global_positions = []
            if self.mode == 'live' or getattr(self.config, 'FUTURES_STRICT_SYNC', False):
                try:
                    self.logger.info("🌐 Fetching Global Positions from Exchange for absolute sync...")
                    ex_positions = self.engine.exchange.get_positions()
                    global_positions = [p for p in ex_positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                except Exception as e:
                    self.logger.warning(f"⚠️ Exchange sync failed on startup: {e}. Falling back to DB state.")
                    # Keep empty list if failed so we don't accidentally close all DB trades, 
                    # but wait, if it fails we might close valid DB trades because list is empty!
                    global_positions = None # None means API failed, distinguish from [] which means 0 positions

            conn = self.logger.db._get_connection(self.logger.db.main_db)
            t3_updates = []
            try:
                cursor = conn.cursor()
                
                # Fetch both OPEN and PENDING_EXIT trades
                cursor.execute("SELECT * FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
                db_trades = cursor.fetchall()
                
                global_symbols = {}
                if global_positions is not None:
                    # Map standard "BTC/USDT" and binance "BTCUSDT" formats for safety
                    global_symbols = {
                        p.get('symbol', '').replace('/', ''): p 
                        for p in global_positions
                    }
                    
                self.logger.info(f"🔄 Reconciling {len(db_trades)} DB trades against Global State...")
                
                # --- PHASE 2: GARBAGE COLLECTION (Drop Ghost Trades) ---
                for db_trade in db_trades:
                    symbol = db_trade['symbol']
                    clean_symbol = symbol.replace('/', '')
                    trade_id = db_trade['trade_id']
                    strategy_name = db_trade['strategy']
                    position_key = f"{strategy_name}:{symbol}"
                    
                    is_closed_on_exchange = False
                    if global_positions is not None:
                        if clean_symbol not in global_symbols:
                            is_closed_on_exchange = True

                    if is_closed_on_exchange:
                        self.logger.warning(f"👻 Garbage Collection: {symbol} ({strategy_name}) is missing from Exchange. Closing in local DB.")
                        exit_time = datetime.utcnow().isoformat()
                        cursor.execute("UPDATE trades SET status = 'CLOSED', exit_time = ?, reason = 'exchange_closed_offline' WHERE trade_id = ?",
                                     (exit_time, trade_id))
                        self.logger.info(f"✅ Synced {symbol} as CLOSED.")
                    else:
                        # HYDRATE into engine memory

                        import json
                        from datetime import datetime as dt
                        
                        # Convert SQLite Row to dict and prepare for engine
                        db_trade = dict(db_trade)
                        metadata = {}
                        try:
                            if db_trade['metadata']:
                                metadata = json.loads(db_trade['metadata'])
                        except: pass
                        
                        position = {
                            'trade_id': db_trade['trade_id'],
                            'symbol': db_trade['symbol'],
                            'side': db_trade['side'],
                            'entry_price': db_trade['entry_price'],
                            'entry_time': dt.fromisoformat(db_trade['entry_time']) if db_trade['entry_time'] else dt.now(),
                            'leverage': db_trade['leverage'],
                            'stop_loss': db_trade['stop_loss'],
                            'take_profit': db_trade['take_profit'],
                            'strategy': db_trade['strategy'],
                            'size': db_trade['size'] if 'size' in db_trade.keys() else metadata.get('size', 0),
                            'status': db_trade['status'],
                            # Trailing stop reconstruction
                            'highest_price': db_trade['highest_price'] or db_trade['entry_price'],
                            'lowest_price': db_trade['lowest_price'] or db_trade['entry_price'],
                            'trailing_stop_active': bool(db_trade['trailing_stop_active']),
                            'trailing_stop_price': db_trade['trailing_stop_price'],
                            'original_stop_loss': db_trade['stop_loss'],
                            # Phase 31: Restore Trailing TP state from persisted metadata
                            'trailing_tp_active': metadata.get('trailing_tp_active', False),
                            'trailing_tp_peak_price': metadata.get('trailing_tp_peak_price', None),
                            'trailing_tp_trough_price': metadata.get('trailing_tp_trough_price', None),
                            'trailing_tp_activation_price': metadata.get('trailing_tp_activation_price', None),
                            # Exchange-Side SL/TP persistence (Phase 15)
                            'sl_order_id': db_trade.get('sl_order_id'),
                            'tp_order_id': db_trade.get('tp_order_id'),
                            'trailing_order_id': metadata.get('trailing_order_id'),
                            'exchange_trailing': metadata.get('exchange_trailing', False),
                            # Keep-alive fallback trailing (previous tier, still live
                            # until the current tier's activation arms)
                            'trailing_fallback_id': metadata.get('trailing_fallback_id'),
                            'trailing_activate_price': metadata.get('trailing_activate_price')
                        }
                        
                        # Add back to active memory
                        self.engine.positions[position_key] = position

                        # --- T3: stale-ID hygiene + T1/T2 backfill for hydrated
                        # positions. Dead IDs (cancelled manually or by a prior
                        # session) would keep Phase 15.3 in permanent "fired-but-
                        # propagation-lag" suppression — clear them. Conversely,
                        # positions with NO tracked IDs (e.g. adopted before T1)
                        # must adopt any pre-existing exchange algos, or get a
                        # fresh hard STOP_MARKET so crash protection exists.
                        try:
                            if self.mode == 'live' and hasattr(self.engine, '_get_cached_open_algo_ids'):
                                open_ids = self.engine._get_cached_open_algo_ids(db_trade['symbol'])
                                if open_ids is not None:
                                    cleared = []
                                    changed = False
                                    for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                                        oid = position.get(key)
                                        if oid and str(oid) not in open_ids:
                                            position[key] = None
                                            cleared.append(key)
                                    if 'trailing_order_id' in cleared:
                                        position['exchange_trailing'] = False
                                    if cleared:
                                        changed = True
                                        self.logger.info(
                                            f"🧹 [T3] {db_trade['symbol']}: cleared stale algo ID(s) "
                                            f"{', '.join(cleared)} (not in open algo orders)"
                                        )
                                    # --- T1/T2 backfill: no tracked IDs at all ---
                                    if not (position.get('sl_order_id') or position.get('tp_order_id')
                                            or position.get('trailing_order_id')):
                                        discovered = None
                                        if hasattr(self.engine, 'discover_position_algos'):
                                            try:
                                                discovered = self.engine.discover_position_algos(db_trade['symbol'])
                                            except Exception as d_e:
                                                self.logger.debug(f"[T1] hydration discovery error {db_trade['symbol']}: {d_e}")
                                        if discovered:
                                            position['sl_order_id'] = discovered.get('sl_order_id')
                                            position['tp_order_id'] = discovered.get('tp_order_id')
                                            position['trailing_order_id'] = discovered.get('trailing_order_id')
                                            if discovered.get('trailing_order_id'):
                                                position['exchange_trailing'] = True
                                            n = discovered.get('open_count', 0)
                                            if n:
                                                changed = True
                                                self.logger.info(
                                                    f"🔍 [T1] Hydrated {db_trade['symbol']}: adopted {n} pre-existing "
                                                    f"exchange algo(s) into tracking"
                                                )
                                            elif (getattr(self.config, 'EXCHANGE_SIDE_SL', False)
                                                    and hasattr(self.engine, '_place_exchange_conditional')
                                                    and position.get('stop_loss')):
                                                try:
                                                    g_ref = global_symbols.get(clean_symbol) if global_positions is not None else None
                                                    qty = abs(float(g_ref.get('contracts', 0) or 0)) if g_ref else 0
                                                    if qty > 0:
                                                        sl_side = 'sell' if str(position.get('side', 'buy')).lower() == 'buy' else 'buy'
                                                        sl_price = float(position['stop_loss'])
                                                        try:
                                                            sl_price = float(self.engine.exchange.exchange.price_to_precision(db_trade['symbol'], sl_price))
                                                        except Exception:
                                                            pass
                                                        client_sl = f"apex{str(position['trade_id']).replace('-', '')[-10:]}SL"[:36]
                                                        new_id = self.engine._place_exchange_conditional(
                                                            db_trade['symbol'], sl_side, 'STOP_MARKET',
                                                            quantity=qty,
                                                            trigger_price=sl_price,
                                                            client_algo_id=client_sl,
                                                        )
                                                        if new_id:
                                                            position['sl_order_id'] = new_id
                                                            changed = True
                                                            self.logger.info(f"🛡️ [T2] Hydrated {db_trade['symbol']}: hard STOP_MARKET placed @ {sl_price} (algoId: {new_id})")
                                                        else:
                                                            self.logger.warning(f"⚠️ [T2] Hydrated {db_trade['symbol']}: STOP_MARKET placement failed — sentinel-only")
                                                except Exception as pl_e:
                                                    self.logger.warning(f"⚠️ [T2] Hydrated {db_trade['symbol']}: placement error: {pl_e}")
                                    if changed:
                                        # capture current DB metadata so the persist step
                                        # below doesn't clobber unrelated keys
                                        t3_updates.append({'pos': dict(position), 'meta': dict(metadata)})
                        except Exception as t3_e:
                            self.logger.debug(f"[T3] validation skipped for {db_trade['symbol']}: {t3_e}")

                        self.logger.info(f"🔌 HYDRATED position: {position_key} (ID: {trade_id[:8]}...)")

                # --- PHASE 3: ADOPTION (Import untracked Exchange positions) ---
                if global_positions is not None:
                    db_symbols = {d['symbol'].replace('/', '') for d in db_trades}
                    for g_pos in global_positions:
                        g_sym = g_pos.get('symbol', '').replace('/', '')
                        if not g_sym or g_sym in db_symbols:
                            continue
                            
                        self.logger.warning(f"🛸 ADOPTION: Untracked live position found on Exchange for {g_sym}! Adopting into Engine.")
                        try:
                            side = 'sell' if float(g_pos.get('contracts', 0) or 0) < 0 else 'buy'
                            entry_price = float(g_pos.get('entryPrice', 0))
                            contracts_size = abs(float(g_pos.get('contracts', 0)))
                            notional_value = contracts_size * entry_price
                            
                            # Automatically record it using trade manager 
                            # Convert to standard format with slash if possible (Binance symbol repair)
                            standard_symbol = g_pos.get('symbol')
                            if not '/' in standard_symbol:
                                standard_symbol = standard_symbol.replace('USDT', '/USDT')
                            
                            # Calculate 5% ROE Hard Stop for adopted positions
                            lev = int(g_pos.get('leverage') or 1)
                            max_move = (5.0 / lev) / 100
                            if side == 'buy':
                                ad_sl = entry_price * (1 - max_move)
                            else:
                                ad_sl = entry_price * (1 + max_move)

                            adopted_pos = self.engine.trade_manager.record_entry(
                                symbol=standard_symbol,
                                strategy_name='MANUAL_ADOPT',
                                side=side,
                                size=notional_value,
                                leverage=lev,
                                stop_loss=ad_sl,
                                take_profit=None,
                                order_response=g_pos,
                                planned_price=entry_price
                            )
                            # Ensure tracking is mounted in memory
                            if adopted_pos:
                                # --- T1: discover pre-existing exchange algos ---
                                discovered = None
                                if hasattr(self.engine, 'discover_position_algos'):
                                    try:
                                        discovered = self.engine.discover_position_algos(standard_symbol)
                                    except Exception as d_e:
                                        self.logger.debug(f"[T1] discovery error {standard_symbol}: {d_e}")
                                if discovered:
                                    adopted_pos['sl_order_id'] = discovered.get('sl_order_id')
                                    adopted_pos['tp_order_id'] = discovered.get('tp_order_id')
                                    adopted_pos['trailing_order_id'] = discovered.get('trailing_order_id')
                                    if discovered.get('trailing_order_id'):
                                        adopted_pos['exchange_trailing'] = True
                                    n = discovered.get('open_count', 0)
                                    if n:
                                        self.logger.info(
                                            f"🔍 [T1] Adopted {standard_symbol}: found {n} open algo(s) on exchange "
                                            f"(SL={discovered.get('sl_order_id')}, TP={discovered.get('tp_order_id')}, "
                                            f"TR={discovered.get('trailing_order_id')}) — now tracked, no bot-side double-management"
                                        )
                                else:
                                    adopted_pos['sl_order_id'] = None
                                    adopted_pos['tp_order_id'] = None
                                    adopted_pos['trailing_order_id'] = None

                                # --- T2: zero protection on exchange -> place hard STOP_MARKET ---
                                placed_sl = None
                                if (discovered is not None and discovered.get('open_count', 0) == 0
                                        and self.mode == 'live'
                                        and getattr(self.config, 'EXCHANGE_SIDE_SL', False)
                                        and hasattr(self.engine, '_place_exchange_conditional')):
                                    try:
                                        sl_side = 'sell' if side == 'buy' else 'buy'
                                        sl_price = ad_sl
                                        try:
                                            sl_price = float(self.engine.exchange.exchange.price_to_precision(standard_symbol, sl_price))
                                        except Exception:
                                            pass
                                        client_sl = f"apex{str(adopted_pos['trade_id']).replace('-', '')[-10:]}SL"[:36]
                                        placed_sl = self.engine._place_exchange_conditional(
                                            standard_symbol, sl_side, 'STOP_MARKET',
                                            quantity=contracts_size,
                                            trigger_price=sl_price,
                                            client_algo_id=client_sl,
                                        )
                                        if placed_sl:
                                            adopted_pos['sl_order_id'] = placed_sl
                                            self.logger.info(f"🛡️ [T2] Adopted {standard_symbol}: hard STOP_MARKET placed @ {sl_price} (algoId: {placed_sl})")
                                        else:
                                            self.logger.warning(f"⚠️ [T2] Adopted {standard_symbol}: STOP_MARKET placement failed — sentinel-only protection")
                                    except Exception as place_e:
                                        self.logger.warning(f"⚠️ [T2] Adopted {standard_symbol}: placement error: {place_e}")

                                # --- persist adopted tracking state for restart recovery ---
                                try:
                                    if (adopted_pos.get('sl_order_id') or adopted_pos.get('tp_order_id')
                                            or adopted_pos.get('trailing_order_id')):
                                        self.logger.db.update_trade_order_ids(
                                            adopted_pos['trade_id'],
                                            sl_order_id=adopted_pos.get('sl_order_id'),
                                            tp_order_id=adopted_pos.get('tp_order_id'),
                                        )
                                        import json as _json
                                        _meta = {}
                                        try:
                                            _meta = _json.loads(adopted_pos.get('metadata') or '{}')
                                        except Exception:
                                            _meta = {}
                                        _meta['exchange_trailing'] = bool(adopted_pos.get('exchange_trailing'))
                                        _meta['trailing_order_id'] = adopted_pos.get('trailing_order_id')
                                        self.logger.db.update_trade_metadata(adopted_pos['trade_id'], {'metadata': _meta})
                                except Exception as db_e:
                                    self.logger.warning(f"⚠️ [T1] Failed to persist adopted algo IDs for {standard_symbol}: {db_e}")

                                self.engine.positions[f"MANUAL_ADOPT:{standard_symbol}"] = adopted_pos
                                self.logger.info(f"🧬 Successfully adopted {standard_symbol} into active tracking matrix.")
                        except Exception as e:
                            self.logger.error(f"Failed to adopt unmatched position {g_sym}: {e}", exc_info=True)
                        
                conn.commit()
            finally:
                conn.close()

            # T3: persist cleared stale IDs after the sync connection is closed
            for item in t3_updates:
                pos, meta = item['pos'], item['meta']
                try:
                    if pos.get('sl_order_id') is None or pos.get('tp_order_id') is None:
                        self.logger.db.update_trade_order_ids(
                            pos['trade_id'],
                            sl_order_id=pos.get('sl_order_id'),
                            tp_order_id=pos.get('tp_order_id'),
                        )
                    meta['exchange_trailing'] = bool(pos.get('exchange_trailing'))
                    meta['trailing_order_id'] = pos.get('trailing_order_id')
                    self.logger.db.update_trade_metadata(pos['trade_id'], {'metadata': meta})
                except Exception as p_e:
                    self.logger.debug(f"[T3] persist failed for {pos.get('symbol')}: {p_e}")
        except Exception as e:
            self.logger.error(f"Failed to run Startup Hydration/Reconciliation: {e}")
