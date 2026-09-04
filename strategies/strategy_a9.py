"""
Strategy A9: Volume-Momentum Grinder Hunter (Phase 2 — live, candle-only)

Cracks the class of day-top-movers A6 is STRUCTURALLY blind to. Forensics on
mainnet sweep data (Aug 20-27): the top-20 gainers (BTR +469%, PROM +143%,
ONG +113%, STX +98%...) were scanned and rejected ~5,000x EACH with
LOW_IMBALANCE because grinders NEVER form a 65% orderbook wall. 19 of 20 top
movers were invisible to A6 by construction.

Core idea — detection by candle ACCELERATION, not orderbook depth:
  - 15m volume ratio >= 1.5x the coin's own 20-bar average (relative spike,
    NOT the $10k absolute floor that kills microcap runners early)
  - >= 0.5% directional move on the confirming bar
  - ADX >= 25 (trending) and price above EMA-200 (confirmed uptrend)
  - NO orderbook wall required -> grinder-visible

Why this is NOT a repeat of A7's testnet bleed:
  A7's live testnet failure (22% win, -$77) was hindsight bias + testnet's thin
  orderbook inflating signals that don't exist on production. A9 uses CANDLE
  DATA ONLY (volume/price), which is identical on testnet and mainnet — the
  distortion that killed A7 cannot apply.

Why this complements A6 rather than competing:
  A6 owns the wall class (instant imbalance spikes). A9 owns the grinder class
  (multi-bar volume/momentum grind). Both run in the same sweep loop, share the
  same risk/entry/exchange-side SL stack, and share the 15-position cap.

Nothing in this file touches the shared entry/exit/risk machinery — the whole
file is strategy scope, so `git revert` of the Phase-2 commit can never damage
core behavior.
"""

from typing import Dict, Optional

import time

import pandas as pd

