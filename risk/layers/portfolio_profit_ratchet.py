"""
Portfolio Profit Ratchet Layer
Implements a global trailing take profit for the entire portfolio using WebSockets.
"""

import asyncio
import threading
import ccxt.pro
from datetime import datetime, timedelta
from typing import Dict, Any, List

class PortfolioProfitRatchet:
    def __init__(self, config, db, exchange_client, logger, telegram, trade_manager=None, engine=None):
        self.config = config
        self.db = db
        self.nx = exchange_client # CCXTExchangeClient
        self.logger = logger
        self.telegram = telegram
        self.tm = trade_manager
        self.engine = engine
        
        # Scheme Settings
        self.enabled = getattr(config, 'PROFIT_RATCHET_ENABLED', True)
        self.activation_roe = getattr(config, 'PROFIT_RATCHET_ACTIVATION', 6.0)
        self.trailing_distance = getattr(config, 'PROFIT_RATCHET_TRAILING', 1.0)
        self.floor_roe = getattr(config, 'PROFIT_RATCHET_FLOOR', 1.0)
        self.cooldown_mins = getattr(config, 'PROFIT_RATCHET_COOLDOWN', 5)
        self.slippage_buffer = getattr(config, 'PROFIT_RATCHET_SLIPPAGE_BUFFER', 0.2)
        self.fee_buffer_enabled = getattr(config, 'PROFIT_RATCHET_FEE_BUFFER_ENABLED', True)
        
        # State
        self.ratchet_active = False
        self.peak_roe = 0.0
        self.is_liquidating = False
        self.peak_notified_roe = 0.0
        self.stop_event = threading.Event()
        
        # Dollar Tracking State
        self.locked_margin = 0.0
        self.peak_dollar_pnl = 0.0
        self.dollar_trail_distance = 0.0
        self.dollar_hard_floor = 0.0
        
        # CCXT.Pro instance (initialized in loop)
        self.nx_pro = None
        
    async def monitor_loop(self):
        """Pure WebSocket monitoring loop using ccxt.pro"""
        if not self.enabled:
            return

        self.logger.info(f"🛡️ Portfolio Profit Ratchet initialized (WebSocket Mode)")
        
        # 1. Initialize Pro client with proper environment
        exchange_class = getattr(ccxt.pro, self.nx.exchange_id)
        # Use existing credentials and options from sync client
        self.nx_pro = exchange_class({
            'apiKey': self.nx.exchange.apiKey,
            'secret': self.nx.exchange.secret,
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        
        if self.config.EXCHANGE_ENVIRONMENT == 'testnet':
            # Binance futures no longer supports ccxt sandbox mode.
            # Use demo trading when available; otherwise keep the live endpoint
            # and rely on the main exchange client to gate live execution.
            if self.nx.exchange_id == 'binance':
                try:
                    self.nx_pro.enable_demo_trading(True)
                except AttributeError:
                    self.logger.warning(
                        'ccxt.pro Binance demo trading helper is unavailable; '
                        'continuing without sandbox mode.'
                    )
            else:
                self.nx_pro.set_sandbox_mode(True)

        try:
            # First check: Run an immediate check using REST before waiting for WSS updates
            self.logger.info(f"🔄 Performing initial Profit Ratchet check...")
            balance = await self.nx_pro.fetch_balance()
            info = balance.get('info', {})
            total_unrealized_pnl = float(info.get('totalUnrealizedProfit', 0) or 0)
            total_margin = float(info.get('totalInitialMargin', 0) or 0)
            
            # Fetch positions via REST for the initial calculation
            positions_initial = await self.nx_pro.fetch_positions()
            total_volume_initial = sum(abs(float(p.get('notional', 0) or 0)) for p in positions_initial)
            target_symbols_initial = [p['symbol'] for p in positions_initial if abs(float(p.get('notional', 0) or 0)) > 0]
            
            if total_margin > 0:
                # Apply Fee and Slippage Buffer
                if self.fee_buffer_enabled:
                    fee_rate = (float(getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04)) / 100) * 2
                else:
                    fee_rate = 0.0
                
                total_equity = float(info.get('totalWalletBalance', 0) or 0) + total_unrealized_pnl
                session_pnl = total_equity - self.config.INITIAL_CAPITAL
                
                slippage_rate = float(self.slippage_buffer) / 100
                total_costs = total_volume_initial * (fee_rate + slippage_rate)
                net_dollar_pnl = session_pnl - total_costs
                net_roe = (net_dollar_pnl / total_margin) * 100
               
                self.logger.info(f"📊 Initial Ratchet Check: Net ROE {net_roe:.2f}% (Target: {self.activation_roe}%)")
                if net_roe >= self.activation_roe:
                    self.ratchet_active = True
                    self.peak_roe = net_roe
                    self.peak_notified_roe = net_roe
                    
                    # Convert to stable Dollar metrics
                    net_dollar_pnl = total_unrealized_pnl - total_costs
                    self.locked_margin = total_margin
                    self.peak_dollar_pnl = net_dollar_pnl
                    
                    self.dollar_trail_distance = self.locked_margin * (self.trailing_distance / 100.0)
                    self.dollar_hard_floor = self.locked_margin * (self.floor_roe / 100.0)
                    
                    self.telegram.send_futures_message(f"🚀 *PROFIT RATCHET ACTIVATED (Initial)*\nNet ROE: {net_roe:.2f}%\nNet Profit: ${net_dollar_pnl:.2f}\nTrailing Stop set at: ${(net_dollar_pnl - self.dollar_trail_distance):.2f}")
                    
                    # GRACE PERIOD: On startup, do NOT run the stop-check immediately.
                    # If we're already above the activation threshold at boot, we just
                    # activate and begin trailing from the current peak. The WebSocket loop
                    # handles all actual stop-hits from the next live update onwards.
                    # Previously, the fee/slippage buffer subtraction would push
                    # net_dollar_pnl below stop_level in the same instant as activation,
                    # causing immediate panic liquidation on every restart.

            while not self.stop_event.is_set():
                try:
                    # Await position update with a 60s timeout to force a refresh even if no events occur
                    positions = await asyncio.wait_for(self.nx_pro.watch_positions(), timeout=60.0)
                except asyncio.TimeoutError:
                    # If no update in 60s, fetch balance/positions manually to check ROE
                    self.logger.debug("Ratchet: No WSS update in 60s, performing scheduled ROE check...")
                    positions = await self.nx_pro.fetch_positions()

                if not positions:
                    continue

                # Fetch fresh 'info' payload from REST for the exact global totals
                balance = await self.nx_pro.fetch_balance()
                info = balance.get('info', {})

                # Extract Global Metrics (Direct from Binance calculation)
                total_unrealized_pnl = float(info.get('totalUnrealizedProfit', 0) or 0)
                total_margin = float(info.get('totalInitialMargin', 0) or 0)
                total_equity = float(info.get('totalWalletBalance', 0) or 0) + total_unrealized_pnl
                
                # Calculate Session PnL based on starting balance
                session_pnl = total_equity - self.config.INITIAL_CAPITAL
                
                # Estimate Notional Volume for Costs (sum of all active positions)
                total_volume = 0.0
                target_symbols = []
                for p in positions:
                    notional = abs(float(p.get('notional', 0) or 0))
                    if notional > 0:
                        total_volume += notional
                        target_symbols.append(p['symbol'])
                
                if total_margin <= 0:
                    continue

                # 4. Apply Fee and Slippage Buffer (Round trip)
                if self.fee_buffer_enabled:
                    fee_rate = (float(getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04)) / 100) * 2
                else:
                    fee_rate = 0.0
                    
                slippage_rate = float(self.slippage_buffer) / 100
                total_costs = total_volume * (fee_rate + slippage_rate)
                
                # Formula: Use Session PnL instead of just Unrealized
                net_dollar_pnl = session_pnl - total_costs
                net_roe = (net_dollar_pnl / total_margin) * 100

                # 5. Ratchet Activation/Trailing Logic
                if not self.ratchet_active and net_roe >= self.activation_roe:
                    self.ratchet_active = True
                    self.peak_roe = net_roe
                    self.peak_notified_roe = net_roe
                    
                    # Convert to stable Dollar metrics
                    self.locked_margin = total_margin
                    self.peak_dollar_pnl = net_dollar_pnl
                    
                    self.dollar_trail_distance = self.locked_margin * (self.trailing_distance / 100.0)
                    self.dollar_hard_floor = self.locked_margin * (self.floor_roe / 100.0)
                    
                    self.telegram.send_futures_message(f"🚀 *PROFIT RATCHET ACTIVATED*\nNet ROE: {net_roe:.2f}%\nNet Profit: ${net_dollar_pnl:.2f}\nTrailing Stop set at: ${(net_dollar_pnl - self.dollar_trail_distance):.2f}")
                
                if self.ratchet_active:
                    if net_dollar_pnl > self.peak_dollar_pnl:
                        self.peak_dollar_pnl = net_dollar_pnl
                        
                    if net_roe > self.peak_roe:
                        self.peak_roe = net_roe
                        if net_roe >= self.peak_notified_roe + 1.0:
                            self.peak_notified_roe = net_roe
                            self.telegram.send_futures_message(f"📈 *Portfolio Peak Profit*: {net_roe:.2f}% Net ROE (${self.peak_dollar_pnl:.2f})")

                    stop_level_dollar = max(self.dollar_hard_floor, self.peak_dollar_pnl - self.dollar_trail_distance)
                    
                    if net_dollar_pnl <= stop_level_dollar:
                        self.logger.critical(f"⚠️ RATCHET STOP HIT! Net Profit: ${net_dollar_pnl:.2f} (Stop: ${stop_level_dollar:.2f})")
                        await self._liquidate_all(net_roe, target_symbols)
                        self.ratchet_active = False
                        self.peak_roe = 0.0
                        self.peak_dollar_pnl = 0.0
                        self.locked_margin = 0.0
                        self.dollar_trail_distance = 0.0
                        self.dollar_hard_floor = 0.0
                
        except Exception as e:
            self.logger.error(f"Ratchet WebSocket Error: {e}")
            await asyncio.sleep(5)
        finally:
            await self.nx_pro.close()

    async def _liquidate_all(self, exit_roe: float, symbols_ignored: List[str]):
        """
        NUCLEAR OPTION (Optimized): Closes all open positions and cancels orders per symbol.
        Avoids the rate-limited global fetch_open_orders().
        """
        self.is_liquidating = True
        try:
            self.logger.critical(f"🚀 [RATCHET NUCLEAR EXIT] Initiating mass liquidation at {exit_roe:.2f}% ROE...")

            # 1. Fetch All Active Positions (The safe call)
            all_positions = await asyncio.to_thread(self.nx.exchange.fetch_positions)
            active_positions = [p for p in all_positions if float(p.get('contracts', 0)) != 0]

            if not active_positions:
                self.logger.warning("Ratchet: STOP HIT but no active positions found on exchange.")
                self.is_liquidating = False
                return

            self.telegram.send_futures_message(f"🚨 *PROFIT RATCHET STOP HIT!*\nNuking {len(active_positions)} positions to lock in {exit_roe:.2f}% Net ROE.")

            # 2. Set Global Cooldown
            cooldown_until = (datetime.utcnow() + timedelta(minutes=self.cooldown_mins)).isoformat()
            self.db.set_setting('portfolio_ratchet_cooldown_until', cooldown_until)

            # 3. Loop and Close via Exchange Client (Optimized Cleanup)
            db_trades = self.db.get_trades(status='OPEN') if self.tm else []

            for pos in active_positions:
                symbol = pos['symbol']
                try:
                    # Cancel orders for THIS symbol only (No rate limit warnings)
                    await asyncio.to_thread(self.nx.exchange.cancel_all_orders, symbol)
                    
                    # Use the robust close_position helper
                    order = await asyncio.to_thread(self.nx.close_position, symbol)
                    
                    self.logger.info(f"✅ Ratchet: Successfully closed and cleaned {symbol}")
                    
                    # Ground the exit in DB
                    if self.tm:
                        matching_trade = next((t for t in db_trades if t['symbol'] == symbol), None)
                        if matching_trade:
                            await asyncio.to_thread(
                                self.tm.record_exit,
                                symbol=symbol,
                                trade_id=matching_trade['trade_id'],
                                reason="RATCHET_LIQUIDATION",
                                current_price=float(pos.get('info', {}).get('markPrice', 0)),
                                order_response=order
                            )
                            self.logger.info(f"📝 Ratchet: Grounded {symbol} in DB as RATCHET_LIQUIDATION")

                except Exception as ex:
                    self.logger.error(f"❌ Ratchet: Failed to clean/close {symbol}: {ex}")
            
            # --- CRITICAL: Clear Memory Ghosts ---
            if self.engine and hasattr(self.engine, 'positions'):
                self.logger.info(f"🧹 Ratchet: Clearing {len(active_positions)} positions from bot memory...")
                self.engine.positions.clear()

            self.logger.system("🏁 RATCHET MASS LIQUIDATION COMPLETE")
            
            # --- PHASE 34: Persistence Guard (Log event to DB) ---
            try:
                self.db.record_portfolio_ratchet({
                    'activation_roe': self.activation_roe,
                    'peak_roe': self.peak_roe,
                    'exit_roe': exit_roe,
                    'total_pnl': 0.0, # Aggregate PnL handled by individual trade records
                    'positions_closed': len(active_positions),
                    'metadata': {'event': 'Trailing Portfolio TP Hit', 'symbols': [p['symbol'] for p in active_positions]}
                })
            except Exception as db_e:
                self.logger.error(f"Failed to log ratchet event to DB: {db_e}")

            self.is_liquidating = False

        except Exception as e:
            self.logger.error(f"🚨 CRITICAL: Ratchet Nuclear Exit Failure: {e}")
            self.is_liquidating = False

    def is_locked(self) -> bool:
        """Checks if the bot is currently in a profit-ratchet cooldown or liquidation state."""
        if self.is_liquidating:
            return True
        
        # Check DB for cooldown
        cooldown_until_str = self.db.get_setting('portfolio_ratchet_cooldown_until')
        if cooldown_until_str:
            try:
                cooldown_until = datetime.fromisoformat(cooldown_until_str)
                if datetime.utcnow() < cooldown_until:
                    return True
            except:
                pass
        return False
