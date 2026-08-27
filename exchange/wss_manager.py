import asyncio
import time
import threading
import logging
import websockets
from typing import Dict, Any

try:
    import orjson as _json_lib

    def _json_loads(s):
        return _json_lib.loads(s if isinstance(s, (bytes, bytearray)) else s.encode() if isinstance(s, str) else s)
except ImportError:
    import json as _json_lib

    def _json_loads(s):
        return _json_lib.loads(s)


class BinanceFuturesWSSManager:
    """
    High-frequency WebSocket manager for Binance Futures.
    Subscribes to the global mark price array stream (!markPrice@arr@1s)
    to provide real-time price updates for all symbols simultaneously.
    """

    def __init__(self, logger=None, testnet=False):
        if testnet:
            self.url = "wss://fstream.binancefuture.com/ws/!markPrice@arr@1s"
        else:
            self.url = "wss://fstream.binance.com/ws/!markPrice@arr@1s"

        self.logger = logger or logging.getLogger(__name__)
        self.live_prices: Dict[str, float] = {}
        # Cache raw->standardized symbols to avoid 4× str.endswith per item per second
        self._symbol_cache: Dict[str, str] = {}
        self.is_running = False
        self._stop_event = asyncio.Event()
        self._thread = None
        self._loop = None
        self._last_heartbeat = 0

    def start(self):
        """Start the WSS manager in a dedicated background thread."""
        if self.is_running:
            return

        self.is_running = True
        self._thread = threading.Thread(target=self._run_thread, daemon=True)
        self._thread.start()
        self.logger.info("📡 Binance Futures WSS Manager started.")

    def stop(self):
        """Stop the WSS manager and close the connection."""
        self.is_running = False
        if self._loop:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)
        self.logger.info("📡 Binance Futures WSS Manager stopped.")

    def _run_thread(self):
        """Thread entry point: Create a new event loop and run the WSS task."""
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main_loop())
        finally:
            self._loop.close()

    async def _main_loop(self):
        """Primary connection loop with automatic reconnection."""
        while self.is_running:
            try:
                async with websockets.connect(self.url) as websocket:
                    self.logger.info(f"✅ WSS Connected to {self.url}")
                    while self.is_running and not self._stop_event.is_set():
                        message = await websocket.recv()
                        self._handle_message(message)
            except Exception as e:
                if self.is_running:
                    self.logger.warning(f"⚠️ WSS Connection lost: {e}. Reconnecting in 5s...")
                    await asyncio.sleep(5)
                else:
                    break

    def _handle_message(self, message: str):
        """Parse the Binance array payload and update live_prices."""
        try:
            data = _json_loads(message)
            if not isinstance(data, list):
                return

            for item in data:
                symbol_raw = item.get('s')  # e.g., "BTCUSDT"
                price_raw = item.get('p')   # e.g., "65000.50"

                if symbol_raw and price_raw:
                    # Dict-lookup fast path; fallback to computed + cache
                    symbol = self._symbol_cache.get(symbol_raw)
                    if symbol is None:
                        symbol = self._standardize_symbol(symbol_raw)
                        self._symbol_cache[symbol_raw] = symbol
                    try:
                        self.live_prices[symbol] = float(price_raw)
                    except (ValueError, TypeError):
                        continue

            now = time.time()
            if now - self._last_heartbeat > 60:
                self.logger.info(f"📡 WSS Heartbeat: {len(self.live_prices)} symbols updated in cache.")
                self._last_heartbeat = now

        except Exception as e:
            self.logger.error(f"❌ Error parsing WSS message: {e}")

    def _standardize_symbol(self, symbol_raw: str) -> str:
        """
        Converts Binance raw symbols to bot standard (BTCUSDT -> BTC/USDT).
        Handles common quotes like USDT, BUSD, USDC.
        """
        for quote in ["USDT", "BUSD", "USDC", "CUSD"]:
            if symbol_raw.endswith(quote):
                base = symbol_raw[:-len(quote)]
                return f"{base}/{quote}"
        return symbol_raw  # Fallback
