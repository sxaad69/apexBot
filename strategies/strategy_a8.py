"""
Strategy A8: Ignition-Confirm-Size (composite moonshot hunter)

Chains A6's real-time orderbook read and A7's 5m acceleration by data availability,
not by which strategy owns them:

  Layer 1 — IGNITION (real-time, no candle close):
      Raw orderbook imbalance (signed) OR whale net-pressure, WITHOUT the
      ADX/EMA200/regime gates that make A6's entry lag. A coin is "ignited"
      when |imbalance| >= ignition_threshold OR whale flow is directionally aligned.

  Layer 2 — CONFIRMATION (5m candle close):
      A7's validated logic: volume_ratio >= 1.5x AND |bar_move| >= 0.5%.
      Entry fires ONLY if the coin was ignited AND the 5m acceleration confirms.

  Layer 3 — SIZING / RISK (A6 gates repurposed, NOT entry-blocking):
      ADX <25       -> size down (not block)
      below EMA200  -> size down (not block)
      volatile      -> size down (not block)
      whale conflict-> HARD BLOCK (legitimate contradiction)
"""

import pandas as pd
from typing import Dict, Optional
from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA8(BaseStrategy):
    """Ignition-Confirm-Size composite strategy (A6 orderbook + A7 5m acceleration)."""

    def __init__(self, config, logger):
        super().__init__(config, logger, "A8: Ignition-Confirm-Size")

        # Layer 1 — ignition threshold (placeholder, calibrate from enrichment data)
        self.ignition_threshold = getattr(config, 'A8_IGNITION_THRESHOLD', 0.40)
        self.min_whale_value = 150000  # same as A6 production

        # Layer 2 — A7 confirmation thresholds (A7's validated edge, untouched)
        self.volume_spike_mult = 1.5
        self.min_move_pct = 0.5
        self.lookback = 20
        self.timeframe = '5m'
        self.min_candles = 40

        # Layer 3 — sizing gates
        self.adx_min_size = 25     # below this -> size down
        self.atr_sl_mult = 3.0
        self.atr_tp_mult = 3.0

        self.filters = get_strategy_filters(config)

        # Indicator memoization (same pattern as A6/A7): 5m candle frame is
        # cached for TTL 300s while sweeps run ~30-60s. Keyed on (symbol,
        # last_ts, close, volume).
        import threading
        self._indicator_cache = {}
        self._indicator_cache_lock = threading.Lock()

        self.logger.info(f"Strategy A8 initialized: Ignition(>= {self.ignition_threshold*100:.0f}% imb) → A7 confirm (vol x{self.volume_spike_mult} + {self.min_move_pct}%) → sizing")

    # =====================================================================
    # Layer 1 — Ignition (real-time orderbook + whale, no lagging gates)
    # =====================================================================
    def _ignition_signal(self, symbol: str) -> Dict:
        """Return ignition state from raw orderbook + whale flow (no ADX/EMA200/regime gates).

        Reads the shared real-time orderbook feed published by A6 to the engine
        (logger.engine.latest_orderbooks), so A8 and A6 see the same WSS book.
        """
        orderbook = None
        try:
            if hasattr(self.logger, 'engine') and hasattr(self.logger.engine, 'latest_orderbooks'):
                orderbook = self.logger.engine.latest_orderbooks.get(symbol)
        except Exception:
            orderbook = None
        # Fallback to A6's private dict if engine publication not yet wired
        if orderbook is None and hasattr(self, 'latest_orderbooks'):
            orderbook = self.latest_orderbooks.get(symbol)

        bid_vol = ask_vol = 0.0
        imb = 0.0
        if orderbook and 'bids' in orderbook and 'asks' in orderbook:
            try:
                bid_vol = sum(price * amount for price, amount in orderbook['bids'][:50])
                ask_vol = sum(price * amount for price, amount in orderbook['asks'][:50])
                total = bid_vol + ask_vol
                imb = (bid_vol - ask_vol) / total if total > 0 else 0.0
            except Exception:
                pass

        whale = {'count': 0, 'net_pressure': 0, 'total_value': 0}
        try:
            if hasattr(self, 'exchange_client') and self.exchange_client:
                whale = self.detect_whales(symbol)
        except Exception:
            pass

        ignited = (abs(imb) >= self.ignition_threshold) or (whale.get('net_pressure', 0) != 0 and whale.get('count', 0) > 0)
        return {'ignited': ignited, 'imbalance': imb, 'bid_depth': bid_vol,
                'ask_depth': ask_vol, 'whale': whale}

    # =====================================================================
    # Layer 2 — Confirmation (A7's 5m acceleration)
    # =====================================================================
    def _cached_indicators(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """Memoized wrapper around calculate_indicators (see A6 for rationale)."""
        if df is None or len(df) == 0:
            return df
        try:
            last = df.iloc[-1]
            cache_key = (symbol, df.index[-1], float(last['close']), float(last['volume']))
            with self._indicator_cache_lock:
                cached = self._indicator_cache.get(cache_key)
                if cached is not None:
                    return cached.copy()
        except Exception:
            cache_key = None

        df_ind = self.calculate_indicators(df)

        if cache_key is not None:
            with self._indicator_cache_lock:
                if len(self._indicator_cache) > 1200:
                    self._indicator_cache.clear()
                self._indicator_cache[cache_key] = df_ind.copy()
        return df_ind

    def _confirm_on_5m(self, symbol: str) -> Optional[Dict]:
        """Run A7's acceleration logic on the coin's own 5m frame. Returns signal or None."""
        try:
            if hasattr(self.logger, 'engine') and self.logger.engine:
                df5 = self.logger.engine.fetch_market_data(symbol, timeframe=self.timeframe)
            else:
                df5 = None
        except Exception:
            df5 = None
        if df5 is None or len(df5) < self.min_candles:
            return None

        df = self._cached_indicators(symbol, df5)
        current = df.iloc[-1]
        vol_ratio = float(current['volume_ratio'])
        bar_move = float(current['bar_move'])

        if vol_ratio < self.volume_spike_mult:
            return None
        if abs(bar_move) < self.min_move_pct:
            return None
        if bar_move < 0:
            return None  # long-only

        return {
            'volume_ratio': round(vol_ratio, 2),
            'bar_move': round(bar_move, 2),
            'price': float(current['close']),
            'atr': float(current['atr']) if 'atr' in df.columns else 0.0,
            'adx': float(current['adx']) if 'adx' in df.columns else 0.0,
            'ema200': float(current['ema_200']) if 'ema_200' in df.columns else float(current['close']),
        }

    # =====================================================================
    # Main signal: ignite → confirm → size
    # =====================================================================
    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT') -> Optional[Dict]:
        if not self.filters._check_stablecoin_filter(symbol):
            return self.set_rejection({"reason": "STABLECOIN_FILTER"})

        # Layer 1 — ignition
        ign = self._ignition_signal(symbol)
        if not ign['ignited']:
            return self.set_rejection({
                "reason": "NO_IGNITION",
                "imbalance": round(ign['imbalance'], 4),
                "whale_count": ign['whale']['count'],
                "whale_net_pressure": ign['whale']['net_pressure'],
            })

        # Layer 2 — A7 confirmation on 5m
        conf = self._confirm_on_5m(symbol)
        if conf is None:
            return self.set_rejection({
                "reason": "NO_CONFIRM",
                "imbalance": round(ign['imbalance'], 4),
                "whale_count": ign['whale']['count'],
                "whale_net_pressure": ign['whale']['net_pressure'],
            })

        current_price = conf['price']
        side = 'buy'

        # Layer 3 — whale conflict HARD BLOCK
        if ign['whale']['count'] >= 2:
            whale_bias = 'buy' if ign['whale']['net_pressure'] > 0 else 'sell'
            if whale_bias != side:
                return self.set_rejection({
                    "reason": "WHALE_CONFLICT",
                    "whale_bias": whale_bias, "ob_side": side,
                    "imbalance": round(ign['imbalance'], 4),
                })

        # Layer 3 — sizing gates (modifiers, not blocks)
        size_multiplier = 1.0
        if conf['adx'] < self.adx_min_size:
            size_multiplier = 0.5  # weak trend -> half size
        if current_price < conf['ema200']:
            size_multiplier = min(size_multiplier, 0.5)  # below EMA200 -> half size

        # ATR-based stops
        stop_loss, take_profit = self.get_dynamic_stops(df, side, self.atr_sl_mult, self.atr_tp_mult)
        atr = conf['atr']

        confidence = 0.50
        confidence += min(abs(conf['bar_move']) * 0.10, 0.15)
        confidence += min(conf['volume_ratio'] * 0.10, 0.15)
        if ign['whale']['count'] > 0:
            confidence += min(ign['whale']['count'] * 0.05, 0.10)
        confidence = min(confidence, 1.0)

        self.logger.info(
            f"[{self.name}] {symbol} IGNITE+CONFIRM: imb {ign['imbalance']*100:.1f}% whale {ign['whale']['count']} "
            f"| vol x{conf['volume_ratio']:.1f} move {conf['bar_move']:+.2f}% | size x{size_multiplier} | Conf {confidence:.2f}"
        )

        return {
            'symbol': symbol,
            'side': side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'size_multiplier': size_multiplier,
            'indicators': {
                'imbalance': round(ign['imbalance'], 4),
                'whale_count': ign['whale']['count'],
                'whale_net_pressure': ign['whale']['net_pressure'],
                'volume_ratio': conf['volume_ratio'],
                'bar_move': conf['bar_move'],
                'atr': round(atr, 8),
                'adx': conf['adx'],
            }
        }

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Same indicator set as A7 (volume_ratio, bar_move, ATR, ADX, EMA200)."""
        df = df.copy()
        if len(df) >= self.lookback + 1:
            df['volume_avg_20'] = df['volume'].rolling(self.lookback).mean()
            df['volume_ratio'] = df['volume'] / df['volume_avg_20']
        else:
            df['volume_ratio'] = 1.0
        df['bar_move'] = (df['close'] - df['open']) / df['open'] * 100
        df = self.calculate_atr(df)
        try:
            df = self.calculate_adx(df)
        except Exception:
            df['adx'] = 0.0
        if len(df) >= 200:
            df['ema_200'] = df['close'].ewm(span=200, adjust=False).mean()
        else:
            df['ema_200'] = df['close'].ewm(span=len(df), adjust=False).mean()
        return df
