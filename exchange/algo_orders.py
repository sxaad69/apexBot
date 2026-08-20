"""
Exchange Algo Order Mixin — extracted from main.py PaperTradingEngine (pure move, no logic change).

Holds exchange-side conditional (Algo) order helpers:
  - _market_supports_trailing_stop
  - _get_cached_order_status
  - _algo_symbol
  - _place_exchange_conditional
  - _cancel_exchange_conditional
  - _get_cached_open_algo_ids

Mixin design: PaperTradingEngine inherits this, so all self.* references resolve
to the engine instance exactly as before. Method names/signatures are unchanged.
"""

import time
from typing import Dict


class AlgoOrdersMixin:
    """Exchange conditional/algo order helpers (moved from main.py, behavior-identical)."""

    def _market_supports_trailing_stop(self, symbol: str) -> bool:
        """Check if the given market supports native TRAILING_STOP_MARKET orders.
        
        Uses the already-cached market metadata (load_markets) — zero extra API calls.
        Binance can temporarily drop TRAILING_STOP_MARKET from a symbol's allowed
        orderTypes (new listings, maintenance, per-symbol restrictions), so we never
        assume support.
        """
        try:
            market = self.exchange.exchange.market(symbol)
            order_types = (market.get('info') or {}).get('orderTypes') or []
            return 'TRAILING_STOP_MARKET' in order_types
        except Exception:
            return False
    
    def _get_cached_order_status(self, order_id: str, symbol: str):
        """Fetch order status with TTL caching (Task 1.1).

        check_exits() polls fetch_order(sl_id) + fetch_order(tp_id) every 0.5s
        per open position (~1,200 calls/min with 5 positions). Orders don't
        fill that fast, so we cache status for a short TTL (default 5s).
        """
        now = time.time()
        cached = self._order_status_cache.get(order_id)
        if cached and (now - cached[1]) < self._order_status_ttl:
            return cached[0]
        try:
            status = self.exchange.exchange.fetch_order(order_id, symbol)
            self._order_status_cache[order_id] = (status, now)
            return status
        except Exception as e:
            # On error, fall back to stale cache if available
            if cached:
                self.logger.warning(f"Rate-limit / fetch error for order {order_id}: {e}. Using cached status.")
                return cached[0]
            self.logger.warning(f"Failed to fetch order {order_id} for {symbol}: {e}")
            return None

    def _algo_symbol(self, symbol: str) -> str:
        """Normalize a ccxt symbol ('BTC/USDT:USDT') to the raw Binance format ('BTCUSDT')."""
        return symbol.replace('/', '').split(':')[0]

    def _place_exchange_conditional(self, symbol, side, order_type, quantity, trigger_price=None,
                                    activate_price=None, callback_rate=None, client_algo_id=None):
        """Place a Binance conditional (Algo) order via the Algo Order API.

        Since 2025-12-09 Binance migrated ALL conditional orders (STOP_MARKET,
        TAKE_PROFIT_MARKET, TRAILING_STOP_MARKET) to the Algo Order API; the
        legacy /fapi/v1/order endpoint rejects them with -4120. This uses
        POST /fapi/v1/algoOrder with algoType=CONDITIONAL.

        reduceOnly=true bypasses the $50 min notional so our small positions work.
        Returns the algoId (str) or None on failure.
        """
        try:
            exchange = self.exchange.exchange
            params = {
                'algoType': 'CONDITIONAL',
                'symbol': self._algo_symbol(symbol),
                'side': side.upper(),
                'type': order_type,
                'quantity': quantity,
                'reduceOnly': 'true',
                'workingType': 'CONTRACT_PRICE',
                'newOrderRespType': 'RESULT',
            }
            if trigger_price is not None:
                params['triggerPrice'] = trigger_price
            if activate_price is not None:
                params['activatePrice'] = activate_price
            if callback_rate is not None:
                params['callbackRate'] = callback_rate
            if client_algo_id:
                params['clientAlgoId'] = client_algo_id
            resp = exchange.fapiPrivatePostAlgoOrder(params)
            algo_id = resp.get('algoId') if isinstance(resp, dict) else None
            if algo_id is not None:
                self.logger.debug(f"[Algo] Placed {order_type} {symbol} ({client_algo_id}): algoId={algo_id}")
            return str(algo_id) if algo_id is not None else None
        except Exception as e:
            self.logger.warning(f"⚠️ Failed to place {order_type} via Algo API for {symbol}: {e}")
            return None

    def _cancel_exchange_conditional(self, symbol, algo_id):
        """Cancel a conditional (Algo) order by its algoId."""
        if not algo_id:
            return
        try:
            self.exchange.exchange.fapiPrivateDeleteAlgoOrder({
                'symbol': self._algo_symbol(symbol),
                'algoId': algo_id,
            })
        except Exception as e:
            self.logger.debug(f"Cancel algo {algo_id} for {symbol} failed (may already be gone): {e}")

    def _get_cached_open_algo_ids(self, symbol):
        """Return a set of open algoIds for a symbol via openAlgoOrders, TTL-cached.

        This is the reliable detection source for exchange-side exits: when a
        conditional order triggers and fills, Binance removes it from the open
        list, so an algoId dropping out == the order fired.
        """
        now = time.time()
        cached = self._open_algo_cache.get(symbol)
        if cached and (now - cached[1]) < self._order_status_ttl:
            return cached[0]
        # While the IP is banned, don't re-poll the Algo API (each call re-arms
        # the ban). Serve the last-known open-algo set so exits still resolve
        # from cache and no new REST traffic is generated.
        if getattr(self.exchange, '_is_banned', lambda: False)():
            if cached:
                return cached[0]
            return None
        # Go through the shared rate limiter so the sentinel's per-symbol
        # openAlgoOrders polls obey the same budget + cold-start warmup as every
        # other REST call (previously this bypassed the limiter entirely).
        rate_limiter = getattr(self.exchange, 'rate_limiter', None)
        if rate_limiter is not None and not rate_limiter.acquire(weight=3, timeout=5):
            if cached:
                return cached[0]
            return None
        try:
            resp = self.exchange.exchange.fapiPrivateGetOpenAlgoOrders({'symbol': self._algo_symbol(symbol)})
            algo_ids = set()
            if isinstance(resp, list):
                for o in resp:
                    if isinstance(o, dict) and o.get('algoId') is not None:
                        algo_ids.add(str(o['algoId']))
            self._open_algo_cache[symbol] = (algo_ids, now)
            return algo_ids
        except Exception as e:
            if getattr(self.exchange, '_record_ban', None):
                self.exchange._record_ban(e)
            self.logger.warning(f"Failed to fetch open algo orders for {symbol}: {e}")
            if cached:
                return cached[0]
            return None

    def cancel_position_algo_orders(self, position: Dict, symbol: str = None) -> int:
        """B1: Cancel ALL algo orders (SL/trailing/TP) for a position/symbol.

        Called when a position exits. Standard cancel_all_orders() does NOT touch
        Binance Algo API orders (migrated 2025-12-09), so orphaned SL/trailing/TP
        accumulate on closed symbols (we saw ~14 orphans). This cancels the tracked
        IDs plus any remaining open algos for the symbol.

        Returns the number of algo orders cancelled.
        """
        cancelled = 0
        sym = symbol or (position.get('symbol') if isinstance(position, dict) else position)
        if not sym:
            return 0
        algo_sym = self._algo_symbol(sym)

        # 1. Cancel tracked IDs on the position
        tracked = []
        if isinstance(position, dict):
            for key in ('sl_order_id', 'tp_order_id', 'trailing_order_id'):
                oid = position.get(key)
                if oid:
                    tracked.append(str(oid))

        # 2. Enumerate any remaining open algos for the symbol
        open_ids = self._get_cached_open_algo_ids(sym)
        to_cancel = set(tracked)
        if open_ids:
            to_cancel |= set(open_ids)

        for algo_id in sorted(to_cancel):
            try:
                self.exchange.exchange.fapiPrivateDeleteAlgoOrder({
                    'symbol': algo_sym, 'algoId': algo_id})
                cancelled += 1
            except Exception:
                pass  # already gone
        if cancelled:
            self._open_algo_cache.pop(sym, None)
            self.logger.info(f"🗑️ [B1] Canceled {cancelled} algo order(s) for {sym} on exit")
        return cancelled
