"""
Strategy A6: Institutional Edge (Orderbook Imbalance)
Professional strategy using direct Orderbook Tape extraction to fade retail flow.
"""

import pandas as pd
from typing import Dict, Optional
import ccxt.pro
import asyncio
import threading

from .base_strategy import BaseStrategy
from .filters import get_strategy_filters

class StrategyA6(BaseStrategy):
    """
    Orderbook Imbalance Strategy (FOR LIVE TRADING)
    - Fetches Level 2 Orderbook via WebSockets (ccxt.pro)
    - Instantly reacts to $500k+ spoofed/real Market Maker walls
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A6: Orderbook WSS")

        self.testing_mode = getattr(config, 'TESTING_MODE', False)
        
        self.imbalance_threshold = 0.40  
        self.atr_sl_mult = 1.0
        self.atr_tp_mult = 3.0
        self.filters = get_strategy_filters(config)
        
        # WSS Memory tracking
        self.latest_orderbooks = {}
        
        # Launch dedicated WSS Thread strictly for A6
        self.wss_thread = threading.Thread(target=self._start_wss_loop, daemon=True)
        self.wss_thread.start()

        self.logger.info("Strategy A6 (WebSockets) initialized in background thread.")

    def _start_wss_loop(self):
        """Creates the event loop for the async ccxt.pro stream."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._watch_orderbooks())
        
    async def _watch_orderbooks(self):
        """Asynchronous stream that instantly updates memory dict."""
        exchange = ccxt.pro.binance({
            'enableRateLimit': True,
        })
        
        # Determine which markets to track from Config
        pairs = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])
        if isinstance(pairs, str) and pairs.lower() == 'auto':
            pairs = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT']
            
        while True:
            try:
                # We can subscribe to multiple at once, but processing them individually here
                # is safer for early CCXT.pro implementation
                for symbol in pairs:
                    book = await exchange.watch_order_book(symbol, limit=50)
                    self.latest_orderbooks[symbol] = book
            except Exception as e:
                self.logger.error(f"[A6 WSS] WebSocket Error: {e}")
                await asyncio.sleep(5) # Cooldown on failure

    def fetch_imbalance(self, symbol: str) -> float:
        """Calculate math from instant WSS memory dictionary."""
        try:
            orderbook = self.latest_orderbooks.get(symbol)
            if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
                return 0.0

            # Raw Volume in Base
            bid_vol = sum([price * amount for price, amount in orderbook['bids'][:50]])
            ask_vol = sum([price * amount for price, amount in orderbook['asks'][:50]])
            
            total_vol = bid_vol + ask_vol
            if total_vol == 0:
                return 0.0
                
            imbalance = (bid_vol - ask_vol) / total_vol
            return imbalance

        except Exception as e:
            self.logger.debug(f"[{self.name}] Orderbook WSS Parse failed: {e}")
            return 0.0

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """A6 does not use lagging indicators. Returning unmodified df."""
        return df

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Query the instant WSS Memory dict to find spoofed walls.
        """
        if len(df) < 20: return None

        should_trade, reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade: return None

        # Fetch instantly from local RAM (no Binance HTTP Ping needed)
        imbalance = self.fetch_imbalance(symbol)
        
        side = None
        if imbalance >= self.imbalance_threshold:
            side = 'buy'
            self.logger.info(f"[{self.name}] {symbol} MASSIVE BID WALL DETECTED! Imbalance: +{imbalance*100:.1f}%")
        elif imbalance <= -self.imbalance_threshold:
            side = 'sell'
            self.logger.info(f"[{self.name}] {symbol} MASSIVE ASK WALL DETECTED! Imbalance: {imbalance*100:.1f}%")
            
        if not side: return None

        df_atr = self.calculate_atr(df)
        current_price = df_atr['close'].iloc[-1]
        atr = df_atr['atr'].iloc[-1] if 'atr' in df_atr.columns else current_price * 0.01

        if side == 'buy':
            stop_loss = current_price - (atr * self.atr_sl_mult)
            take_profit = current_price + (atr * self.atr_tp_mult)
        else:
            stop_loss = current_price + (atr * self.atr_sl_mult)
            take_profit = current_price - (atr * self.atr_tp_mult)

        confidence = min(1.0, 0.70 + (abs(imbalance) - 0.40)) 

        return {
            'side': side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence
        }
