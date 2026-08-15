"""
Market Data Mixin — extracted from main.py PaperTradingEngine (pure move, no logic change).

Holds:
  - get_top_pairs_by_volume   (market discovery / universe)
  - _get_candle_ttl_seconds   (OHLCV cache TTL)
  - fetch_market_data         (OHLCV fetch + cache + batch rotation)

Mixin design: PaperTradingEngine inherits this, so all self.* references resolve
to the engine instance exactly as before. Method names/signatures are unchanged.
"""

import time

import pandas as pd


class MarketDataMixin:
    """Market discovery + OHLCV caching (moved from main.py, behavior-identical)."""

    def get_top_pairs_by_volume(self, top_n=None, min_volume_usdt=None):
        """
        Fetch top N trading pairs by 24h volume

        Args:
            top_n: Number of top pairs to return (defaults to config if None)
            min_volume_usdt: Minimum 24h volume in USDT (defaults to config if None)

        Returns:
            List of trading pair symbols
        """
        from datetime import datetime, timedelta

        # Update cache every 15 minutes (faster alpha discovery)
        now = datetime.now()
        
        # Use provided args or fall back to config
        if top_n is None:
            top_n = int(getattr(self.config, 'FUTURES_AUTO_TOP_N', 30))
        if min_volume_usdt is None:
            min_volume_usdt = float(getattr(self.config, 'FUTURES_AUTO_MIN_VOLUME', 1000000))

        if (self.last_pairs_update and
            now - self.last_pairs_update < timedelta(minutes=15) and
            self.top_pairs_cache):
            return self.top_pairs_cache

        try:
            self.logger.info("Fetching top trading pairs by volume...")

            # Fetch all tickers
            tickers = self.exchange.exchange.fetch_tickers()
            
            # Filter and sort by volume
            usdt_pairs = []
            markets = self.exchange.exchange.markets
            
            for symbol, ticker in tickers.items():
                clean_symbol = symbol.upper()
                if 'USDT' not in clean_symbol:
                    continue

                # PHASE 16: Ensure symbol exists in the Futures market (skip spot-only leakage)
                if markets and symbol in markets:
                    market_info = markets[symbol]
                    # Filter for linear futures (USDT settled)
                    is_futures = market_info.get('future', False) or market_info.get('swap', False)
                    if not is_futures:
                        continue
                        
                    # --- FIX: Filter out inactive/non-trading symbols ---
                    if not market_info.get('active', True):
                        continue
                        
                    # Binance-specific status check
                    if market_info.get('info', {}).get('status') and market_info['info']['status'] != 'TRADING':
                        continue
                        
                    # --- PHASE 2 FIX: Skip TradFi contracts requiring agreement ---
                    # Binance marks equity/commodity perps (META, LITE, XAU, etc.)
                    # as contractType='TRADIFI_PERPETUAL'. Trading them returns
                    # -4411 "you need to sign the agreement" errors. Filter out.
                    market_type = market_info.get('info', {}).get('contractType', '')
                    if market_type == 'TRADIFI_PERPETUAL':
                        continue

                # Skip specific non-trading symbols if necessary
                if any(x in clean_symbol for x in ['BUSD', 'EUR', 'GBP', 'AUD']):
                    continue

                # --- USER-DEFINED EXCLUSIONS (FUTURES_EXCLUDE_SYMBOLS) ---
                exclude_list = getattr(self.config, 'FUTURES_EXCLUDE_SYMBOLS', [])
                if exclude_list:
                    base = clean_symbol.split('/')[0].replace('USDT', '')
                    if base in exclude_list:
                        continue

                # Standard CCXT quoteVolume is preferred
                quote_volume = ticker.get('quoteVolume', 0)
                
                if not quote_volume and 'info' in ticker:
                    info = ticker['info']
                    for vol_key in ['quoteVolume', 'volume', 'vol', '24hVolume', 'quote_volume']:
                        if vol_key in info:
                            try:
                                quote_volume = float(info[vol_key])
                                if quote_volume > 0: break
                            except: continue

                if not quote_volume or quote_volume < min_volume_usdt:
                    continue

                usdt_pairs.append({
                    'symbol': symbol,
                    'volume': quote_volume
                })

            # Sort by volume (descending)
            usdt_pairs.sort(key=lambda x: x['volume'], reverse=True)

            # Get top N base list
            top_pairs = [p['symbol'] for p in usdt_pairs[:top_n]]

            # --- PHASE 26: Inject Open Positions into Monitoring Cycle ---
            try:
                if hasattr(self.logger, 'db'):
                    conn = self.logger.db._get_connection(self.logger.db.main_db)
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT DISTINCT symbol FROM trades WHERE status IN ('OPEN', 'PENDING_EXIT')")
                        must_monitor = [row['symbol'] for row in cursor.fetchall()]
                    finally:
                        conn.close()
                    
                    if must_monitor:
                        # Merge and deduplicate
                        combined = list(set(top_pairs + must_monitor))
                        # Keep original volume order for top N, then append newcomers
                        top_pairs = combined
                        self.logger.info(f"📍 Position-Aware Sync: Added {len(must_monitor)} active trade symbols to monitoring cycle.")
            except Exception as e:
                self.logger.warning(f"⚠️ Failed to inject open positions into monitoring: {e}")

            # Update cache
            self.top_pairs_cache = top_pairs
            self.last_pairs_update = now

            if len(top_pairs) == 0:
                self.logger.warning(f"⚠️  MARKET DISCOVERY FAILED: Found 0 pairs matching 'USDT' with volume > ${min_volume_usdt:,.0f}.")
            else:
                self.logger.info(f"Found {len(top_pairs)} top pairs (min volume: ${min_volume_usdt:,.0f})")
                self.logger.info(f"Top 10: {', '.join(top_pairs[:10])}")

            return top_pairs

        except Exception as e:
            self.logger.error(f"Error fetching top pairs: {e}")
            return getattr(self.config, 'FUTURES_PAIRS', ['BTC/USDT', 'ETH/USDT'])

    def _get_candle_ttl_seconds(self, timeframe: str) -> int:
        """Return how long a candle timeframe is valid for (in seconds).
        
        15m candles only update when a new 15m window opens. We cache them
        for the full timeframe duration (or a bounded window to be safe),
        which is the primary driver of the -1003 rate-limit bans: the bot
        was re-downloading 300 candles x 100 symbols every sweep.
        """
        tf_map = {
            '1m': 60, '3m': 180, '5m': 300, '15m': 900,
            '30m': 1800, '1h': 3600, '2h': 7200, '4h': 14400,
        }
        return tf_map.get(str(timeframe).lower(), 900)  # default 15m

    def fetch_market_data(self, symbol='BTC/USDT', timeframe=None, limit=None):
        """Fetch market data using CCXT (exchange-agnostic) with OHLCV caching.

        RATE-LIMIT FIX: Cached per (symbol, timeframe) for the candle's own
        expiry window. Since a 15m candle is immutable until its window closes,
        we never re-download it within that window. This cuts REST fetch_ohlcv
        calls from ~130/min (100 symbols every sweep) down to a handful per
        timeframe boundary — eliminating the -1003 IP bans.
        """
        if timeframe is None:
            timeframe = self.config.TIMEFRAME
        if limit is None:
            # Use config-reduced limit (210) to cut per-call weight (Task 1.5)
            limit = int(getattr(self.config, 'OHLCV_LIMIT', 210))

        # --- CACHE HIT: return cached candles if still within their TTL window ---
        cache_key = (symbol, timeframe)
        ttl_seconds = self._get_candle_ttl_seconds(timeframe)
        now = time.time()
        last_fetch = self._ohlcv_cache_ts.get(cache_key, 0)
        if now - last_fetch < ttl_seconds and cache_key in self._ohlcv_cache:
            return self._ohlcv_cache[cache_key]

        # --- PHASE 2 FIX (Task 2.3): Batch rotation cap ---
        # On cold start, all symbols are stale. Cap stale refreshes per sweep
        # at OHLCV_BATCH_MAX to avoid bursting the rate limit. Symbols beyond
        # the cap use stale cache (or None) this sweep; they'll refresh next
        # sweep as the batch counter resets.
        if self._ohlcv_batch_count >= self._ohlcv_batch_max:
            if cache_key in self._ohlcv_cache:
                return self._ohlcv_cache[cache_key]
            # Track batch-cap skips separately so we can suppress noise in logs
            self._batch_cap_skipped_symbols.add(symbol)
            return None
        self._ohlcv_batch_count += 1

        # Normalize symbol for exchange specificity (e.g. ADA/USDT -> ADA/USDT:USDT if needed)
        try:
            exchange = self.exchange.exchange
            if symbol not in exchange.markets:
                # Try to find the actual symbol in the loaded markets (robust matching)
                for market_symbol in exchange.markets:
                    if market_symbol.split(':')[0] == symbol:
                        symbol = market_symbol
                        break

            # Route through the client so candle fetches respect the rate limiter
            # AND the IP-ban cooldown (previously bypassed both via raw ccxt).
            ohlcv = self.exchange.get_ohlcv(symbol, timeframe, limit=limit)
            if not ohlcv:
                if cache_key in self._ohlcv_cache:
                    self.logger.warning(f"Using cached candles for {symbol} (fetch returned empty/blocked).")
                    return self._ohlcv_cache[cache_key]
                return None
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

            # --- CACHE MISS: store the fetched candles for the candle TTL ---
            self._ohlcv_cache[cache_key] = df
            self._ohlcv_cache_ts[cache_key] = now
            return df
        except Exception as e:
            # On error, fall back to stale cache if available (better than nothing)
            if cache_key in self._ohlcv_cache:
                self.logger.warning(f"Rate-limit / fetch error for {symbol}: {e}. Using cached candles.")
                return self._ohlcv_cache[cache_key]
            self.logger.error(f"Error fetching market data for {symbol}: {e}")
            return None
