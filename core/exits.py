"""
Exit & Trailing Mixin — extracted from main.py PaperTradingEngine (pure move, no logic change).

Holds:
  - check_exits                  (0.5s sentinel exit path — SL/TP/trailing polling + software exit)
  - check_position_exit          (per-position software exit decision)
  - update_trailing_stops        (bot-side trailing ratchet)
  - update_trailing_tier         (B1: tiered exchange trailing upgrades)
  - update_trailing_take_profit  (bot-side TP ratchet)
  - _update_symbol_loss_streak   (C1: re-entry blacklist tracking)
  - _persist_tp_update / _persist_tp_watermark / _persist_stop_watermark
  - calculate_dynamic_leverage   (confidence-based leverage)

Mixin design: PaperTradingEngine inherits this, so all self.* references resolve
to the engine instance exactly as before. Method names/signatures are unchanged.
"""


class ExitsMixin:

    def update_trailing_stops(self, symbol, current_price):
        """Update trailing stops for all positions on a symbol"""
        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            # Exchange-managed positions: native trailing on the exchange is the
            # authority. The bot must not double-ratchet or double-close.
            if position.get('exchange_trailing'):
                continue

            strategy_name = position['strategy']

            # Calculate profit threshold for activation
            activation_threshold = self.config.TRAILING_STOP_ACTIVATION / 100  # Convert to decimal
            trailing_distance = self.config.TRAILING_STOP_DISTANCE / 100  # Convert to decimal

            if position['side'] == 'buy':
                # Track highest price since entry
                if current_price > position['highest_price']:
                    position['highest_price'] = current_price
                    # Persistent peak update (Phase 34)
                    self._persist_stop_watermark(position['trade_id'], peak=current_price)
                # Track lowest price since entry too — this is the MAE (max adverse
                # excursion) basis for LONG positions. Previously only the SHORT
                # branch updated lowest_price, so MAE was always 0% for longs and
                # trade_outcomes.max_adverse_excursion was meaningless.
                if current_price < position['lowest_price']:
                    position['lowest_price'] = current_price
                    # Persist the trough to the DB column so record_exit computes a
                    # real MAE (not just metadata). The ratchet layer also writes
                    # lowest_price, but only after trailing activation — this covers
                    # the pre-activation adverse moves too.
                    self._persist_stop_watermark(position['trade_id'], trough=current_price)
                
                # Check for activation
                profit_percent = (current_price - position['entry_price']) / position['entry_price']
                if not position['trailing_stop_active'] and profit_percent >= activation_threshold:
                    position['trailing_stop_active'] = True
                    position['trailing_activation_price'] = current_price
                    # Activation moves SL relative to peak
                    new_stop = position['highest_price'] * (1 - trailing_distance)
                    if position['stop_loss'] is not None and new_stop > position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING ACTIVATED @ ${current_price:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")
                        self._persist_stop_watermark(position['trade_id'], peak=position['highest_price'])

                # Ratchet logic: move SL up if current highest_price justifies it
                if position['trailing_stop_active']:
                    new_stop = position['highest_price'] * (1 - trailing_distance)
                    if position['stop_loss'] is not None and new_stop > position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING RATCHET @ ${current_price:.2f} | Peak: ${position['highest_price']:.2f} | SL: ${old_stop:.2f} -> ${new_stop:.2f}")
                        self._persist_stop_watermark(position['trade_id'], peak=position['highest_price'])

            else:  # sell position (SHORT)
                # Track lowest price since entry (most profitable price for a short)
                if current_price < position['lowest_price']:
                    position['lowest_price'] = current_price
                    # Persistent trough update (Phase 34)
                    self._persist_stop_watermark(position['trade_id'], trough=current_price)

                # For a SHORT: profit_percent is how much price has DROPPED from entry
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']

                # Activation: once we're >= activation threshold in profit
                if not position['trailing_stop_active'] and profit_percent >= activation_threshold:
                    position['trailing_stop_active'] = True
                    position['trailing_activation_price'] = current_price
                    # New stop is ABOVE lowest price by trailing_distance (locking in gains)
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    old_stop = position['stop_loss']
                    position['stop_loss'] = new_stop
                    self.logger.info(f"[{strategy_name}] {symbol} TRAILING ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | SL: ${old_stop:.5f} → ${new_stop:.5f}")
                    self._persist_stop_watermark(position['trade_id'], trough=position['lowest_price'])

                # Ratchet: keep moving stop DOWN as price falls further (locking in MORE profit)
                if position['trailing_stop_active']:
                    new_stop = position['lowest_price'] * (1 + trailing_distance)
                    # Ratchet only in the direction of more profit (lower stop for shorts)
                    if position['stop_loss'] is not None and new_stop < position['stop_loss']:
                        old_stop = position['stop_loss']
                        position['stop_loss'] = new_stop
                        self.logger.info(f"[{strategy_name}] {symbol} TRAILING RATCHET @ ${current_price:.5f} | Trough: ${position['lowest_price']:.5f} | SL: ${old_stop:.5f} → ${new_stop:.5f}")
                        self._persist_stop_watermark(position['trade_id'], trough=position['lowest_price'])
    def _update_symbol_loss_streak(self, symbol, reason, net_pnl):
        """C1: track consecutive stop-loss losses per symbol for the re-entry blacklist."""
        base = symbol.split('/')[0]
        if reason and 'stop_loss' in str(reason).lower():
            # stop_loss exit — increment streak
            self.symbol_loss_streak[base] = self.symbol_loss_streak.get(base, 0) + 1
        elif net_pnl and net_pnl > 0:
            # profitable exit — reset streak
            self.symbol_loss_streak[base] = 0

    def _ensure_hard_stop(self, position, sl_side, quantity):
        """A3: guarantee a hard STOP_MARKET is live for a software-managed position.

        Called when an exchange trailing upgrade fails and we drop to bot-side
        sentinel control. The entry hard SL may be stale/missing; (re)place one at
        the current stop_loss so the downside is never left open.
        """
        try:
            stop_price = position.get('stop_loss')
            if not stop_price or stop_price <= 0:
                return
            sl_price = float(self.exchange.exchange.price_to_precision(position['symbol'], stop_price))
            qty = float(self.exchange.exchange.amount_to_precision(position['symbol'], quantity))
            client_sl = f"apex{str(position['trade_id']).replace('-', '')[-10:]}SL"
            new_sl_id = self._place_exchange_conditional(
                position['symbol'], sl_side, 'STOP_MARKET',
                quantity=qty,
                trigger_price=sl_price,
                client_algo_id=client_sl[:36],
            )
            if new_sl_id:
                position['sl_order_id'] = new_sl_id
                self.trade_manager.db.update_trade_order_ids(position['trade_id'], sl_order_id=new_sl_id)
                self.logger.info(f"🛡️ [A3] Hard STOP re-placed for {position['symbol']} @ {sl_price} (algoId: {new_sl_id})")
        except Exception as e:
            self.logger.warning(f"[A3] _ensure_hard_stop error for {position.get('symbol')}: {e}")

    def update_trailing_tier(self, symbol, current_price):
        """B1: Tiered trailing for exchange-managed positions.

        The exchange's native TRAILING_STOP_MARKET has a fixed callbackRate baked
        in at placement. As a position proves it runs, widen the callback so we
        stop cutting winners early (audit: 53% of trailing exits were premature).
        Tier 0 (<+8%): keep entry callback. Tier 1 (+8%): 5%. Tier 2 (+20%): 8%.

        Each tier change cancels the live trailing algo order and re-places it with
        the wider callbackRate, then persists the new trailing_order_id.

        DIAGNOSTIC: every position evaluated logs its skip reason so we can observe
        why a runner (e.g. AGT) may not upgrade — flag state, missing trailing id,
        unresolved quantity, etc. Remove verbose per-tick logs after root-causing.
        """
        if not getattr(self.config, 'EXCHANGE_SIDE_SL', False):
            return

        tier1_at = getattr(self.config, 'TRAILING_TIER_1_AT', 8.0) / 100.0
        tier1_cb = getattr(self.config, 'TRAILING_TIER_1_CALLBACK', 5.0)
        tier2_at = getattr(self.config, 'TRAILING_TIER_2_AT', 20.0) / 100.0
        tier2_cb = getattr(self.config, 'TRAILING_TIER_2_CALLBACK', 8.0)

        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            # Order-execution gate: tier ANY position with a live exchange trailing,
            # regardless of the (sometimes unreliable) exchange_trailing flag.
            trailing_id = position.get('trailing_order_id')
            if not trailing_id:
                self.logger.debug(
                    f"[tier] {symbol} {position_key}: skip (no trailing_order_id; "
                    f"exchange_trailing={position.get('exchange_trailing')})"
                )
                continue

            entry = float(position.get('entry_price') or 0)
            if entry <= 0:
                self.logger.debug(f"[tier] {symbol} {position_key}: skip (bad entry {entry})")
                continue
            profit_percent = (current_price - entry) / entry

            # Determine target tier from current profit
            if profit_percent >= tier2_at:
                target_tier, target_cb = 2, tier2_cb
            elif profit_percent >= tier1_at:
                target_tier, target_cb = 1, tier1_cb
            else:
                continue  # still in tier 0 — keep entry callback (no log: normal path, noisy)

            current_tier = position.get('trailing_tier', 0)
            if current_tier >= target_tier:
                continue  # already at this tier or higher (no log: normal steady-state)

            self.logger.info(
                f"[tier] {symbol} evaluating upgrade: profit +{profit_percent*100:.1f}% "
                f"> tier{target_tier}@+{tier1_at*100 if target_tier==1 else tier2_at*100:.0f}% | "
                f"cur_tier={current_tier} | trailing_id={trailing_id}"
            )

            # Widen: cancel live trailing, re-place with wider callback
            sl_side = 'sell' if position.get('side') == 'buy' else 'buy'
            size = float(position.get('size') or 0)
            lev = float(position.get('leverage') or 1)
            if size <= 0 or entry <= 0:
                self.logger.warning(f"[tier] {symbol}: cannot resolve size/entry for tier upgrade. Skipping.")
                continue
            quantity = (size * lev) / entry  # same contract math as initial placement
            try:
                quantity = float(self.exchange.exchange.amount_to_precision(symbol, quantity))
            except Exception:
                pass
            if quantity <= 0:
                self.logger.warning(f"[tier] {symbol}: invalid quantity {quantity} for tier upgrade. Skipping.")
                continue

            self._cancel_exchange_conditional(symbol, trailing_id)

            # B3: dedupe — a failed cancel (e.g. -2011 stale ID) can leave the OLD
            # trailing still live, so re-placing here would stack duplicate
            # TRAILING_STOP_MARKET orders (risking the per-symbol algo cap that blocks
            # new callback orders). Enumerate the symbol's open algos and cancel any
            # duplicate trailing-stops before placing the new one.
            try:
                algo_sym = symbol.replace('/', '').split(':')[0]
                resp = self.exchange.exchange.fapiPrivateGetOpenAlgoOrders({'symbol': algo_sym})
                orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
                for o in orders:
                    if isinstance(o, dict) and o.get('algoType') == 'TRAILING_STOP_MARKET':
                        try:
                            self.exchange.exchange.fapiPrivateDeleteAlgoOrder({'symbol': algo_sym, 'algoId': o.get('algoId')})
                        except Exception:
                            pass
                self._open_algo_cache.pop(symbol, None)
            except Exception as _dedupe_e:
                self.logger.debug(f"[tier] {symbol} B3 dedupe skipped: {_dedupe_e}")

            # A1 FIX: Do NOT pass activate_price=current_price. For a live trailing
            # re-place Binance rejects an activation at/behind the current mark with
            # -2021 "Order would immediately trigger" (killed ZEC/HEI/RE/M upgrades).
            # Arm the trailing just AHEAD of the current mark with a small buffer so it
            # engages on the next favorable tick without being instantly triggerable.
            #   long  (SELL trailing): activation must be ABOVE mark  -> mark * (1+buf)
            #   short (BUY  trailing): activation must be BELOW mark -> mark * (1-buf)
            rearm_buffer = getattr(self.config, 'TRAILING_REARM_BUFFER', 0.5) / 100.0
            is_long = position.get('side') == 'buy'
            try:
                mark = float(self.exchange.exchange.fetch_ticker(symbol)['last'])
            except Exception:
                mark = current_price
            if is_long:
                activate = mark * (1 + rearm_buffer)
            else:
                activate = mark * (1 - rearm_buffer)
            try:
                activate = float(self.exchange.exchange.price_to_precision(symbol, activate))
            except Exception:
                pass

            client_tr = f"apex{str(position['trade_id']).replace('-', '')[-10:]}TR"
            # E1: retry the re-place a few times to self-heal transient -2021/-2011
            # placement failures instead of instantly stranding the position in
            # software-managed mode (the ZEC disaster path).
            import time as _time
            new_id = None
            _max_retries = getattr(self.config, 'TIER_REPLACE_RETRIES', 3)
            for _attempt in range(max(1, _max_retries)):
                new_id = self._place_exchange_conditional(
                    symbol, sl_side, 'TRAILING_STOP_MARKET',
                    quantity=quantity,
                    activate_price=activate,
                    callback_rate=target_cb,
                    client_algo_id=client_tr[:36],
                )
                if new_id:
                    break
                _time.sleep(0.5)
            if new_id:
                position['trailing_order_id'] = new_id
                position['trailing_tier'] = target_tier
                # Invalidate the openAlgo cache so exit-detection re-fetches and does not
                # treat the cancelled old trailing id as a fired exit (phantom-exit bug).
                self._open_algo_cache.pop(symbol, None)
                # Persist tier + order id for restart recovery
                try:
                    import json as _json
                    _meta = _json.loads(position.get('metadata') or '{}')
                except Exception:
                    _meta = {}
                _meta['trailing_tier'] = target_tier
                _meta['trailing_order_id'] = new_id
                self.trade_manager.db.update_trade_metadata(
                    position['trade_id'], {'metadata': _meta}
                )
                self.logger.info(
                    f"🎯 TRAILING TIER UPGRADE: {symbol} → tier {target_tier} "
                    f"(callback {target_cb}%) at +{profit_percent*100:.1f}% profit (algoId: {new_id})"
                )
            else:
                # Re-place failed. The old trailing is already cancelled, so the position
                # would have NO trailing AND (if exchange_trailing stayed True) the bot would
                # also skip software checks → unprotected. Drop exchange_trailing so the
                # bot-side sentinel takes over this position (no unprotected window).
                position['exchange_trailing'] = False
                position['trailing_order_id'] = None
                try:
                    import json as _json
                    _meta = _json.loads(position.get('metadata') or '{}')
                except Exception:
                    _meta = {}
                _meta['exchange_trailing'] = False
                _meta['trailing_order_id'] = None
                self.trade_manager.db.update_trade_metadata(
                    position['trade_id'], {'metadata': _meta}
                )
                # A3: ensure the position has REAL downside protection in software-managed
                # mode. The hard STOP_MARKET (sl_order_id) may be stale/missing; re-place a
                # hard stop at the current stop_loss level so a drop can't run away.
                try:
                    self._ensure_hard_stop(position, sl_side, quantity)
                except Exception as _e:
                    self.logger.warning(f"[tier] A3 hard-stop ensure failed for {symbol}: {_e}")
                self.logger.warning(
                    f"⚠️ Trailing tier upgrade FAILED for {symbol} (old trailing cancelled). "
                    f"Fell back to bot-side sentinel — position now software-managed."
                )
    def update_trailing_take_profit(self, symbol, current_price):
        """Update trailing take profit for all positions on a symbol."""
        if not getattr(self.config, 'TRAILING_TP_ENABLED', True):
            return

        for position_key, position in list(self.positions.items()):
            if position['symbol'] != symbol:
                continue

            # Exchange-managed positions: native trailing on the exchange is the
            # authority. The bot must not double-ratchet or double-close.
            if position.get('exchange_trailing'):
                continue

            strategy_name = position['strategy']
            tp_activation_threshold = getattr(self.config, 'TRAILING_TP_ACTIVATION', 3.0) / 100
            tp_trailing_distance = getattr(self.config, 'TRAILING_TP_DISTANCE', 1.5) / 100

            if position['side'] == 'buy':
                profit_percent = (current_price - position['entry_price']) / position['entry_price']
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_peak_price'] = None

                if position['trailing_tp_peak_price'] is None or current_price > position['trailing_tp_peak_price']:
                    position['trailing_tp_peak_price'] = current_price
                    self._persist_tp_watermark(position['trade_id'], peak=current_price)  # Phase 31

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 - tp_trailing_distance)
                        if new_tp > old_tp and new_tp > position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | TP: ${old_tp:.5f} → ${new_tp:.5f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "activation")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_peak_price'] * (1 - tp_trailing_distance)
                        if new_tp > position['take_profit'] and new_tp > position['entry_price']: # Move TP HIGHER for Long
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "ratchet")

            else:  # sell position
                profit_percent = (position['entry_price'] - current_price) / position['entry_price']
                if 'trailing_tp_active' not in position:
                    position['trailing_tp_active'] = False
                    position['trailing_tp_trough_price'] = None

                if position['trailing_tp_trough_price'] is None or current_price < position['trailing_tp_trough_price']:
                    position['trailing_tp_trough_price'] = current_price
                    self._persist_tp_watermark(position['trade_id'], trough=current_price)  # Phase 31

                    if profit_percent >= tp_activation_threshold and not position['trailing_tp_active']:
                        position['trailing_tp_active'] = True
                        position['trailing_tp_activation_price'] = current_price
                        old_tp = position['take_profit']
                        new_tp = current_price * (1 + tp_trailing_distance)
                        if new_tp < old_tp and new_tp < position['entry_price']:
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP ACTIVATED @ ${current_price:.5f} (Profit: {profit_percent*100:.2f}%) | TP: ${old_tp:.5f} → ${new_tp:.5f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "activation")

                    elif position['trailing_tp_active']:
                        new_tp = position['trailing_tp_trough_price'] * (1 + tp_trailing_distance)
                        if new_tp < position['take_profit'] and new_tp < position['entry_price']: # Move TP LOWER for Short
                            old_tp = position['take_profit']
                            position['take_profit'] = new_tp
                            self.logger.info(f"[{strategy_name}] {symbol} TRAILING TP UPDATED @ ${current_price:.2f} | TP: ${old_tp:.2f} → ${new_tp:.2f}")
                            self._persist_tp_update(position['trade_id'], new_tp, current_price, "ratchet")
    def _persist_tp_update(self, trade_id: str, new_tp: float, current_price: float, event_type: str):
        """Helper to sync trailing take-profit changes to the DB."""
        try:
            import json
            from datetime import datetime
            trades = self.db.get_trades(status='OPEN')
            trade = next((t for t in trades if t['trade_id'] == trade_id), None)
            if trade:
                meta = json.loads(trade['metadata']) if trade['metadata'] else {}
                meta['trailing_tp_history'] = meta.get('trailing_tp_history', []) + [{
                    'price': new_tp, 
                    'trigger_price': current_price, 
                    'type': event_type,
                    'time': datetime.utcnow().isoformat()
                }]
                self.trade_manager.update_trade_params(trade_id, {
                    'take_profit': new_tp,
                    'metadata': meta
                })

                # --- [PHASE 15.4b: EXCHANGE-SIDE TP RATCHET] ---
                if self.mode == 'live' and getattr(self.config, 'ENABLE_EXCHANGE_STOPS', False):
                    # We need the current active TP order ID
                    # Re-fetch from DB to be safe, or get from in-memory positions
                    tp_order_id = None
                    sl_order_id = None
                    symbol = None
                    sl_side = None
                    qty = None
                    
                    for pos in self.positions.values():
                        if pos['trade_id'] == trade_id:
                            tp_order_id = pos.get('tp_order_id')
                            sl_order_id = pos.get('sl_order_id')
                            symbol = pos['symbol']
                            sl_side = 'sell' if pos['side'] == 'buy' else 'buy'
                            qty = pos['size']
                            break
                            
                    if tp_order_id and symbol:
                        try:
                            # 1. Try to cancel old TP
                            self.exchange.exchange.cancel_order(tp_order_id, symbol)
                            
                            # 2. SUCCESS: Place new TP. Use base asset qty from metadata, not dollar size.
                            tp_order_type = getattr(self.config, 'EXCHANGE_TP_ORDER_TYPE', 'TAKE_PROFIT_MARKET')
                            new_tp_price = new_tp
                            # Resolve base asset quantity
                            base_qty = None
                            for pos in self.positions.values():
                                if pos['trade_id'] == trade_id:
                                    # Try getting executed_qty from DB metadata first, fallback to calculating
                                    trades = self.db.get_trades(status='OPEN')
                                    t = next((t for t in trades if t['trade_id'] == trade_id), None)
                                    if t:
                                        meta = {}
                                        try: meta = json.loads(t['metadata']) if t['metadata'] else {}
                                        except Exception:
                                            pass
                                        base_qty = meta.get('executed_qty')
                                    if not base_qty and pos.get('entry_price', 0) > 0:
                                        leverage = pos.get('leverage', 1)
                                        base_qty = (pos['size'] * leverage) / pos['entry_price']
                                    break
                            
                            if not base_qty:
                                self.logger.warning(f"⚠️ Could not resolve base quantity for {symbol} TP ratchet. Skipping.")
                                return
                            
                            try:
                                new_tp_price = float(self.exchange.exchange.price_to_precision(symbol, new_tp_price))
                                base_qty = float(self.exchange.exchange.amount_to_precision(symbol, base_qty))
                            except Exception:
                                pass
                            
                            new_tp_order = self.exchange.exchange.create_stop_market_order(
                                symbol,
                                sl_side,
                                base_qty,
                                new_tp_price,
                                {'reduceOnly': True}
                            )
                            
                            # 3. Save new ID
                            new_id = new_tp_order.get('id')
                            if new_id:
                                self.trade_manager.db.update_trade_order_ids(trade_id, tp_order_id=new_id)
                                for pos in self.positions.values():
                                    if pos['trade_id'] == trade_id: pos['tp_order_id'] = new_id
                                self.logger.info(f"🎯 EXCHANGE TP RATCHET SUCCESS: {symbol} -> ${new_tp_price}")
                        
                        except Exception as e:
                            # 4. CANCEL FAILED: Check if it was already filled (Race Condition)
                            try:
                                old_order = self.exchange.exchange.fetch_order(tp_order_id, symbol)
                                if old_order.get('status') == 'closed':
                                    self.logger.info(f"🎯 [RACE DETECTED] TP already filled for {symbol} during ratchet. Recording exit.")
                                    if sl_order_id:
                                        try: self.exchange.exchange.cancel_order(sl_order_id, symbol)
                                        except Exception:
                                            pass
                                    self.trade_manager.record_exit(
                                        symbol=symbol, trade_id=trade_id, reason='take_profit',
                                        current_price=old_order.get('average') or current_price,
                                        order_response=old_order
                                    )
                                    # --- BUG FIX: Remove from in-memory positions to avoid ghost trade ---
                                    position_key = f"{next((p['strategy'] for p in self.positions.values() if p['trade_id'] == trade_id), None)}:{symbol}"
                                    if position_key in self.positions:
                                        del self.positions[position_key]
                                    return
                            except Exception as fetch_e:
                                self.logger.warning(f"Failed to verify old TP status after ratchet fail: {fetch_e}")
                            
                            self.logger.warning(f"⚠️ Trailing TP exchange update failed for {symbol}: {e}. Software fallback active.")
        except Exception as e:
            self.logger.error(f"Failed to persist Trailing TP update: {e}")
    def _persist_tp_watermark(self, trade_id: str, peak: float = None, trough: float = None):
        """Phase 31: Persist trailing TP high-water/low-water marks to metadata for restart recovery."""
        try:
            import json
            trades = self.db.get_trades(status='OPEN')
            trade = next((t for t in trades if t['trade_id'] == trade_id), None)
            if not trade:
                return
            meta = json.loads(trade['metadata']) if trade['metadata'] else {}
            if peak is not None:
                meta['trailing_tp_peak_price'] = peak
            if trough is not None:
                meta['trailing_tp_trough_price'] = trough
            # Also persist the active flag from memory (look it up by trade_id)
            for pos in self.positions.values():
                if pos.get('trade_id') == trade_id:
                    meta['trailing_tp_active'] = pos.get('trailing_tp_active', False)
                    if pos.get('trailing_tp_activation_price'):
                        meta['trailing_tp_activation_price'] = pos['trailing_tp_activation_price']
                    break
            self.trade_manager.update_trade_params(trade_id, {'metadata': meta})
        except Exception as e:
            self.logger.warning(f"Failed to persist TP watermark: {e}")
    def _persist_stop_watermark(self, trade_id: str, peak: float = None, trough: float = None):
        """Persist trailing stop high-water/low-water marks to metadata for restart recovery."""
        try:
            import json
            trades = self.db.get_trades(status='OPEN')
            trade = next((t for t in trades if t['trade_id'] == trade_id), None)
            if not trade:
                return
            meta = json.loads(trade['metadata']) if trade['metadata'] else {}
            if peak is not None:
                meta['trailing_stop_peak_price'] = peak
            if trough is not None:
                meta['trailing_stop_trough_price'] = trough
            
            # Also persist the active flag and the current stop level from memory
            for pos in self.positions.values():
                if pos.get('trade_id') == trade_id:
                    meta['trailing_stop_active'] = pos.get('trailing_stop_active', False)
                    meta['trailing_stop_price'] = pos.get('stop_loss') # Use stop_loss as the source of truth
                    break
            # Persist peak/trough to the DB COLUMNS too (not just metadata JSON) so
            # record_exit computes MFE/MAE from real watermarks. update_trade_metadata
            # maps highest_price/lowest_price keys to their columns.
            col_updates = {'metadata': meta}
            if peak is not None:
                col_updates['highest_price'] = peak
            if trough is not None:
                col_updates['lowest_price'] = trough
            self.trade_manager.update_trade_params(trade_id, col_updates)
        except Exception as e:
            self.logger.warning(f"Failed to persist stop watermark: {e}")
    def check_position_exit(self, position, current_price):
        """Check if position should be exited"""
        if position['side'] == 'buy':
            if position.get('stop_loss') is not None and current_price <= position['stop_loss']:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
            elif position.get('take_profit') is not None and current_price >= position['take_profit']:
                return True, 'take_profit'
        else:  # sell
            if position.get('stop_loss') is not None and current_price >= position['stop_loss']:
                return True, 'trailing_stop' if position.get('trailing_stop_active') else 'stop_loss'
            elif position.get('take_profit') is not None and current_price <= position['take_profit']:
                return True, 'take_profit'

        return False, None
    def calculate_dynamic_leverage(self, strategy_name, confidence):
        """
        Tier-based leverage (2026-08-22 flip):
          - conf >= TIER_HOT_CONF (0.90) -> TIER_HOT_LEV (3x)
          - else                         -> TIER_BASE_LEV (2x)
        Hard-capped by LEV_CAP (5x) and drawdown-adjusted max.
        Old ladder (0.55/0.65/0.75 thresholds) was dead code for A6 whose
        scores all sat above 0.75 - every trade took max leverage.
        """
        cap = min(
            int(getattr(self.config, 'LEV_CAP', 5)),
            int(getattr(self.config, 'FUTURES_MAX_LEVERAGE', 5)),
        )

        # Drawdown guard preserved: shrink the cap while underwater
        initial_capital = getattr(self.config, 'INITIAL_CAPITAL', 100)
        current_capital = self.total_capital
        if current_capital < initial_capital:
            drawdown_percent = ((initial_capital - current_capital) / initial_capital) * 100
            cap = min(cap, self.config.get_drawdown_adjusted_leverage(drawdown_percent))

        hot_conf = float(getattr(self.config, 'TIER_HOT_CONF', 0.90))
        lev = int(getattr(self.config, 'TIER_HOT_LEV', 3)) if confidence >= hot_conf \
            else int(getattr(self.config, 'TIER_BASE_LEV', 2))

        return max(1, min(lev, cap))

    def check_exits(self, symbol, current_price):
        """Check all positions for exit conditions for a specific symbol"""
        # B1: Tiered trailing MUST run on the high-frequency sentinel path.
        # The sentinel calls check_exits directly every ~0.5s (not run_cycle, whose
        # exit block is skipped by entry_only=True). Placing the tier call here is
        # the only way a runner actually upgrades its callback (AVNT +141% bug).
        try:
            self.update_trailing_tier(symbol, current_price)
        except Exception as e:
            self.logger.warning(f"⚠️ update_trailing_tier failed in check_exits for {symbol}: {e}")

        closed_positions = []

        for position_key, position in list(self.positions.items()):
            # Only check positions for this symbol
            if position['symbol'] != symbol:
                continue

            # E2: track MFE/MAE watermarks (highest/lowest price) from live ticks so the
            # trade_outcomes table gets real max favorable/adverse excursion instead of 0.
            try:
                entry_px = float(position.get('entry_price') or 0)
                if entry_px > 0 and current_price and current_price > 0:
                    hi = float(position.get('highest_price') or entry_px)
                    lo = float(position.get('lowest_price') or entry_px)
                    changed = False
                    if current_price > hi:
                        position['highest_price'] = hi = current_price
                        changed = True
                    if current_price < lo:
                        position['lowest_price'] = lo = current_price
                        changed = True
                    if changed:
                        # Throttle DB writes: persist watermark at most every N seconds per trade.
                        # In-memory hi/lo stay fresh every tick; flush to DB on interval or at exit.
                        now_ts = __import__('time').time()
                        last_wm = float(position.get('_last_wm_persist', 0) or 0)
                        interval = float(getattr(self.config, 'WATERMARK_PERSIST_INTERVAL', 30.0))
                        if now_ts - last_wm >= interval:
                            self.db.update_trade_metadata(position['trade_id'], {
                                'highest_price': position['highest_price'],
                                'lowest_price': position['lowest_price'],
                            })
                            position['_last_wm_persist'] = now_ts
            except Exception:
                pass

            # --- [PHASE 15.3: EXCHANGE-SIDE SL/TP POLLING] ---
            # If we placed exchange orders, check their status FIRST before software checks.
            # Active only when EXCHANGE_SIDE_SL is true (the master switch).
            exchange_exit_triggered = False
            exchange_exit_reason = None
            filled_order = None
            position_uses_exchange_trailing = position.get('exchange_trailing', False)

            if self.mode == 'live' and getattr(self.config, 'EXCHANGE_SIDE_SL', False):
                sl_id = position.get('sl_order_id')
                tp_id = position.get('tp_order_id')
                trailing_id = position.get('trailing_order_id')
                
                if sl_id or tp_id or trailing_id:
                    try:
                        # Fetch the current set of open algo orders for this symbol
                        # (TTL-cached; a single call covers SL/trailing/TP).
                        open_algo_ids = self._get_cached_open_algo_ids(symbol)
                        
                        if open_algo_ids is not None:
                            # Cross-check: an exchange-side order that fired closes the
                            # position on the exchange. Requiring the position to be GONE
                            # eliminates false positives from placement propagation lag
                            # (freshly placed orders may take a second to appear in the
                            # openAlgoOrders list — we must not record a phantom exit).
                            # A2: use a GROUND-TRUTH direct fetch, NOT get_positions()
                            # (TTL-cache/rate-limit/ban fallbacks can falsely report the
                            # position as gone — the ZEC phantom-close root cause).
                            pos_exists = self.exchange.confirm_position_exists(symbol)
                            
                            # 1. Check SL — if its algoId dropped out of the open list, it fired
                            if sl_id and str(sl_id) not in open_algo_ids:
                                exchange_exit_triggered = True
                                exchange_exit_reason = 'stop_loss'
                                filled_order = {'id': sl_id}
                                # Cancel orphan TP + trailing
                                if tp_id:
                                    self._cancel_exchange_conditional(symbol, tp_id)
                                if trailing_id:
                                    self._cancel_exchange_conditional(symbol, trailing_id)
                        
                            # 2. Check Trailing (native exchange trailing fired first)
                            if not exchange_exit_triggered and trailing_id and str(trailing_id) not in open_algo_ids:
                                exchange_exit_triggered = True
                                exchange_exit_reason = 'trailing_stop'
                                filled_order = {'id': trailing_id}
                                # Cancel orphan SL + TP
                                if sl_id:
                                    self._cancel_exchange_conditional(symbol, sl_id)
                                if tp_id:
                                    self._cancel_exchange_conditional(symbol, tp_id)
                        
                            # 3. Check TP (if SL/trailing didn't fill)
                            if not exchange_exit_triggered and tp_id and str(tp_id) not in open_algo_ids:
                                exchange_exit_triggered = True
                                exchange_exit_reason = 'take_profit'
                                filled_order = {'id': tp_id}
                                # Cancel orphan SL + trailing
                                if sl_id:
                                    self._cancel_exchange_conditional(symbol, sl_id)
                                if trailing_id:
                                    self._cancel_exchange_conditional(symbol, trailing_id)
                        
                            # A fired conditional order always closes the position on the
                            # exchange. If the position still exists, the "missing" order
                            # is just propagation lag — do NOT record an exit yet.
                            if exchange_exit_triggered and pos_exists:
                                self.logger.debug(f"{symbol}: algo order fired but position still on exchange ({sl_id=},{trailing_id=},{tp_id=}). Likely propagation lag — waiting.")
                                exchange_exit_triggered = False
                                exchange_exit_reason = None
                                filled_order = None
                        
                        # 4. If any fired on exchange, bypass software checks and record exit
                        if exchange_exit_triggered:
                            self.logger.info(f"⚡ EXCHANGE-SIDE EXIT DETECTED: {symbol} hit {exchange_exit_reason}")
                            # Flush watermark before recording exit (throttled writes may be stale)
                            try:
                                self.db.update_trade_metadata(position['trade_id'], {
                                    'highest_price': position.get('highest_price', position.get('entry_price')),
                                    'lowest_price': position.get('lowest_price', position.get('entry_price')),
                                })
                            except Exception:
                                pass
                            try:
                                # Ensure all IDs are cleared in DB
                                self.trade_manager.db.update_trade_order_ids(position['trade_id'], sl_order_id=None, tp_order_id=None)
                            except Exception as e:
                                self.logger.warning(f"Failed to clear order IDs for {symbol}: {e}")
                                
                            exit_result = self.trade_manager.record_exit(
                                symbol=symbol,
                                trade_id=position['trade_id'],
                                reason=exchange_exit_reason,
                                current_price=filled_order.get('average') or filled_order.get('price') or current_price,
                                order_response=filled_order
                            )
                            
                            if exit_result and exit_result.get('verified'):
                                if self.mode == 'paper':
                                    self.total_capital += exit_result.get('capital_return', 0)
                                is_win = exit_result.get('net_pnl', 0) > 0
                                self.risk_manager.record_trade_result(is_win, exit_result.get('net_pnl', 0))
                                if self.total_capital > self.peak_balance:
                                    self.peak_balance = self.total_capital
                                    self.risk_manager.update_peak_balance(self.total_capital)
                                    if hasattr(self.logger, 'db'):
                                        self.logger.db.set_setting('peak_balance', self.total_capital)
                            
                            import time
                            self.recent_liquidations[symbol] = time.time()
                            self._update_symbol_loss_streak(symbol, exchange_exit_reason, exit_result.get('net_pnl', 0) if exit_result else 0)
                            closed_positions.append(position_key)
                            continue # Skip the rest of the loop for this position
                            
                    except Exception as e:
                        self.logger.warning(f"⚠️ Failed to poll exchange orders for {symbol}: {e}. Falling back to software check.")

            # --- SOFTWARE EXIT CHECKS (Fallback) ---
            # Skip entirely for positions managed by a native exchange trailing stop —
            # the exchange is the authority there, the bot must not double-close.
            if position_uses_exchange_trailing:
                continue

            # Update Trailing Stop Ratchet BEFORE Software Check (System B — TrailingStopLayer)
            trailing_engine = getattr(self, 'trailing_stop_engine', None)
            if trailing_engine:
                trailing_engine.update_position_ratchet(position, current_price)

            # Check for failed previous exits (PENDING_EXIT)
            is_pending_retry = position.get('status') == 'PENDING_EXIT'
            should_exit, reason = self.check_position_exit(position, current_price)

            if should_exit or is_pending_retry:
                if is_pending_retry:
                    self.logger.info(f"🔄 Retrying failed exit for {symbol}...")
                
                strategy_name = position['strategy']
                leverage = position.get('leverage', 1)

                # Flush watermark before software exit (throttled writes may be stale)
                try:
                    self.db.update_trade_metadata(position['trade_id'], {
                        'highest_price': position.get('highest_price', position.get('entry_price')),
                        'lowest_price': position.get('lowest_price', position.get('entry_price')),
                    })
                except Exception:
                    pass
                # --- LIVE EXECUTION (Phase 14) ---
                close_order = None
                if self.mode == 'live':
                    try:
                        self.logger.system(f"🚀 [LIVE] Executing Market Close for {symbol} ({reason})")
                        close_order = self.exchange.close_position(symbol)
                    except Exception as e:
                        self.logger.error(f"🚨 LIVE EXIT FAILED for {symbol}: {e}")
                        # Mark as PENDING_EXIT in SQLite for infinite retry
                        if hasattr(self.logger, 'db'):
                            conn = self.logger.db._get_connection(self.logger.db.main_db)
                            try:
                                cursor = conn.cursor()
                                cursor.execute("UPDATE trades SET status = 'PENDING_EXIT' WHERE trade_id = ?", (position.get('trade_id'),))
                                conn.commit()
                            finally:
                                conn.close()
                        position['status'] = 'PENDING_EXIT'
                        self.positions[position_key] = position
                        continue # Skip capital updates until successfully closed

                # --- UNIFIED EXIT FINALIZATION via TradeManager ---
                exit_result = self.trade_manager.record_exit(
                    symbol=symbol,
                    trade_id=position['trade_id'],
                    reason=reason,
                    current_price=current_price,
                    order_response=close_order
                )

                if exit_result and exit_result.get('verified'):
                    # In paper mode: manually track capital since there's no exchange sync.
                    # In live mode: Binance balance sync (line ~2302) updates total_capital
                    # accurately. Adding capital_return here would create a temporary ghost
                    # spike that gets saved as a false peak_balance before sync corrects it.
                    if self.mode == 'paper':
                        self.total_capital += exit_result.get('capital_return', 0)
                    self.logger.info(f"💰 CAPITAL RECOVERED: ${exit_result.get('capital_return', 0):.2f} (including Sync P&L)")

                    # Record result with risk manager
                    is_win = exit_result.get('net_pnl', 0) > 0
                    self.risk_manager.record_trade_result(is_win, exit_result.get('net_pnl', 0))

                    # Update peak balance and drawdown tracking.
                    # Only persist to DB in paper mode — in live mode, peak is updated
                    # after the Binance sync gives us the real balance (not a ghost value).
                    if self.total_capital > self.peak_balance:
                        self.peak_balance = self.total_capital
                        self.risk_manager.update_peak_balance(self.total_capital)
                        if self.mode == 'paper' and hasattr(self.logger, 'db'):
                            self.logger.db.set_setting('peak_balance', self.total_capital)
                
                # Remove from memory loop
                import time
                self.recent_liquidations[symbol] = time.time()
                self._update_symbol_loss_streak(symbol, reason, exit_result.get('net_pnl', 0) if exit_result else 0)
                closed_positions.append(position_key)
                continue
                # (Legacy code removed)

                # MongoDB structured logging
                self.logger.trade_exit(
                    symbol=symbol,
                    pnl=pnl_amount,
                    pnl_percent=leveraged_pnl_percent * 100,
                    duration=f"{(trade['exit_time'] - trade['entry_time']).seconds // 60} minutes",
                    market_type='futures',
                    exit_price=current_price,
                    reason=reason,
                    strategy=strategy_name,
                    entry_price=position['entry_price'],
                    side=position['side'],
                    leverage=leverage,
                    stop_loss=position['stop_loss'],
                    take_profit=position['take_profit'],
                    trade_id=position.get('trade_id'),
                    total_capital=self.total_capital
                )

                # Telegram notification
                if self.telegram and self.telegram.futures_bot:
                    # Deduplication check
                    import time
                    current_time_ts = time.time()
                    last_notification = self.recent_exit_notifications.get(position_key, 0)
                    
                    if current_time_ts - last_notification > 5:  # 5-second cooldown
                        duration = (trade['exit_time'] - trade['entry_time']).seconds // 60
                        self.telegram.send_futures_trade_exit({
                            'trade_id': position.get('trade_id', 'N/A'),
                            'symbol': symbol,
                            'entry_price': position['entry_price'],
                            'exit_price': current_price,
                            'pnl': pnl_amount,
                            'pnl_percent': leveraged_pnl_percent * 100,
                            'leverage': leverage,
                            'remaining_capital': self.total_capital,
                            'duration': f"{duration} minutes",
                            'strategy': strategy_name
                        })
                        self.recent_exit_notifications[position_key] = current_time_ts
                        
                        # Cleanup old entries (keep last hour only)
                        cutoff = current_time_ts - 3600
                        keys_to_remove = [k for k, v in self.recent_exit_notifications.items() if v < cutoff]
                        for k in keys_to_remove:
                            del self.recent_exit_notifications[k]
                    else:
                        self.logger.debug(f"Skipping duplicate exit notification for {position_key}")

                closed_positions.append(position_key)

        # Remove closed positions
        for position_key in closed_positions:
            del self.positions[position_key]
