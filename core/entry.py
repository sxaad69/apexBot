"""
Entry Mixin — extracted from main.py PaperTradingEngine (pure move, no logic change).

Holds:
  - execute_entry   (the full signal→risk→entry pipeline; places live orders in live mode)

Mixin design: PaperTradingEngine inherits this, so all self.* references resolve
to the engine instance exactly as before. Method names/signatures are unchanged.
"""

from datetime import datetime


class EntryMixin:

    def execute_entry(self, signal, strategy_name, symbol):
        """Simulate trade execution with risk validation"""
        
        # 0. Concurrent Cooldown Matrix Restriction
        import time
        cooldown_minutes = getattr(self.config, 'FUTURES_SYMBOL_COOLDOWN_MINUTES', 15)
        last_liquidation = self.recent_liquidations.get(symbol, 0)
        elapsed_minutes = (time.time() - last_liquidation) / 60
        if elapsed_minutes < cooldown_minutes:
            self.logger.warning(f"🚫 [COOLDOWN REJECTED] {symbol} hit an exit {elapsed_minutes:.1f}m ago. Under {cooldown_minutes}m cooling off period.")
            return

        # C1: Re-entry blacklist — 2 consecutive SL losses on the same symbol blocks the session.
        # Kills the MORPHO/TA/Q repeat-loss pattern (audit: those 3 symbols lost ~$8.5 on 0 wins).
        max_streak = getattr(self.config, 'FUTURES_MAX_LOSS_STREAK', 2)
        streak = self.symbol_loss_streak.get(symbol.split('/')[0], 0)
        if streak >= max_streak:
            self.logger.warning(
                f"🚫 [RE-ENTRY BLACKLIST] {symbol} has {streak} consecutive SL losses. "
                f"Blocking re-entry for this session."
            )
            return

        # 1. Triple-Check Global Symbol Guard (Phase 28)
        # Prevent taking multiple positions on the same coin across different strategies
        
        # A) In-memory check
        if any(p['symbol'] == symbol for p in self.positions.values()):
            self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (in-memory)")
            return
        
        # B) SQLite check (survives bot restarts)
        if hasattr(self.logger, 'db'):
            try:
                conn = self.logger.db._get_connection(self.logger.db.main_db)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE symbol = ? AND status = 'OPEN'", (symbol,))
                    open_count = cursor.fetchone()[0]
                finally:
                    conn.close()
                if open_count > 0:
                    self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (SQLite OPEN trade exists)")
                    return
            except Exception as guard_e:
                self.logger.warning(f"[{strategy_name}] Symbol guard SQLite check failed: {guard_e}")

        # C) Binance Live Check (Ultimate Source of Truth)
        if self.mode == 'live' and self.exchange:
            try:
                positions = self.exchange.get_positions()
                # Check for any non-zero position on this symbol
                live_pos = next((p for p in positions if p['symbol'] == symbol), None)
                if live_pos and abs(float(live_pos.get('contracts', 0) or 0)) > 0:
                    self.logger.info(f"[{strategy_name}] {symbol} SIGNAL SKIPPED - Global Symbol Guard active (BINANCE position exists)")
                    return
            except Exception as bin_e:
                self.logger.warning(f"[{strategy_name}] Binance guard check failed: {bin_e}")

        position_key = f"{strategy_name}:{symbol}"

        # Entry
        if signal and position_key not in self.positions:
            # Phase 32: Set Margin Mode before entry
            if self.mode == 'live' and self.exchange:
                margin_mode = getattr(self.config, 'FUTURES_MARGIN_MODE', 'ISOLATED')
                self.exchange.set_margin_mode(symbol, margin_mode)

            # --- Confidence-Based Position Sizing ---
            # Higher conviction signals deserve proportionally more capital
            confidence = signal.get('confidence', 0.5)
            if confidence >= 0.95:
                base_size_pct = 0.20  # 20% — Top conviction (C2: big winners were >=0.95)
            elif confidence >= 0.90:
                base_size_pct = 0.15  # 15% — Elite conviction
            elif confidence >= 0.80:
                base_size_pct = 0.12  # 12% — High conviction
            elif confidence >= 0.70:
                base_size_pct = 0.10  # 10% — Standard
            else:
                base_size_pct = 0.07  # 7% — Low conviction, cautious

            total_capital = self.total_capital

            # --- Exposure & Reserve Management ---
            # Dynamically pull limits from config (supports 85% Normal / 95% Elite)
            opportunity_threshold = getattr(self.config, 'FUTURES_OPPORTUNITY_THRESHOLD', 0.90)

            if confidence >= opportunity_threshold:
                # Elite signal: uses higher exposure limit (e.g., 95%)
                max_exposure_pct = getattr(self.config, 'FUTURES_MAX_EXPOSURE_ELITE', 0.95)
            else:
                # Normal signal: uses standard exposure limit (e.g., 85%)
                max_exposure_pct = getattr(self.config, 'FUTURES_MAX_EXPOSURE_NORMAL', 0.85)

            max_exposure = total_capital * max_exposure_pct
            current_exposure = sum(p['size'] for p in self.positions.values() if p['strategy'] == strategy_name)
            available_for_new_trade = max_exposure - current_exposure

            reserve_min_pct = getattr(self.config, 'FUTURES_RESERVE_MIN_PCT', 0.03)
            if available_for_new_trade < total_capital * reserve_min_pct:  # Min 3% must be free
                if confidence >= opportunity_threshold:
                    self.logger.warning(f"[{strategy_name}] {symbol} HIGH-CONFIDENCE SIGNAL SKIPPED - Even reserve is exhausted")
                else:
                    self.logger.warning(f"[{strategy_name}] {symbol} INSUFFICIENT RESERVE CAPITAL - Max exposure reached")
                return

            # Limit position size to available capital, respecting confidence tier
            max_position_size = min(
                total_capital * base_size_pct,
                available_for_new_trade
            )

            # A2: TREND-OVERRIDE signals (strong orderbook below EMA200) enter at 50% size
            size_multiplier = signal.get('size_multiplier', 1.0)
            if size_multiplier < 1.0:
                max_position_size *= size_multiplier
                self.logger.info(
                    f"[{strategy_name}] {symbol} TREND-OVERRIDE size halved "
                    f"(x{size_multiplier}) → ${max_position_size:.2f}"
                )

            self.logger.debug(
                f"[{strategy_name}] Position sizing: Conf {confidence:.2f} → "
                f"{base_size_pct*100:.0f}% size (${max_position_size:.2f}) | "
                f"Reserve mode: {'OPPORTUNITY' if confidence >= opportunity_threshold else 'NORMAL'}"
            )

            # Prepare trade parameters for risk evaluation
            leverage = self.calculate_dynamic_leverage(strategy_name, confidence)
            indicators = signal.get('indicators', {})
            trade_params = {

                'symbol': symbol,
                'side': signal['side'],
                'entry_price': signal['entry_price'],
                'size': max_position_size,  # Respect reserve limits
                'leverage': leverage,
                'stop_loss': signal['stop_loss'],
                'take_profit': signal['take_profit'],
                'strategy': strategy_name,
                'confidence': signal.get('confidence', 0.5),
                'atr': indicators.get('atr', None),  # Pass ATR for dynamic leverage
            }

            # Unified drawdown tracking for the shared pool (Equity-Based)
            # We use Equity (Available Balance + Open Margin + Unrealized P&L) 
            # so that capital allocation isn't counted as a loss, but market drops are.
            current_capital = self.total_capital
            
            # Calculate total value of open positions (Margin + P&L)
            # NOTE: Use 'pos_symbol' NOT 'symbol' to avoid overwriting the outer signal symbol!
            open_positions_value = 0
            for pos in self.positions.values():
                pos_symbol = pos['symbol']  # ← renamed to avoid clobbering outer `symbol`
                current_price = self.current_prices.get(pos_symbol, pos['entry_price'])
                
                # Simple leveraged P&L calculation for paper mode
                price_move = (current_price - pos['entry_price']) / pos['entry_price'] if pos['entry_price'] > 0 else 0
                if pos['side'].lower() == 'sell':
                    price_move = -price_move
                
                unrealized_pnl = pos['size'] * price_move * pos.get('leverage', 1)
                open_positions_value += (pos['size'] + unrealized_pnl)
            
            current_equity = current_capital + open_positions_value
            
            peak_capital = self.peak_balance
            drawdown_percent = 0
            if current_equity < peak_capital:
                drawdown_percent = ((peak_capital - current_equity) / peak_capital) * 100
            
            account_state = {
                'total_balance': current_equity, # Pass equity as total balance for risk layers
                'available_balance': current_capital,
                'drawdown_percent': drawdown_percent,
                'peak_balance': peak_capital,
                'open_positions': list(self.positions.values()),
                'open_positions_count': len(self.positions),
                'recent_trades': self.trades[-20:],
                'current_time': datetime.now()
            }
            
            # Debug: Log account state for visibility
            self.logger.debug(
                f"[{strategy_name}] Account state: "
                f"available=${account_state['available_balance']:.2f}, "
                f"equity=${account_state['total_balance']:.2f}, "
                f"drawdown={account_state['drawdown_percent']:.2f}%"
            )

            # Validate through risk management system (11 layers)
            approved_params = self.risk_manager.evaluate_trade(trade_params, account_state)

            if approved_params is None:
                # Trade rejected by risk management
                self.logger.warning(f"[{strategy_name}] {symbol} TRADE REJECTED by risk management")
                return

            # Approved trade - execute
            try:
                # Initialize safety variables to prevent UnboundLocalError
                position = None
                entry_fee = 0.0
                
                entry_price = approved_params['entry_price']
                size = approved_params['size']
                leverage = approved_params.get('leverage', leverage)
                
                # --- [PHASE 15: LIVE ENTRY BRIDGE] ---
                # This is the "Entry" side of the live trading connection.
                # It only triggers if MODE=live and an exchange is connected.
                order_metadata = {}
                if self.mode == 'live' and self.exchange:
                    try:
                        self.logger.info(f"🚀 LIVE ENTRY: Placing {approved_params['side']} order for {symbol}...")
                        
                        # Calculate quantity for CCXT (Margin * Leverage / Price)
                        quantity = (size * leverage) / entry_price
                        
                        # SAFETY: Ensure total order value (quantity * price) roughly matches intended risk
                        total_value = quantity * entry_price
                        max_allowed_value = (size * leverage) * 1.1 # 10% buffer for slippage
                        if total_value > max_allowed_value:
                            error_msg = f"❌ FORCED ABORT: Calculated value ${total_value:.2f} exceeds safety limit ${max_allowed_value:.2f}"
                            self.logger.critical(error_msg)
                            raise ValueError(error_msg)
                        
                        # Format precision to fix "minimum amount precision" errors
                        try:
                            quantity = float(self.exchange.exchange.amount_to_precision(symbol, quantity))
                        except Exception:
                            pass
                            
                        if quantity <= 0:
                            raise ValueError(f"Calculated target quantity ({quantity}) too small after formatting.")
                            
                        # --- FIX: Check against exchange max quantity limits ---
                        if symbol in self.exchange.exchange.markets:
                            limits = self.exchange.exchange.markets[symbol].get('limits', {})
                            max_qty = limits.get('amount', {}).get('max')
                            if max_qty and quantity > max_qty:
                                self.logger.warning(f"⚠️ Quantity {quantity} exceeds max {max_qty} for {symbol}. Capping.")
                                quantity = max_qty
                            
                        # 0. Set Leverage on Exchange (CRITICAL SYNC)
                        try:
                            # Standardize leverage to int as required by some CCXT implementations
                            self.exchange.exchange.set_leverage(int(leverage), symbol)
                            self.logger.info(f"⚙️ Leverage set to {int(leverage)}x for {symbol} on exchange.")
                        except Exception as lev_e:
                            self.logger.warning(f"⚠️ Failed to set leverage for {symbol}: {lev_e}")

                        # 1. Place Market Entry Order
                        order = self.exchange.exchange.create_order(
                            symbol=symbol,
                            type='market',
                            side=approved_params['side'].lower(),
                            amount=quantity
                        )
                        
                        # --- [PHASE 15.1: ENTRY GROUNDING via TradeManager] ---
                        position = self.trade_manager.record_entry(
                            symbol=symbol,
                            strategy_name=strategy_name,
                            side=approved_params['side'],
                            size=size,
                            leverage=leverage,
                            stop_loss=approved_params['stop_loss'],
                            take_profit=approved_params['take_profit'],
                            order_response=order,
                            planned_price=entry_price,
                            confidence=approved_params.get('confidence', 0.0),
                            stop_loss_roe=approved_params.get('stop_loss_roe', 5.0)
                        )
                        
                        # Use grounded info for SL placement if filled
                        if order and order.get('average'):
                            entry_price = float(order['average'])
                        
                        self.logger.info(f"✅ LIVE ENTRY SUCCESS: {order.get('id')} ({quantity} {symbol}) at {entry_price}")
                        
                        # --- [PHASE 15.2: EXCHANGE-SIDE SL/TP PLACEMENT] ---
                        # EXCHANGE_SIDE_SL is the master switch:
                        #   true  -> place native TRAILING_STOP_MARKET where the market
                        #            supports it, plus a hard STOP_MARKET at the initial
                        #            SL to protect the pre-activation phase.
                        #            If the market does NOT support trailing, place only
                        #            the hard STOP_MARKET and keep the sentinel layer
                        #            active for this position (per-position fallback).
                        #   false -> bot-side sentinel layer only (legacy behavior).
                        sl_order_id = None
                        tp_order_id = None
                        trailing_order_id = None
                        exchange_trailing = False

                        if getattr(self.config, 'EXCHANGE_SIDE_SL', False):
                            sl_side = 'sell' if approved_params['side'].lower() == 'buy' else 'buy'
                            sl_price = approved_params['stop_loss']
                            
                            try:
                                sl_price = float(self.exchange.exchange.price_to_precision(symbol, sl_price))
                            except Exception:
                                pass
                                
                            # 1. Capability check: does this market support native trailing stops?
                            supports_trailing = self._market_supports_trailing_stop(symbol)
                            
                            # 2. Place Hard Stop Loss on Exchange (always: protects pre-activation)
                            client_sl = f"apex{str(position['trade_id']).replace('-', '')[-10:]}SL"
                            sl_order_id = self._place_exchange_conditional(
                                symbol, sl_side, 'STOP_MARKET',
                                quantity=quantity,
                                trigger_price=sl_price,
                                client_algo_id=client_sl[:36],
                            )
                            if sl_order_id:
                                self.logger.info(f"🛡️ HARD STOP PLACED: {sl_side.upper()} {symbol} @ {sl_price} (algoId: {sl_order_id})")
                            else:
                                self.logger.warning(f"⚠️ Failed to place hard Stop Loss on exchange for {symbol}. Bot safety logic still active.")
                            
                            # 3. Native TRAILING_STOP_MARKET (only where the market supports it)
                            if supports_trailing:
                                try:
                                    activation_price = entry_price * (1 + getattr(self.config, 'TRAILING_STOP_ACTIVATION', 5.0) / 100.0)
                                    activation_price = float(self.exchange.exchange.price_to_precision(symbol, activation_price))
                                except Exception:
                                    pass
                                callback_rate = getattr(self.config, 'TRAILING_STOP_DISTANCE', 3.0)
                                client_tr = f"apex{str(position['trade_id']).replace('-', '')[-10:]}TR"
                                trailing_order_id = self._place_exchange_conditional(
                                    symbol, sl_side, 'TRAILING_STOP_MARKET',
                                    quantity=quantity,
                                    activate_price=activation_price,
                                    callback_rate=callback_rate,
                                    client_algo_id=client_tr[:36],
                                )
                                if trailing_order_id:
                                    exchange_trailing = True
                                    self.logger.info(f"🔁 NATIVE TRAILING STOP PLACED: {sl_side.upper()} {symbol} activation={activation_price} callback={callback_rate}% (algoId: {trailing_order_id})")
                                else:
                                    self.logger.warning(f"⚠️ Failed to place native trailing stop on exchange for {symbol}. Falling back to sentinel trailing.")
                                    trailing_order_id = None
                                    exchange_trailing = False
                            
                            # 4. Place Hard Take Profit on Exchange
                            tp_price = approved_params['take_profit']
                            try:
                                tp_price = float(self.exchange.exchange.price_to_precision(symbol, tp_price))
                            except Exception:
                                pass
                                
                            client_tp = f"apex{str(position['trade_id']).replace('-', '')[-10:]}TP"
                            tp_order_id = self._place_exchange_conditional(
                                symbol, sl_side, 'TAKE_PROFIT_MARKET',
                                quantity=quantity,
                                trigger_price=tp_price,
                                client_algo_id=client_tp[:36],
                            )
                            if tp_order_id:
                                self.logger.info(f"🎯 HARD TP PLACED: {sl_side.upper()} {symbol} @ {tp_price} (algoId: {tp_order_id})")
                            else:
                                self.logger.warning(f"⚠️ Failed to place hard Take Profit on exchange for {symbol}. Bot safety logic still active.")

                            # --- SAFETY INVARIANT ---
                            # Trust the exchange ONLY if it fully protects the position:
                            # the hard STOP_MARKET must be live too. If the hard SL failed,
                            # the exchange does NOT cover the pre-activation downside, so the
                            # bot must be the sole authority. Cancel any exchange trailing/TP
                            # we just placed to avoid double-management, and keep the bot-side
                            # sentinel fully active for this position.
                            if exchange_trailing and not sl_order_id:
                                self.logger.warning(
                                    f"⚠️ HARD SL FAILED for {symbol} but trailing placed — exchange is NOT fully protecting "
                                    f"this position. Cancelling exchange trailing/TP; bot sentinel takes full control."
                                )
                                if trailing_order_id:
                                    self._cancel_exchange_conditional(symbol, trailing_order_id)
                                if tp_order_id:
                                    self._cancel_exchange_conditional(symbol, tp_order_id)
                                trailing_order_id = None
                                tp_order_id = None
                                exchange_trailing = False
                        
                        # 5. Save order IDs + exchange_trailing flag to the DB (Trade Manager)
                        if sl_order_id or tp_order_id or trailing_order_id:
                            # Invalidate the openAlgoOrders cache so the fresh orders are
                            # picked up by Phase 15.3 detection on the next poll cycle.
                            self._open_algo_cache.pop(symbol, None)
                            self.trade_manager.db.update_trade_order_ids(
                                position['trade_id'],
                                sl_order_id=sl_order_id,
                                tp_order_id=tp_order_id
                            )
                            # Update in-memory reference too
                            position['sl_order_id'] = sl_order_id
                            position['tp_order_id'] = tp_order_id
                            position['trailing_order_id'] = trailing_order_id
                            position['exchange_trailing'] = exchange_trailing
                            # Persist exchange_trailing + trailing order id to metadata for restart recovery
                            import json as _json
                            _meta = {}
                            try:
                                _meta = _json.loads(position.get('metadata') or '{}')
                            except Exception:
                                _meta = {}
                            _meta['exchange_trailing'] = exchange_trailing
                            _meta['trailing_order_id'] = trailing_order_id
                            self.trade_manager.db.update_trade_metadata(
                                position['trade_id'], {'metadata': _meta}
                            )
                            
                    except Exception as e:
                        self.logger.error(f"🚨 LIVE ENTRY ATTEMPT FAILED for {symbol}: {e}")
                        return

                # --- UNIFIED POSITION TRACKING ---
                with self.trade_lock:
                    if self.mode == 'paper':
                        # Record entry via TradeManager (handles DB persistence)
                        position = self.trade_manager.record_entry(
                            symbol=symbol,
                            strategy_name=strategy_name,
                            side=approved_params['side'],
                            size=size,
                            leverage=leverage,
                            stop_loss=approved_params['stop_loss'],
                            take_profit=approved_params['take_profit'],
                            planned_price=entry_price,
                            confidence=approved_params.get('confidence', 0.0),
                            stop_loss_roe=approved_params.get('stop_loss_roe', 5.0)
                        )
                    
                    # Update Capital accounting ONLY if it's a valid position
                    if not position or 'symbol' not in position:
                        self.logger.error(f"🚨 ATOMIC BOOKING FAILED: Trade manager returned invalid position object for {symbol}. Rejecting.")
                        return

                    # Re-apply exchange-side SL/TP/trailing state onto the FINAL position
                    # object. record_entry() returns a fresh dict that drops the order-ID
                    # fields set on the entry-bridge object above — without this the
                    # in-memory position loses sl_order_id/tp_order_id/trailing_order_id
                    # and tiered trailing + exit detection silently no-op (AGT bug).
                    position['sl_order_id'] = sl_order_id
                    position['tp_order_id'] = tp_order_id
                    position['trailing_order_id'] = trailing_order_id
                    position['exchange_trailing'] = exchange_trailing
                    position['trailing_tier'] = 0

                    fee_percent = getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04) / 100
                    entry_fee = size * fee_percent
                    if self.mode == 'paper':
                        self.total_capital -= (size + entry_fee)
    
                    self.positions[position_key] = position
                
                self.logger.info(f"[{strategy_name}] {symbol} BALANCE DEDUCTED: ${size + entry_fee:.2f} (Entry: ${size:.2f} + Fee: ${entry_fee:.2f})")
                self.logger.info(f"[{strategy_name}] {symbol} ENTRY {signal['side'].upper()} @ ${position['entry_price']:.2f} (Leverage: {leverage}x) ✅ Risk Approved")

                # MongoDB structured logging
                self.logger.trade_entry(
                    symbol=symbol,
                    side=signal['side'],
                    size=position['size'],
                    price=signal['entry_price'],
                    leverage=leverage,
                    market_type='futures',
                    strategy=strategy_name,
                    confidence=confidence,
                    stop_loss=signal['stop_loss'],
                    take_profit=signal['take_profit'],
                    trade_id=position['trade_id'],
                    total_capital=self.total_capital,
                    metadata=order_metadata
                )
            except Exception as e:
                self.logger.error(f"🚨 CRITICAL: Booking Failure for {symbol} | Error: {e}", exc_info=True)
                return

            # Telegram notification
            if self.telegram and self.telegram.futures_bot:
                self.telegram.send_futures_trade_entry({
                    'trade_id': position['trade_id'],
                    'symbol': symbol,
                    'side': signal['side'],
                    'entry_price': signal['entry_price'],
                    'size': position['size'],
                    'leverage': leverage,
                    'stop_loss': signal['stop_loss'],
                    'take_profit': signal['take_profit'],
                    'strategy': strategy_name,
                    'confidence': confidence,
                    'remaining_capital': self.total_capital,
                    'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                })
