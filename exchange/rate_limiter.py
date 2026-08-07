"""
Global Token-Bucket Rate Limiter (Task 1.2)

Safety net that caps total Binance REST weight per minute regardless of
which code path is making calls. Binance futures limit is 2,400 weight/min;
we budget 1,500 to leave headroom for spikes and retries.

Usage:
    limiter = RateLimiter(max_weight_per_min=1500)
    with limiter.acquire(weight=5):   # blocks until tokens available
        exchange.fetch_ohlcv(...)
"""

import threading
import time
from typing import Optional


class RateLimiter:
    """Token-bucket rate limiter keyed on Binance request weight."""

    def __init__(self, max_weight_per_min: int = 1500, logger=None):
        self.max_weight_per_min = max_weight_per_min
        self.logger = logger
        self._tokens = float(max_weight_per_min)   # start full
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        # Telemetry for the testing suite / monitoring
        self.total_weight_consumed = 0
        self.calls_made = 0
        self.waits_seconds = 0.0

    def _refill(self):
        """Refill tokens continuously at max_weight_per_min / 60 per second."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.max_weight_per_min,
            self._tokens + elapsed * (self.max_weight_per_min / 60.0)
        )
        self._last_refill = now

    def acquire(self, weight: int = 1, timeout: Optional[float] = None) -> bool:
        """
        Block until `weight` tokens are available (or timeout elapses).

        Returns True if acquired, False if timed out.
        """
        if weight <= 0:
            return True

        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= weight:
                    self._tokens -= weight
                    self.total_weight_consumed += weight
                    self.calls_made += 1
                    return True
                # Not enough tokens — compute wait time
                deficit = weight - self._tokens
                wait_needed = deficit / (self.max_weight_per_min / 60.0)

            if deadline is not None and time.monotonic() + wait_needed > deadline:
                if self.logger:
                    self.logger.warning(
                        f"⏳ Rate limiter timeout: needed {weight} weight, "
                        f"waited {timeout}s. Skipping call."
                    )
                return False

            # Sleep in small increments so we can check the deadline
            sleep_for = min(wait_needed, 0.1)
            time.sleep(sleep_for)
            self.waits_seconds += sleep_for

    def __enter__(self):
        # Default weight 1; callers can use acquire() directly for custom weight
        self.acquire(weight=1)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def stats(self) -> dict:
        """Return telemetry for monitoring / tests."""
        with self._lock:
            return {
                'max_weight_per_min': self.max_weight_per_min,
                'tokens_available': round(self._tokens, 2),
                'total_weight_consumed': self.total_weight_consumed,
                'calls_made': self.calls_made,
                'waits_seconds': round(self.waits_seconds, 2),
            }