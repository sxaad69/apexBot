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

    def __init__(self, max_weight_per_min: int = 1500, logger=None,
                 warmup_seconds: float = 180.0, warmup_floor_ratio: float = 0.30):
        self.max_weight_per_min = max_weight_per_min
        self.logger = logger
        # --- COLD-START WARMUP ---
        # A fresh bot restart bursts the API (all caches empty: positions,
        # tickers, algo-orders, OHLCV). Starting at a low budget and ramping
        # to full over a few minutes smooths the burst so the -1003 IP ban
        # never trips. time.monotonic() at creation ≈ process startup.
        self.warmup_seconds = warmup_seconds
        self.warmup_floor_ratio = min(max(warmup_floor_ratio, 0.05), 1.0)
        self._started_at = time.monotonic()
        self._tokens = float(max_weight_per_min)   # start full
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()
        # Telemetry for the testing suite / monitoring
        self.total_weight_consumed = 0
        self.calls_made = 0
        self.waits_seconds = 0.0

    def _current_max_weight(self) -> float:
        """Ramped weight budget: floor ratio at startup, linear to full after warmup."""
        elapsed = time.monotonic() - self._started_at
        if elapsed >= self.warmup_seconds or self.warmup_seconds <= 0:
            return float(self.max_weight_per_min)
        progress = elapsed / self.warmup_seconds
        ratio = self.warmup_floor_ratio + (1.0 - self.warmup_floor_ratio) * progress
        return float(self.max_weight_per_min) * ratio

    def _refill(self):
        """Refill tokens continuously at current (possibly ramped) weight budget per second."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        max_w = self._current_max_weight()
        self._tokens = min(
            max_w,
            self._tokens + elapsed * (max_w / 60.0)
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
                wait_needed = deficit / (self._current_max_weight() / 60.0)

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
                'current_budget': round(self._current_max_weight(), 2),
                'warmup_seconds': self.warmup_seconds,
                'tokens_available': round(self._tokens, 2),
                'total_weight_consumed': self.total_weight_consumed,
                'calls_made': self.calls_made,
                'waits_seconds': round(self.waits_seconds, 2),
            }


# ---------------------------------------------------------------------------
# Global singleton: ONE shared token bucket per process/host IP.
# Previously every CCXTExchangeClient instance built its OWN RateLimiter
# (1500/min each), so combined REST weight across clients/threads/scripts on
# the same IP could exceed Binance's 2,400/min cap and trip -1003.
# All clients now drain the same budget, keeping the discovery burst
# pre-throttled under the IP cap regardless of which code path is running.
# ---------------------------------------------------------------------------
_GLOBAL_LIMITER: Optional['RateLimiter'] = None
_GLOBAL_LIMITER_LOCK = threading.Lock()


def get_global_rate_limiter(max_weight_per_min: int = 1500, logger=None,
                            warmup_seconds: float = 180.0,
                            warmup_floor_ratio: float = 0.30) -> 'RateLimiter':
    """Return the process-wide shared RateLimiter, created once on first use.

    Every CCXTExchangeClient instance shares this bucket so per-minute REST
    weight across all code paths stays under Binance's IP cap. First caller's
    config wins (all instances load the same config values).
    """
    global _GLOBAL_LIMITER
    if _GLOBAL_LIMITER is None:
        with _GLOBAL_LIMITER_LOCK:
            if _GLOBAL_LIMITER is None:
                _GLOBAL_LIMITER = RateLimiter(
                    max_weight_per_min=max_weight_per_min,
                    logger=logger,
                    warmup_seconds=warmup_seconds,
                    warmup_floor_ratio=warmup_floor_ratio,
                )
    return _GLOBAL_LIMITER


def reset_global_rate_limiter() -> None:
    """Clear the shared instance (tests / tooling only)."""
    global _GLOBAL_LIMITER
    with _GLOBAL_LIMITER_LOCK:
        _GLOBAL_LIMITER = None