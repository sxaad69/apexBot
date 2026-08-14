"""
Sync Mixin — extracted from main.py ApexHunterBot (pure move, no logic change).

Holds:
  - _sync_open_trades   (startup reconciliation of DB trades vs exchange positions)

Mixin design: ApexHunterBot inherits this, so all self.* references resolve to
the bot instance exactly as before. Method names/signatures are unchanged.
"""


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
                            'exchange_trailing': metadata.get('exchange_trailing', False)
                        }
                        
                        # Add back to active memory
                        self.engine.positions[position_key] = position
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
                                self.engine.positions[f"MANUAL_ADOPT:{standard_symbol}"] = adopted_pos
                                self.logger.info(f"🧬 Successfully adopted {standard_symbol} into active tracking matrix.")
                        except Exception as e:
                            self.logger.error(f"Failed to adopt unmatched position {g_sym}: {e}", exc_info=True)
                        
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            self.logger.error(f"Failed to run Startup Hydration/Reconciliation: {e}")
