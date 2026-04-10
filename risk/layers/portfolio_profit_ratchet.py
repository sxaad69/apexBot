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
    def __init__(self, config, db, exchange_client, logger, telegram):
        self.config = config
        self.db = db
        self.nx = exchange_client # CCXTExchangeClient
        self.logger = logger
        self.telegram = telegram
        
        # Scheme Settings
        self.enabled = getattr(config, 'PROFIT_RATCHET_ENABLED', True)
        self.activation_roe = getattr(config, 'PROFIT_RATCHET_ACTIVATION', 6.0)
        self.trailing_distance = getattr(config, 'PROFIT_RATCHET_TRAILING', 1.0)
        self.floor_roe = getattr(config, 'PROFIT_RATCHET_FLOOR', 1.0)
        self.cooldown_mins = getattr(config, 'PROFIT_RATCHET_COOLDOWN', 5)
        self.slippage_buffer = getattr(config, 'PROFIT_RATCHET_SLIPPAGE_BUFFER', 0.2)
        
        # State
        self.ratchet_active = False
        self.peak_roe = 0.0
        self.is_liquidating = False
        self.peak_notified_roe = 0.0
        self.stop_event = threading.Event()
        
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
            self.nx_pro.set_sandbox_mode(True)

        try:
            while not self.stop_event.is_set():
                # Await ANY position update (PnL change, price change, or size change)
                positions = await self.nx_pro.watch_positions()
                
                if not positions:
                    continue

                # Fetch fresh 'info' payload from REST for the exact global totals
                # This matches the working scratch script exactly
                balance = await self.nx_pro.fetch_balance()
                info = balance.get('info', {})

                # Extract Global Metrics (Direct from Binance calculation)
                total_unrealized_pnl = float(info.get('totalUnrealizedProfit', 0) or 0)
                total_margin = float(info.get('totalInitialMargin', 0) or 0)
                
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
                # Logic: Binance profit is GROS; we must subtract our expected exit costs
                fee_rate = (float(getattr(self.config, 'FUTURES_FEE_PERCENT', 0.04)) / 100) * 2
                slippage_rate = float(self.slippage_buffer) / 100
                total_costs = total_volume * (fee_rate + slippage_rate)
                
                # Formula matching verified scratch test:
                net_roe = ((total_unrealized_pnl - total_costs) / total_margin) * 100

                # 5. Ratchet Activation/Trailing Logic
                if not self.ratchet_active and net_roe >= self.activation_roe:
                    self.ratchet_active = True
                    self.peak_roe = net_roe
                    self.peak_notified_roe = net_roe
                    self.telegram.send_futures_message(f"🚀 *PROFIT RATCHET ACTIVATED*\nNet ROE: {net_roe:.2f}%\nTrailing Stop set at: {net_roe - self.trailing_distance:.2f}%")
                
                if self.ratchet_active:
                    if net_roe > self.peak_roe:
                        self.peak_roe = net_roe
                        if net_roe >= self.peak_notified_roe + 1.0:
                            self.peak_notified_roe = net_roe
                            self.telegram.send_futures_message(f"📈 *Portfolio Peak Profit*: {net_roe:.2f}% Net ROE")

                    stop_level = max(self.floor_roe, self.peak_roe - self.trailing_distance)
                    
                    if net_roe <= stop_level:
                        self.logger.critical(f"⚠️ RATCHET STOP HIT! Net ROE: {net_roe:.2f}% (Stop: {stop_level:.2f}%)")
                        await self._liquidate_all(net_roe, target_symbols)
                        self.ratchet_active = False
                        self.peak_roe = 0.0
                
        except Exception as e:
            self.logger.error(f"Ratchet WebSocket Error: {e}")
            await asyncio.sleep(5)
        finally:
            await self.nx_pro.close()

    # (Polling sync removed in favor of watch_positions)

    async def _liquidate_all(self, exit_roe: float, symbols: List[str]):
        """Triggers a mass liquidation event"""
        self.is_liquidating = True
        try:
            self.telegram.send_futures_message(f"🚨 *PROFIT RATCHET STOP HIT!*\nExiting {len(symbols)} positions at {exit_roe:.2f}% Net ROE to lock gains.")
            
            # Step 1: Set global cooldown
            cooldown_until = (datetime.utcnow() + timedelta(minutes=self.cooldown_mins)).isoformat()
            self.db.set_setting('portfolio_ratchet_cooldown_until', cooldown_until)

            # Step 2: Fast Liquidation via CCXT
            # We use the symbols from the live stream
            for symbol in symbols:
                try:
                    await asyncio.to_thread(self.nx.exchange.cancel_all_orders, symbol)
                    # Use the main sync exchange client for orders as it's more stable for one-offs
                    # We need to fetch the position once more to get current side/size
                    # Or we could have passed the full pos dicts
                    positions = await asyncio.to_thread(self.nx.get_positions, symbol)
                    if not positions: continue
                    p = positions[0]
                    side = 'sell' if p['side'].lower() in ('long', 'buy') else 'buy'
                    await asyncio.to_thread(self.nx.exchange.create_market_order, symbol, side, abs(float(p['contracts'])), None, {'reduceOnly': True})
                    self.logger.info(f"✅ Ratchet: Closed {symbol}")
                except Exception as ex:
                    self.logger.error(f"❌ Failed to liquidate {symbol} during ratchet: {ex}")
            
        finally:
            self.is_liquidating = False

    def is_locked(self) -> bool:
        """Check if trading is currently blocked by the ratchet liquidation or cooldown"""
        # 1. Active liquidation check
        if self.is_liquidating:
            return True
            
        # 2. Cooldown check
        try:
            cooldown_val = self.db.get_setting('portfolio_ratchet_cooldown_until')
            if cooldown_val:
                until = datetime.fromisoformat(cooldown_val)
                if datetime.utcnow() < until:
                    return True
        except:
            pass
            
        return False