from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA9(BaseStrategy):
    """Volume-Momentum Grinder Hunter (long-only, 15m candle acceleration)."""

    def __init__(self, config, logger):
        super().__init__(config, logger, "A9: Volume-Momentum Grinder")

        # Signal thresholds (environment-overridable for live tuning without a commit)
        self.volume_spike_mult = float(getattr(config, 'A9_VOLUME_SPIKE_MULT', 1.5))
        self.min_move_pct = float(getattr(config, 'A9_MIN_MOVE_PCT', 0.5))
        self.adx_min = float(getattr(config, 'A9_ADX_MIN', 25.0))
        self.lookback = 20

        # ATR stops: 3.0x ATR gives the vol-sync layer room; the Phase-1
        # MIN_RAW_STOP_PERCENT floor catches any coin whose ATR is too tight.
        self.atr_sl_mult = 3.0
        self.atr_tp_mult = 3.0  # trailing TP handles exits

        # Operates on the SHARED 15m df passed into generate_signal (no
        # independent fetch -> zero added rate-limit weight per sweep).
        self.timeframe = '15m'
        self.min_candles = 40

        self.filters = get_strategy_filters(config)

        # Indicator memoization on (symbol, last_ts, close, volume) — same
        # pattern as A6/A7/A8; the 15m frame is cached by its candle TTL.
        import threading
        self._indicator_cache = {}
        self._indicator_cache_lock = threading.Lock()

        # --- Replay-validated gates (2026-09-05, see AGENTS.md) ---
        # Every gate below was measured on the real-fill 1m replay of 640
        # September paper signals before being coded.
        self.skip_conf_band = bool(getattr(config, 'A9_SKIP_CONF_BAND', True))
        self.max_extension_pct = float(getattr(config, 'A9_MAX_EXTENSION_PCT', 8.0))
        self.brake_enabled = bool(getattr(config, 'A9_BRAKE_ENABLED', True))
        self.brake_window = int(getattr(config, 'A9_BRAKE_WINDOW', 20))
        self.brake_min_winrate = float(getattr(config, 'A9_BRAKE_MIN_WINRATE', 35.0))
        self._brake_ts = 0.0
        self._brake_cached = False

        self.logger.info(
            f"Strategy A9 initialized: Grinder (vol x{self.volume_spike_mult}, "
            f"move >= {self.min_move_pct}%, ADX >= {self.adx_min:.0f}, above EMA200)"
        )

    def _cached_indicators(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
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

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """volume_ratio, bar_move, ATR, ADX, EMA-200 on the shared 15m frame."""
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

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT', market_type: str = 'futures') -> Optional[Dict]:
        if market_type != 'futures':
            return None
        if df is None or len(df) < self.min_candles:
            return self.set_rejection("INSUFFICIENT_DATA")

        df = self._cached_indicators(symbol, df)

        # Stablecoin filter
        if not self.filters._check_stablecoin_filter(symbol):
            return self.set_rejection("STABLECOIN_FILTER")

        # Absolute volume floor with relative-escape (Phase-1 fix in filters.py)
        # keeps microcap runners visible while blocking dead coins.
        if not self.filters._check_minimum_volume(df):
            return self.set_rejection("VOLUME_FLOOR")

        current = df.iloc[-1]
        vol_ratio = float(current['volume_ratio'])
        bar_move = float(current['bar_move'])
        adx_val = round(float(current['adx']) if 'adx' in df.columns else 0.0, 2)
        atr_val = round(float(current['atr']) if 'atr' in df.columns else 0.0, 8)
        price_now = float(current['close'])
        ema200_now = float(current['ema_200']) if 'ema_200' in df.columns else price_now

        def _common(reason, **extra):
            d = {
                "reason": reason,
                "volume_ratio": round(vol_ratio, 2),
                "bar_move": round(bar_move, 2),
                "price": round(price_now, 8),
                "atr": atr_val,
                "adx": adx_val,
                "ema200_distance": round((price_now - ema200_now) / ema200_now, 4),
            }
            d.update(extra)
            return self.set_rejection(d)

        # Core acceleration signal
        if vol_ratio < self.volume_spike_mult:
            return _common("LOW_VOLUME_RATIO", required=self.volume_spike_mult)

        if abs(bar_move) < self.min_move_pct:
            return _common("LOW_MOMENTUM", required=self.min_move_pct)

        # Long-only: negative momentum with high volume = distribution, not a setup
        if bar_move < 0:
            return _common("NEGATIVE_MOMENTUM")

        # Trend confluence: trending + above the long-term mean
        if adx_val < self.adx_min:
            return _common("ADX_LOW", required=round(self.adx_min, 2))
        if price_now < ema200_now:
            return _common("BELOW_EMA200", ema200=round(ema200_now, 8))

        stop_loss, take_profit = self.get_dynamic_stops(df, 'buy', self.atr_sl_mult, self.atr_tp_mult)
        atr = df['atr'].iloc[-1] if 'atr' in df.columns else price_now * 0.01

        confidence = 0.50
        confidence += min(vol_ratio * 0.10, 0.20)      # volume contribution (up to 20%)
        confidence += min(abs(bar_move) * 0.10, 0.15)  # momentum contribution (up to 15%)
        confidence = min(confidence, 1.0)

        # 1. Toxic confidence band: 0.80-0.85 is this formula's high end (extended
        #    moves) and lost -0.38%/trade on the real-fill replay; <0.80 and >=0.85
        #    were both positive there.
        if self.skip_conf_band and 0.80 <= confidence < 0.85:
            self.log_strategy_skip(symbol, "CONF_BAND_EXCLUDED", {"confidence": round(confidence, 3)})
            return None

        # 2. Late-entry filter: buying a move already extended off its 12h low is
        #    buying exhaustion (the CATI-loser/Q/XAN September pattern — 4 of 8
        #    losers). 48 x 15m bars = 12h lookback.
        if self.max_extension_pct > 0:
            try:
                recent_low = float(df['low'].iloc[-48:].min())
                extension = (price_now - recent_low) / max(recent_low, 1e-12) * 100
                if extension > self.max_extension_pct:
                    self.log_strategy_skip(symbol, "EXTENSION_HIGH",
                                           {"extension_pct": round(extension, 2), "max": self.max_extension_pct})
                    return None
            except Exception:
                pass

        # 3. Cohort reversal brake: pause new entries when this strategy's own
        #    recent outcomes collapse (Sep 4: 33% win day, -135 paper points).
        if self.brake_enabled and self._cohort_brake():
            self.log_strategy_skip(symbol, "REVERSAL_BRAKE",
                                   {"window": self.brake_window, "min_winrate": self.brake_min_winrate})
            return None

        self.logger.info(
            f"[{self.name}] {symbol} GRINDER: vol x{vol_ratio:.1f} | "
            f"move {bar_move:+.2f}% | ADX {adx_val:.0f} | Conf {confidence:.2f}"
        )

        return {
            'symbol': symbol,
            'side': 'buy',
            'entry_price': price_now,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'timeframe': self.timeframe,
            'regime': 'acceleration',
            'indicators': {
                'volume_ratio': round(vol_ratio, 2),
                'bar_move': round(bar_move, 2),
                'atr': round(float(atr), 8),
                'adx': adx_val,
                'ema200_distance': round((price_now - ema200_now) / ema200_now, 4),
            }
        }

    def _cohort_brake(self) -> bool:
        """True when the strategy's own last N resolved paper outcomes collapsed.

        While A9 is paper-only this reads paper_signals; the query is memoized
        for 5 minutes (fires ~150x/day, one indexed query per window). When A9
        eventually goes live this must switch to realized trade outcomes —
        flagged in AGENTS.md so the switch isn't forgotten.
        """
        now = time.time()
        if now - self._brake_ts < 300:
            return self._brake_cached
        val = False
        try:
            db = getattr(self.logger, 'db', None)
            if db is not None and hasattr(db, 'recent_paper_winrate'):
                wins, n = db.recent_paper_winrate('A9', self.brake_window)
                if n >= self.brake_window and (100.0 * wins / n) < self.brake_min_winrate:
                    val = True
                    self.logger.warning(
                        f"[{self.name}] REVERSAL BRAKE ACTIVE: last {n} outcomes "
                        f"{wins}/{n} wins ({100.0*wins/n:.0f}% < {self.brake_min_winrate:.0f}%) "
                        f"— new entries paused")
        except Exception as e:
            self.logger.debug(f"[{self.name}] brake check failed: {e}")
        self._brake_cached = val
        self._brake_ts = now
        return val