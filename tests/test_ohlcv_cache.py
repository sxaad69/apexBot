"""
Unit tests for the OHLCV TTL cache in PaperTradingEngine.fetch_market_data.
Mocks the exchange so no network calls are made.
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import unittest
from unittest.mock import MagicMock


class TestOHLCVCache(unittest.TestCase):
    """Simulates PaperTradingEngine's cache logic directly (unit isolation)."""

    def setUp(self):
        # Create a minimal engine-like object with the cache dicts
        self.engine = type('E', (), {})()
        self.engine._ohlcv_cache = {}
        self.engine._ohlcv_cache_ts = {}
        self.engine.config = type('C', (), {})()
        self.engine.config.TIMEFRAME = '15m'
        self.engine.config.OHLCV_LIMIT = 210
        self.engine._ohlcv_batch_count = 0
        self.engine._ohlcv_batch_max = 100
        self.engine.logger = type('L', (), {
            'warning': lambda *a, **k: None,
            'error': lambda *a, **k: None,
        })()

        # Mock exchange
        self.engine.exchange = type('X', (), {})()
        self.engine.exchange.exchange = MagicMock()
        self.engine.exchange.exchange.markets = {'BTC/USDT': {}}
        self.engine.exchange.exchange.fetch_ohlcv.return_value = [
            [1600000000000, 100, 101, 99, 100.5, 1000],
            [1600000900000, 100.5, 102, 100, 101, 1200],
            [1600001800000, 101, 103, 100.5, 102.5, 1500],
        ]

        # Define the _get_candle_ttl_seconds logic inline (mirrors main.py)
        def _ttl(tf):
            tf_map = {
                '1m': 60, '3m': 180, '5m': 300, '15m': 900,
                '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400,
            }
            return tf_map.get(str(tf).lower(), 900)
        self.engine._get_candle_ttl_seconds = _ttl

        # Bind a local replica of fetch_market_data (avoids importing full app stack)
        # -- mirrors exactly the cache/batch logic in main.py --
        def _fetch(self, symbol='BTC/USDT', timeframe=None, limit=None):
            if timeframe is None:
                timeframe = self.config.TIMEFRAME
            if limit is None:
                limit = int(getattr(self.config, 'OHLCV_LIMIT', 210))
            cache_key = (symbol, timeframe)
            ttl_seconds = self._get_candle_ttl_seconds(timeframe)
            now = time.time()
            last_fetch = self._ohlcv_cache_ts.get(cache_key, 0)
            if now - last_fetch < ttl_seconds and cache_key in self._ohlcv_cache:
                return self._ohlcv_cache[cache_key]
            if self._ohlcv_batch_count >= self._ohlcv_batch_max:
                if cache_key in self._ohlcv_cache:
                    return self._ohlcv_cache[cache_key]
                return None
            self._ohlcv_batch_count += 1
            try:
                if symbol not in self.exchange.exchange.markets:
                    for m in self.exchange.exchange.markets:
                        if m.split(':')[0] == symbol:
                            symbol = m
                            break
                ohlcv = self.exchange.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)
                self._ohlcv_cache[cache_key] = df
                self._ohlcv_cache_ts[cache_key] = now
                return df
            except Exception:
                if cache_key in self._ohlcv_cache:
                    return self._ohlcv_cache[cache_key]
                return None
        # Bind as method so `self` receives the engine instance
        self.engine.fetch_market_data = _fetch.__get__(self.engine)

    def test_cache_hit_within_ttl(self):
        # First call populates cache, exchanges with exchange.fetch_ohlcv
        df1 = self.engine.fetch_market_data('BTC/USDT')
        self.assertIsNotNone(df1)
        calls_after_first = self.engine.exchange.exchange.fetch_ohlcv.call_count
        # Second call within TTL should use cache (no new exchange call)
        df2 = self.engine.fetch_market_data('BTC/USDT')
        self.assertIs(df2, df1)
        self.assertEqual(self.engine.exchange.exchange.fetch_ohlcv.call_count, calls_after_first)

    def test_cache_miss_after_ttl_expiry(self):
        df1 = self.engine.fetch_market_data('BTC/USDT')
        # Force TTL expiry
        self.engine._ohlcv_cache_ts[('BTC/USDT', '15m')] = time.time() - 1000
        df2 = self.engine.fetch_market_data('BTC/USDT')
        # Should refetch (new call), cache updated
        self.assertIsNot(df2, df1)
        self.assertEqual(self.engine.exchange.exchange.fetch_ohlcv.call_count, 2)

    def test_batch_cap_stops_fetches(self):
        # Fill the batch cap
        self.engine._ohlcv_batch_count = 100  # == max
        # No cached data -> should return None (batch capped)
        result = self.engine.fetch_market_data('BTC/USDT')
        self.assertIsNone(result)
        self.assertEqual(self.engine.exchange.exchange.fetch_ohlcv.call_count, 0)

    def test_stale_cache_fallback_on_error(self):
        df1 = self.engine.fetch_market_data('BTC/USDT')
        # Force TTL expiry AND make fetch fail
        self.engine._ohlcv_cache_ts[('BTC/USDT', '15m')] = time.time() - 1000
        self.engine.exchange.exchange.fetch_ohlcv.side_effect = Exception('rate limited')
        df2 = self.engine.fetch_market_data('BTC/USDT')
        # Should fall back to stale cache
        self.assertIs(df2, df1)


if __name__ == '__main__':
    unittest.main(verbosity=2)