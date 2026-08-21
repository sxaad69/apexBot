"""
CCXT Exchange Client
Unified exchange client using CCXT library for multi-exchange support
"""

import ccxt
import time
from typing import Dict, Any, List, Optional
from .base_client import BaseExchangeClient
from .rate_limiter import RateLimiter


class CCXTExchangeClient(BaseExchangeClient):
    """
    Unified exchange client using CCXT
    Supports 100+ exchanges with a common interface
    """
    
    def __init__(self, config, logger, exchange_id: Optional[str] = None):
        """
        Initialize CCXT exchange client
        
        Args:
            config: Configuration object
            logger: Logger instance
            exchange_id: Exchange ID (e.g., 'binance', 'kucoin')
                        If None, uses config.EXCHANGE
        """
        self.config = config
        self.logger = logger
        self.exchange_id = exchange_id or config.EXCHANGE.lower()
        
        # Get API credentials for this exchange
        self.credentials = self._get_credentials()
        
        # Initialize CCXT exchange
        self.exchange = self._initialize_exchange()

        # --- RATE-LIMIT FIX (Task 1.2): Global token-bucket safety net ---
        # Caps total REST weight/min regardless of code path. Binance futures
        # limit is 2,400 weight/min; we budget 1,500 for headroom.
        max_weight = getattr(config, 'RATE_LIMIT_MAX_WEIGHT_PER_MIN', 1500)
        # Cold-start warmup: ramp from a low budget to full over a few minutes
        # so a fresh restart doesn't burst the API (all caches empty) and trip
        # the -1003 IP ban. Defaults: 3 min warmup from 30% budget.
        warmup_secs = float(getattr(config, 'RATE_LIMIT_WARMUP_SECONDS', 180.0))
        warmup_floor = float(getattr(config, 'RATE_LIMIT_WARMUP_FLOOR_RATIO', 0.30))
        self.rate_limiter = RateLimiter(max_weight_per_min=max_weight, logger=logger,
                                        warmup_seconds=warmup_secs,
                                        warmup_floor_ratio=warmup_floor)
        
        self.logger.system(
            f"CCXT client initialized: {self.exchange_id} "
            f"({config.EXCHANGE_ENVIRONMENT}) | Rate limiter: {max_weight}/min "
            f"(warmup {warmup_secs:.0f}s from {warmup_floor*100:.0f}%)"
        )

        # --- IP BAN COOLDOWN (self-sustaining -1003 ban fix) ---
        # Binance bans the IP (-1003) when request rate spikes. The sentinel's
        # per-symbol polls keep firing even WHILE banned, and each 418 response
        # re-arms the ban window — so a cold-start burst turns into an hours-long
        # self-sustaining ban. When a ban is detected we record the expiry and
        # short-circuit all REST fetches (serve cache / safe defaults) until it
        # clears, so the IP can actually cool down.
        self._ban_until = 0.0            # epoch seconds; 0 == not banned

    def _is_banned(self) -> bool:
        """True if Binance is currently rate-limit-banning our IP."""
        import time as _time
        return getattr(self, '_ban_until', 0) > _time.time()

    @staticmethod
    def _is_ban_condition(error) -> bool:
        """True if an exception is a -1003/418/429 rate-limit condition."""
        import re
        msg = str(error)
        return ('banned until' in msg
                or 'Too Many Requests' in msg
                or '-1003' in msg
                or str(getattr(error, 'code', '')) in ('429', '418'))

    @staticmethod
    def _parse_ban_until(error) -> float:
        """Extract the epoch-seconds ban expiry from a -1003 message, else now."""
        import re
        m = re.search(r"banned until (\d+)", str(error))
        if m:
            return int(m.group(1)) / 1000.0
        return __import__('time').time() + 30.0

    def _record_ban(self, error) -> None:
        """Detect -1003 'banned until <ms>' / 429 and start a cooldown.

        Called from every rate-limited fetch when an exception bubbles up.
        - 418 'banned until <epoch_ms>': park until that time + a small grace
          so we don't re-trip the instant the ban lifts.
        - 429 (generic too-many-requests): back off briefly instead of hammering.
        """
        import time as _time
        import re
        msg = str(error)
        m = re.search(r"banned until (\d+)", msg)
        now = _time.time()
        if m:
            until = int(m.group(1)) / 1000.0
            # Grace: resume a few seconds after the ban window so the first
            # resumed calls land in a cool window, not right at the edge.
            self._ban_until = max(self._ban_until, until + 5.0)
            self.logger.warning(
                f"🚫 BINANCE IP BAN — pausing REST calls until "
                f"{self._ban_until:.0f} (now {now:.0f}; diff {(self._ban_until-now)/60:.1f} min)"
            )
        elif 'Too Many Requests' in msg or '429' in str(getattr(error, 'code', '')):
            # Transient overload — short backoff so the sentinel stops polling.
            if self._ban_until < now + 30.0:
                self._ban_until = now + 30.0
            self.logger.warning("🚦 Binance 429 — backing off REST for 30s.")
    
    def _get_credentials(self) -> Dict[str, str]:
        """Get API credentials for the exchange"""
        exchange_upper = self.exchange_id.upper()
        
        credentials = {
            'apiKey': getattr(self.config, f'{exchange_upper}_API_KEY', ''),
            'secret': getattr(self.config, f'{exchange_upper}_API_SECRET', ''),
        }
        
        # Some exchanges need passphrase
        if self.exchange_id in ['kucoin', 'okx']:
            passphrase = getattr(self.config, f'{exchange_upper}_API_PASSPHRASE', '')
            if passphrase:
                credentials['password'] = passphrase
        
        return credentials
    
    def _initialize_exchange(self):
        """Initialize the CCXT exchange instance"""
        try:
            # Get exchange class
            # Get exchange class (Map 'kucoin' to 'kucoinfutures' for proper API support)
            effective_id = self.exchange_id
            if effective_id == 'kucoin':
                effective_id = 'kucoinfutures'
            
            exchange_class = getattr(ccxt, effective_id)
            
            # Base configuration
            exchange_config = {
                **self.credentials,
                'enableRateLimit': True,
                'options': {
                    'defaultType': 'future',  # Use futures by default
                    'warnOnFetchOpenOrdersWithoutSymbol': False,
                }
            }
            
            # Set testnet/sandbox if configured
            # NOTE: For Binance, sandbox mode is deprecated for futures (ccxt >= 4.5).
            #       We use demo trading instead via enable_demo_trading().
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet' and self.exchange_id != 'binance':
                exchange_config['sandbox'] = True  # Sandbox for non-Binance exchanges (e.g. KuCoin)
            
            # Initialize exchange
            exchange = exchange_class(exchange_config)
            
            # Binance explicit demo trading enable (handles different ccxt versions safely)
            if self.config.EXCHANGE_ENVIRONMENT == 'testnet' and self.exchange_id == 'binance':
                try:
                    # Native general CCXT method - switches to demo-fapi.binance.com endpoints
                    exchange.enable_demo_trading(True)
                except AttributeError:
                    try:
                        exchange.set_sandbox_mode(True)
                    except AttributeError:
                        self.logger.warning("Could not set Binance demo trading mode dynamically. Ensure ccxt is up to date.")
                
                # Binance Testnet API keys are futures-only, fetching SPOT currencies crashes it
                exchange.options['fetchCurrencies'] = False

            # Paper mode only needs PUBLIC market data (candles, orderbook, tickers).
            # fetchCurrencies/fetchMargins hit PRIVATE SAPI endpoints, which would fail
            # on production when no valid production key is attached. Skip them entirely.
            if getattr(self.config, 'TRADING_MODE', 'paper').lower() == 'paper':
                exchange.options['fetchCurrencies'] = False
                exchange.options['fetchMargins'] = False
            
            # Load markets (ban-aware: if Binance is still cooling down from a
            # -1003 ban, load_markets() raises 418 and would crash-loop via
            # systemd restarts, re-arming the ban each time. Wait out the ban
            # window instead of failing.)
            try:
                exchange.load_markets()
            except Exception as e:
                if self._is_ban_condition(e):
                    until = self._parse_ban_until(e)
                    wait_s = max(0.0, until - time.time())
                    self.logger.warning(
                        f"🚫 Binance still cooling down from IP ban — waiting "
                        f"{wait_s/60:.1f} min before loading markets..."
                    )
                    time.sleep(wait_s + 5.0)
                    exchange.load_markets()
                else:
                    raise

            return exchange
            
        except AttributeError:
            self.logger.error(f"Exchange '{self.exchange_id}' not supported by CCXT")
            raise ValueError(f"Unsupported exchange: {self.exchange_id}")
        except Exception as e:
            self.logger.error(f"Failed to initialize {self.exchange_id}: {e}", exc_info=True)
            raise
    
    def get_recent_trades(self, symbol: str, limit: int = 100) -> List:
        """Fetch recent trades (rate-limited + ban-gated).

        The A6 whale detector called raw ccxt ``exchange.fetch_trades`` per
        symbol on every rejected-signal sweep, bypassing the rate limiter and
        ban gate entirely — hundreds of unthrottled REST calls per sweep that
        pushed the IP over Binance's request limit. Route through here.
        """
        if self._is_banned():
            self.logger.debug(f"[trades] {symbol} skipped — IP banned until {self._ban_until:.0f}")
            return []
        # weight 8: whale enrichment is low-value, so budget it conservatively
        # (8/1500 of the per-minute budget each) vs the true Binance weight.
        if not self.rate_limiter.acquire(weight=8, timeout=5):
            self.logger.warning(f"⏳ Rate limiter timeout fetching trades for {symbol}. Returning empty.")
            return []
        try:
            return self.exchange.fetch_trades(symbol, limit=limit)
        except Exception as e:
            self._record_ban(e)
            self.logger.warning(f"Rate-limit / fetch error for trades {symbol}: {e}. Returning empty.")
            return []

    def get_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 210) -> List:
        """Fetch OHLCV candles (rate-limited + ban-gated).

        The old market-data path called raw ccxt ``exchange.fetch_ohlcv`` directly,
        bypassing the rate limiter AND the ban gate — a cold-start burst of
        per-symbol candle fetches could push the IP over the -1003 threshold and,
        once banned, keep hammering with every sweep. Route all candle fetches
        through here so the ban cooldown applies to them too.
        """
        import time as _time
        if self._is_banned():
            self.logger.debug(f"[ohlcv] {symbol} skipped — IP banned until {self._ban_until:.0f}")
            return []
        if not self.rate_limiter.acquire(weight=5, timeout=5):
            self.logger.warning(f"⏳ Rate limiter timeout fetching {symbol} OHLCV. Returning empty.")
            return []
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            return ohlcv
        except Exception as e:
            self._record_ban(e)
            self.logger.warning(f"Rate-limit / fetch error for {symbol}: {e}. Returning empty candles.")
            return []

    def get_balance(self) -> Dict[str, Any]:
        """Get account balance (rate-limited + TTL-cached 60s).

        RATE-LIMIT FIX (Task 1.4): Called every sweep (~45s) in live mode.
        Balance doesn't change that fast, so cache for 60s to cut REST calls.
        """
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'BALANCE_CACHE_TTL', 60.0)
        if (now - getattr(self, '_balance_cache_ts', 0)) < ttl and getattr(self, '_balance_cache', None) is not None:
            return self._balance_cache
        if self._is_banned():
            # Serve stale cache (or empty) without hitting the API while banned.
            if getattr(self, '_balance_cache', None) is not None:
                return self._balance_cache
            return {}
        try:
            if not self.rate_limiter.acquire(weight=1, timeout=5):
                # On limiter timeout, fall back to stale cache if available
                if getattr(self, '_balance_cache', None) is not None:
                    return self._balance_cache
                return {}
            balance = self.exchange.fetch_balance()
            self._balance_cache = balance
            self._balance_cache_ts = now
            self.logger.debug(f"Fetched balance from {self.exchange_id}")
            return balance
        except Exception as e:
            # On error, fall back to stale cache if available
            self._record_ban(e)
            if getattr(self, '_balance_cache', None) is not None:
                self.logger.warning(f"Rate-limit / fetch error for balance: {e}. Using cached balance.")
                return self._balance_cache
            self.logger.error(f"Error fetching balance: {e}", exc_info=True)
            return {}
            
    def get_canonical_symbol(self, symbol: str) -> str:
        """
        Get the canonical symbol mapping from CCXT (e.g. 'BTC/USDT' -> 'BTC/USDT:USDT').
        Used for exact string matching against CCXT position and order objects.
        """
        try:
            market = self.exchange.market(symbol)
            return market.get('symbol', symbol)
        except Exception as e:
            self.logger.debug(f"Could not find canonical symbol for {symbol}: {e}")
            return symbol
    
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get open positions (TTL-cached to avoid hammering Binance REST).

        RATE-LIMIT FIX: The PriorityExitSentinel thread calls this every 0.5s,
        which was a major contributor to the -1003 IP bans (2004+ occurrences).
        Positions don't change that fast, so we cache for a short TTL.
        """
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'POSITIONS_CACHE_TTL', 5.0)  # 5s default

        def _filter_by_symbol(positions_list):
            if not symbol:
                return positions_list
            return [p for p in positions_list if p.get('symbol') == symbol]

        cache_hit = (now - getattr(self, '_positions_cache_ts', 0)) < ttl and getattr(self, '_positions_cache', None) is not None
        if cache_hit:
            return _filter_by_symbol(self._positions_cache)
        if self._is_banned():
            # Serve stale cache (or empty) without hitting the API while banned.
            if getattr(self, '_positions_cache', None) is not None:
                return _filter_by_symbol(self._positions_cache)
            return []
        if not self.rate_limiter.acquire(weight=5, timeout=5):
            # On limiter timeout, fall back to stale cache if available
            if getattr(self, '_positions_cache', None) is not None:
                return _filter_by_symbol(self._positions_cache)
            return []
        try:
            if symbol:
                positions = self.exchange.fetch_positions([symbol])
            else:
                positions = self.exchange.fetch_positions()
            
            # Filter out empty positions
            active_positions = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
            
            self._positions_cache = active_positions
            self._positions_cache_ts = now
            self.logger.debug(f"Fetched {len(active_positions)} positions from {self.exchange_id}")
            return _filter_by_symbol(active_positions)
        except Exception as e:
            # On error, fall back to stale cache if available
            self._record_ban(e)
            if getattr(self, '_positions_cache', None) is not None:
                self.logger.warning(f"Rate-limit / fetch error for positions: {e}. Using cached positions.")
                return _filter_by_symbol(self._positions_cache)
            self.logger.error(f"Error fetching positions: {e}", exc_info=True)
            return []

    def confirm_position_exists(self, symbol: str) -> bool:
        """A2: Ground-truth check whether a position is still open on the exchange.

        Bypasses the TTL cache and the ban/limiter 'return []' fallbacks that can
        FALSELY report a position as gone (the phantom-close / 4USDT root cause).
        Does a fresh direct fetch with a small retry. Used ONLY for exit verification
        (not the 0.5s sentinel poll), so the extra REST weight is acceptable.
        """
        import time as _time
        sym = symbol
        attempts = 0
        while attempts < 3:
            try:
                if self._is_banned():
                    # Don't hammer while banned, but do NOT trust empty as "gone".
                    # Fall back to whatever the cache says, but flag uncertainty.
                    cached = getattr(self, '_positions_cache', None)
                    if cached is not None:
                        return any(p.get('symbol') == sym for p in cached)
                    return True  # unknown -> treat as still open (safe: no phantom close)
                # direct fetch through the shared limiter (single call, small weight)
                rate_limiter = getattr(self, 'rate_limiter', None)
                if rate_limiter is not None and not rate_limiter.acquire(weight=5, timeout=8):
                    # timeout on the limiter -> don't trust empty; return True (still open)
                    return True
                positions = self.exchange.fetch_positions([sym])
                active = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                # update the cache so downstream calls agree with this ground truth
                self._positions_cache = active
                self._positions_cache_ts = _time.time()
                return len(active) > 0
            except Exception as e:
                self._record_ban(e)
                attempts += 1
                _time.sleep(0.5)
        # After retries, uncertain -> do NOT trust empty (avoid phantom close)
        return True

    def fetch_position_size(self, symbol: str) -> float:
        """Ground-truth fetch of a single position's contract size.

        Bypasses the TTL cache and ban/limiter fallbacks the same way
        confirm_position_exists does, but returns the actual size (0 if
        no position) so callers can both verify AND get the quantity.
        Used by record_entry verification (the S1 fix).
        """
        import time as _time
        sym = symbol
        for attempt in range(3):
            try:
                if self._is_banned():
                    cached = getattr(self, '_positions_cache', None)
                    if cached is not None:
                        for p in cached:
                            if p.get('symbol') == sym:
                                return abs(float(p.get('contracts', 0) or 0))
                    return 0.0
                rate_limiter = getattr(self, 'rate_limiter', None)
                if rate_limiter is not None and not rate_limiter.acquire(weight=5, timeout=8):
                    cached = getattr(self, '_positions_cache', None)
                    if cached is not None:
                        for p in cached:
                            if p.get('symbol') == sym:
                                return abs(float(p.get('contracts', 0) or 0))
                    return 0.0
                positions = self.exchange.fetch_positions([sym])
                active = [p for p in positions if abs(float(p.get('contracts', 0) or 0)) > 0]
                if active:
                    return abs(float(active[0].get('contracts', 0) or 0))
                return 0.0
            except Exception as e:
                self._record_ban(e)
                _time.sleep(0.5)
        return 0.0

    def get_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get current ticker data (TTL-cached to avoid hammering Binance REST)."""
        import time as _time
        now = _time.time()
        ttl = getattr(self.config, 'TICKER_CACHE_TTL', 5.0)  # 5s default
        cache_key = f"ticker_{symbol}"
        if (now - getattr(self, '_ticker_cache_ts', {}).get(cache_key, 0)) < ttl and cache_key in getattr(self, '_ticker_cache', {}):
            return self._ticker_cache[cache_key]
        if self._is_banned():
            # Serve stale cache (or empty) without hitting the API while banned.
            if hasattr(self, '_ticker_cache') and cache_key in self._ticker_cache:
                return self._ticker_cache[cache_key]
            return {}
        if not self.rate_limiter.acquire(weight=2, timeout=5):
            # On limiter timeout, fall back to stale cache if available
            if hasattr(self, '_ticker_cache') and cache_key in self._ticker_cache:
                return self._ticker_cache[cache_key]
            return {}
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            if not hasattr(self, '_ticker_cache'):
                self._ticker_cache = {}
                self._ticker_cache_ts = {}
            self._ticker_cache[cache_key] = ticker
            self._ticker_cache_ts[cache_key] = now
            return ticker
        except Exception as e:
            # On error, fall back to stale cache if available
            self._record_ban(e)
            if hasattr(self, '_ticker_cache') and cache_key in self._ticker_cache:
                self.logger.warning(f"Rate-limit / fetch error for ticker {symbol}: {e}. Using cached ticker.")
                return self._ticker_cache[cache_key]
            self.logger.error(f"Error fetching ticker for {symbol}: {e}")
            return {}
    
    def set_margin_mode(self, symbol: str, margin_mode: str = 'ISOLATED'):
        """
        Set margin mode for a symbol (e.g., 'ISOLATED', 'CROSS')
        Binance specific implementation via CCXT
        """
        try:
            # Normalize mode to uppercase for Binance
            mode = margin_mode.upper()
            
            # Use CCXT unified method if available
            if hasattr(self.exchange, 'set_margin_mode'):
                self.exchange.set_margin_mode(mode, symbol)
                self.logger.info(f"Set margin mode to {mode} for {symbol}")
            else:
                self.logger.debug(f"Exchange {self.exchange_id} does not support set_margin_mode via CCXT")
        except Exception as e:
            # Binance throws error if already set to that mode - we ignore and log as debug
            if "No need to change margin type" in str(e):
                self.logger.debug(f"Margin mode for {symbol} already set to {margin_mode}")
            else:
                self.logger.warning(f"Could not set margin mode for {symbol}: {e}")

    def get_orderbook(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Get order book"""
        try:
            orderbook = self.exchange.fetch_order_book(symbol, limit)
            return orderbook
        except Exception as e:
            self.logger.error(f"Error fetching orderbook for {symbol}: {e}")
            return {'bids': [], 'asks': []}
    
    def place_order(self, symbol: str, side: str, order_type: str,
                   amount: float, price: Optional[float] = None, **kwargs) -> Dict[str, Any]:
        """Place an order"""
        try:
            # CCXT unified order placement
            if order_type == 'market':
                order = self.exchange.create_market_order(symbol, side, amount, params=kwargs)
            elif order_type == 'limit':
                if price is None:
                    raise ValueError("Price required for limit orders")
                order = self.exchange.create_limit_order(symbol, side, amount, price, params=kwargs)
            else:
                raise ValueError(f"Unsupported order type: {order_type}")
            
            # self.logger.trade_entry(...) - REMOVED (Redundant with TradeManager)
            
            return order
        except Exception as e:
            self.logger.error(f"Error placing order: {e}", exc_info=True)
            return {}
    
    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancel an order"""
        try:
            result = self.exchange.cancel_order(order_id, symbol)
            self.logger.info(f"Cancelled order {order_id} for {symbol}")
            return result
        except Exception as e:
            self.logger.error(f"Error cancelling order: {e}", exc_info=True)
            return {}
    
    def get_markets(self) -> Dict[str, Any]:
        """Get all available markets"""
        try:
            return self.exchange.markets
        except Exception as e:
            self.logger.error(f"Error fetching markets: {e}")
            return {}
    
    def get_trading_fees(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get trading fees"""
        try:
            if symbol:
                fees = self.exchange.fetch_trading_fee(symbol)
            else:
                fees = self.exchange.fetch_trading_fees()
            return fees
        except Exception as e:
            self.logger.error(f"Error fetching fees: {e}")
            return {}
    
    def close_position(self, symbol: str) -> Dict[str, Any]:
        """Close a position (futures-specific)"""
        try:
            # Get current position
            positions = self.get_positions(symbol)
            if not positions:
                self.logger.warning(f"No open position for {symbol}")
                return {}
            
            position = positions[0]
            side = 'sell' if position.get('side') == 'long' else 'buy'
            amount = abs(float(position.get('contracts', 0)))
            
            # Close with market order
            result = self.place_order(symbol, side, 'market', amount, reduceOnly=True)
            
            # Clean up any lingering Stop Loss / Take profit orders (Important!)
            try:
                self.exchange.cancel_all_orders(symbol)
                self.logger.info(f"Cleared lingering physical orders for {symbol}")
            except Exception as clean_e:
                self.logger.warning(f"Failed to clean up orders after closing {symbol}: {clean_e}")

            # B1: also cancel Algo API orders (SL/trailing/TP). cancel_all_orders does
            # NOT touch them (Binance migrated conditionals to the Algo API 2025-12-09),
            # so orphaned algos would accumulate on the now-closed symbol.
            try:
                self.cancel_all_algo_orders(symbol)
            except Exception as clean_e2:
                self.logger.warning(f"Failed to clean up algo orders after closing {symbol}: {clean_e2}")
            
            self.logger.info(f"Closed position for {symbol}")
            return result
        except Exception as e:
            self.logger.error(f"Error closing position: {e}", exc_info=True)
            return {}

    def cancel_all_algo_orders(self, symbol: str) -> int:
        """B1: Cancel ALL open Binance Algo API orders (SL/TP/trailing) for a symbol.

        Standard cancel_all_orders does NOT touch Algo API orders (Binance migrated
        conditional orders to /fapi/v1/algoOrder on 2025-12-09). Without this, orphaned
        SL/trailing/TP orders accumulate on closed symbols. Returns count cancelled.
        """
        algo_sym = symbol.replace('/', '').split(':')[0]
        cancelled = 0
        try:
            resp = self.exchange.fapiPrivateGetOpenAlgoOrders({'symbol': algo_sym})
            orders = resp if isinstance(resp, list) else (resp.get('orders', []) if isinstance(resp, dict) else [])
            for o in orders:
                algo_id = o.get('algoId') if isinstance(o, dict) else None
                if algo_id is None:
                    continue
                try:
                    self.exchange.fapiPrivateDeleteAlgoOrder({'symbol': algo_sym, 'algoId': algo_id})
                    cancelled += 1
                except Exception:
                    pass  # already gone
        except Exception as e:
            self.logger.warning(f"⚠️ cancel_all_algo_orders failed for {symbol}: {e}")
        if cancelled:
            self.logger.info(f"🗑️ [B1] Canceled {cancelled} algo order(s) for {symbol}")
        return cancelled
