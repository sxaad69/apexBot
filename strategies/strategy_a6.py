"""
Strategy A6: Institutional Edge (Orderbook Imbalance)
Professional strategy using direct Orderbook Tape extraction to fade retail flow.

V2: Upgraded with A5-inspired multi-factor confirmation:
  - Minimum 30% orderbook imbalance (real institutional walls only)
  - Whale confirmation layer ($5k+ trades on testnet, $50k+ on production)
  - Market regime filter (blocks entries in volatile/unknown conditions)
  - Session-aware confidence floors (75% base, 85% during US peak)
  - 2.0x ATR stop loss (prevents being stopped out by normal candle noise)
"""

import pandas as pd
import numpy as np
from typing import Dict, Optional
from datetime import datetime
import ccxt.pro
import ccxt
import asyncio
import threading
import os
import time as time_module

from .base_strategy import BaseStrategy
from .filters import get_strategy_filters

class StrategyA6(BaseStrategy):
    """
    Orderbook Imbalance Strategy (FOR LIVE TRADING)
    - Fetches Level 2 Orderbook via WebSockets (ccxt.pro) for speed
    - Requires 30%+ imbalance (institutional walls only, not noise)
    - Multi-factor confirmation: Orderbook + Whale + Regime + Session
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A6: Orderbook WSS")

        self.testing_mode = getattr(config, 'TESTING_MODE', False)

        # --- Core Configuration (A5-inspired thresholds) ---
        # Imbalance threshold: 40% is a real institutional wall. 15% is too noisy.
        self.imbalance_threshold = 0.25 if self.testing_mode else 0.40

        # Whale confirmation: require recent large trades confirming direction
        self.min_whale_value = 5000 if self.testing_mode else 75000

        # ATR multipliers: 2.5x stop gives more room vs 2.0x which was being suffocated
        self.atr_sl_mult = 2.5
        self.atr_tp_mult = 5.0

        # Session-based confidence floors (UTC hours)
        self.sessions = {
            'asia':    {'start': 0,  'end': 8,  'confidence_floor': 0.75, 'confidence_boost': 0.00},
            'europe':  {'start': 8,  'end': 14, 'confidence_floor': 0.75, 'confidence_boost': 0.05},
            'us_peak': {'start': 14, 'end': 21, 'confidence_floor': 0.85, 'confidence_boost': 0.10},
            'us_late': {'start': 21, 'end': 24, 'confidence_floor': 0.75, 'confidence_boost': 0.00},
        }

        self.min_order_value = float(os.getenv("MIN_FUTURES_ORDER_VALUE", 100))
        self.filters = get_strategy_filters(config)

        # WSS Memory tracking (speed advantage over A5's REST calls)
        self.latest_orderbooks = {}

        # Launch dedicated WSS Thread strictly for A6
        self.wss_thread = threading.Thread(target=self._start_wss_loop, daemon=True)
        self.wss_thread.start()

        self.logger.info("Strategy A6 (WebSockets V2) initialized — Multi-factor confirmation active.")
        self.logger.info(f"Imbalance threshold: {self.imbalance_threshold*100:.0f}% | ATR SL: {self.atr_sl_mult}x | Whale min: ${self.min_whale_value:,}")

    # =========================================================================
    # WebSocket Orderbook Streaming
    # =========================================================================

    def _start_wss_loop(self):
        """Creates the event loop for the async ccxt.pro stream."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._watch_orderbooks())

    async def _watch_single_symbol(self, exchange, symbol: str):
        """Dedicated coroutine that continuously streams one symbol's orderbook."""
        while True:
            try:
                book = await exchange.watch_order_book(symbol, limit=50)
                self.latest_orderbooks[symbol] = book
            except Exception as e:
                self.logger.debug(f"[A6 WSS] {symbol} stream error: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def _watch_orderbooks(self):
        """Asynchronous stream: spawns one concurrent task per monitored symbol."""
        exchange = ccxt.pro.binance({
            'enableRateLimit': True,
            'options': {'defaultType': 'future'}
        })
        if getattr(self.config, 'EXCHANGE_ENVIRONMENT', 'production').lower() == 'testnet':
            exchange.set_sandbox_mode(True)
            self.logger.info("[A6 WSS] Testnet Sandbox Mode Enabled")
        active_tasks = {}

        while True:
            try:
                if hasattr(self.logger, 'engine') and self.logger.engine:
                    pairs = list(self.logger.engine.top_pairs_cache or [])
                else:
                    pairs_config = getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT'])
                    if isinstance(pairs_config, str) and pairs_config.lower() == 'auto':
                        pairs = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
                    elif isinstance(pairs_config, str):
                        pairs = [p.strip() for p in pairs_config.split(',')]
                    else:
                        pairs = list(pairs_config)

                if not pairs:
                    await asyncio.sleep(10)
                    continue

                for sym in list(active_tasks.keys()):
                    if sym not in pairs:
                        active_tasks[sym].cancel()
                        del active_tasks[sym]
                        if sym in self.latest_orderbooks:
                            del self.latest_orderbooks[sym]

                for sym in pairs:
                    if sym not in active_tasks or active_tasks[sym].done():
                        task = asyncio.ensure_future(self._watch_single_symbol(exchange, sym))
                        active_tasks[sym] = task

                await asyncio.sleep(60)

            except Exception as e:
                self.logger.error(f"[A6 WSS] Supervisor error: {e}")
                await asyncio.sleep(5)

    # =========================================================================
    # Signal Quality Layers (A5-inspired)
    # =========================================================================

    def fetch_imbalance(self, symbol: str) -> float:
        """Calculate imbalance from instant WSS memory dictionary."""
        try:
            orderbook = self.latest_orderbooks.get(symbol)
            if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
                return 0.0

            bid_vol = sum([price * amount for price, amount in orderbook['bids'][:50]])
            ask_vol = sum([price * amount for price, amount in orderbook['asks'][:50]])

            total_vol = bid_vol + ask_vol
            if total_vol == 0:
                return 0.0

            return (bid_vol - ask_vol) / total_vol

        except Exception as e:
            self.logger.debug(f"[{self.name}] Orderbook parse failed: {e}")
            return 0.0

    def detect_whales(self, symbol: str) -> Dict:
        """Detect large institutional trades in the last 5 minutes (REST fallback)."""
        try:
            exchange_class = getattr(ccxt, getattr(self.config, 'FUTURES_EXCHANGE', 'binance').lower())
            exchange = exchange_class({'options': {'defaultType': 'future'}})
            if getattr(self.config, 'EXCHANGE_ENVIRONMENT', 'production').lower() == 'testnet':
                exchange.set_sandbox_mode(True)

            recent_trades = exchange.fetch_trades(symbol, limit=100)
            if not recent_trades:
                return {'count': 0, 'buy_pressure': 0, 'sell_pressure': 0, 'net_pressure': 0, 'total_value': 0}

            current_time = time_module.time() * 1000
            five_min_ago = current_time - (5 * 60 * 1000)

            whale_trades = []
            for trade in recent_trades:
                if trade['timestamp'] > five_min_ago:
                    value = trade['price'] * trade['amount']
                    if value >= self.min_whale_value:
                        whale_trades.append({'side': trade['side'], 'value': value})

            buy_whales = [w for w in whale_trades if w['side'] == 'buy']
            sell_whales = [w for w in whale_trades if w['side'] == 'sell']

            return {
                'count': len(whale_trades),
                'buy_pressure': len(buy_whales),
                'sell_pressure': len(sell_whales),
                'net_pressure': len(buy_whales) - len(sell_whales),
                'total_value': sum(w['value'] for w in whale_trades)
            }

        except Exception as e:
            self.logger.debug(f"[A6] Whale detection failed for {symbol}: {e}")
            return {'count': 0, 'buy_pressure': 0, 'sell_pressure': 0, 'net_pressure': 0, 'total_value': 0}

    def get_market_regime(self, df: pd.DataFrame) -> str:
        """Determine market regime: trending, ranging, volatile, or unknown."""
        if len(df) < 20:
            return 'unknown'
        try:
            adx_series = self.calculate_adx(df)
            adx = adx_series['adx'].iloc[-1] if isinstance(adx_series, pd.DataFrame) and 'adx' in adx_series.columns else df.get('adx', pd.Series([0])).iloc[-1]

            returns = df['close'].pct_change().dropna()
            volatility = returns.tail(20).std() * 100

            if adx > 30 and volatility < 3:
                return 'strong_trend'
            elif adx > 25 and volatility < 4:
                return 'moderate_trend'
            elif adx < 15 and volatility < 2:
                return 'ranging'
            elif volatility > 5:
                return 'volatile'
            else:
                return 'normal'
        except Exception as e:
            self.logger.debug(f"[A6] Regime analysis failed: {e}")
            return 'unknown'

    def get_current_session(self) -> tuple:
        """Get current trading session name and confidence boost."""
        current_hour = datetime.utcnow().hour
        for name, data in self.sessions.items():
            if data['start'] <= current_hour < data['end']:
                return name, data['confidence_boost'], data['confidence_floor']
        return 'unknown', 0.0, 0.75

    # =========================================================================
    # Indicator Calculation
    # =========================================================================

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators."""
        df = df.copy()
        df = self.calculate_adx(df)
        df = self.calculate_atr(df)
        
        # Calculate EMA 200 for major trend filter
        if len(df) >= 200:
            df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        else:
            # Fallback if not enough data for 200 EMA
            df['ema_200'] = df['close'].ewm(span=len(df), adjust=False).mean()
            
        if len(df) >= 20:
            df['volume_avg_20'] = df['volume'].rolling(20).mean()
            df['volume_ratio'] = df['volume'] / df['volume_avg_20']
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(20).std() * 100
        return df

    # =========================================================================
    # Signal Generation
    # =========================================================================

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        """
        Multi-factor institutional signal generator.
        Requires confluence of: WSS Imbalance + Whale Flow + Market Regime + Session.
        """
        if len(df) < 25:
            return self.set_rejection("INSUFFICIENT_DATA")

        # 1. Stablecoin Filter
        if not self.filters._check_stablecoin_filter(symbol):
            return self.set_rejection("STABLECOIN_FILTER")

        # 2. Calculate indicators
        df = self.calculate_indicators(df)
        df = self.calculate_atr(df)

        # 3. Universal filters (volume, etc.)
        should_trade, filter_reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            self.log_strategy_skip(symbol, f"FILTER_{filter_reason.upper()}", {})
            return None

        # 4. ADX Trend Filter (require meaningful trend)
        adx_val = df['adx'].iloc[-1] if 'adx' in df.columns else 0
        if adx_val < 20:
            self.log_strategy_skip(symbol, "ADX_LOW", {"adx": round(adx_val, 2)})
            return None

        # 5. Market Regime Filter (skip volatile/unknown)
        regime = self.get_market_regime(df)
        if regime in ['volatile', 'unknown']:
            self.log_strategy_skip(symbol, "REGIME_BLOCKED", {"regime": regime})
            return None

        # 5b. Major Trend Filter (EMA 200) - PREVENT FIGHTING THE TREND
        current_price = df['close'].iloc[-1]
        ema_200 = df['ema_200'].iloc[-1]
        
        # We will determine the bias here to use later
        trend_bias = 'bullish' if current_price > ema_200 else 'bearish'

        # 6. Fetch WSS Imbalance (the speed advantage over A5)
        imbalance = self.fetch_imbalance(symbol)
        if imbalance == 0.0:
            self.logger.debug(f"[{self.name}] {symbol}: Waiting for WebSocket data...")
            return self.set_rejection("WAITING_FOR_WSS_DATA")

        if abs(imbalance) < self.imbalance_threshold:
            self.logger.debug(
                f"[{self.name}] {symbol} scanning... Imbalance: {imbalance*100:.1f}% "
                f"(Threshold: {self.imbalance_threshold*100:.1f}%)"
            )
            return self.set_rejection("LOW_IMBALANCE")

        # 7. Whale Confirmation
        whale_data = self.detect_whales(symbol)

        # 8. Session Awareness
        session_name, confidence_boost, confidence_floor = self.get_current_session()

        # 9. Multi-Factor Confidence Score
        confidence = 0.50  # Base

        # Orderbook imbalance contribution (up to 25%)
        confidence += abs(imbalance) * 0.25

        # Volume contribution (up to 10%)
        volume_ratio = df.get('volume_ratio', pd.Series([1])).iloc[-1]
        if volume_ratio > 1.2:
            confidence += 0.10

        # Whale confirmation (up to 15%)
        if whale_data['count'] > 0:
            whale_score = min(whale_data['count'] * 0.05, 0.15)
            confidence += whale_score

        # Market regime bonus (up to 10%)
        if regime in ['strong_trend', 'moderate_trend']:
            confidence += 0.10

        # Session boost
        confidence += confidence_boost

        # 10. Enforce session-specific confidence floor
        if confidence < confidence_floor:
            self.log_strategy_skip(
                symbol, "CONFIDENCE_LOW",
                {"confidence": round(confidence, 3), "floor": confidence_floor, "session": session_name}
            )
            return None

        # 11. Determine direction with TREND FILTER
        side = None
        if imbalance >= self.imbalance_threshold:
            if trend_bias != 'bullish':
                return self.set_rejection("TREND_MISMATCH_BEARISH")
            side = 'buy'
            self.logger.info(f"[{self.name}] {symbol} MASSIVE BID WALL: +{imbalance*100:.1f}% | Conf: {confidence:.2f} | Regime: {regime}")
        elif imbalance <= -self.imbalance_threshold:
            if trend_bias != 'bearish':
                return self.set_rejection("TREND_MISMATCH_BULLISH")
            side = 'sell'
            self.logger.info(f"[{self.name}] {symbol} MASSIVE ASK WALL: {imbalance*100:.1f}% | Conf: {confidence:.2f} | Regime: {regime}")

        if not side:
            return self.set_rejection("NO_CLEAR_SIGNAL_SIDE")

        # 12. Whale vs. Imbalance Conflict Check
        if whale_data['count'] >= 2:
            whale_bias = 'buy' if whale_data['net_pressure'] > 0 else 'sell'
            if side != whale_bias:
                self.log_strategy_skip(symbol, "WHALE_CONFLICT", {
                    "ob_side": side, "whale_bias": whale_bias,
                    "net_pressure": whale_data['net_pressure']
                })
                return None

        # 13. ATR-Based Dynamic Stops (2.0x for breathing room)
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)
        current_price = df['close'].iloc[-1]
        atr = df['atr'].iloc[-1] if 'atr' in df.columns else current_price * 0.01

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': min(confidence, 1.0),
            'strategy': self.name,
            'session': session_name,
            'regime': regime,
            'indicators': {
                'imbalance': round(imbalance, 4),
                'atr': round(atr, 8),
                'adx': round(adx_val, 2),
                'whale_count': whale_data['count'],
                'whale_net_pressure': whale_data['net_pressure'],
            }
        }
