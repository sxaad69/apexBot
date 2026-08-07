"""
Unit tests for the global token-bucket RateLimiter (Task 1.2).
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exchange.rate_limiter import RateLimiter


def test_initial_tokens_full():
    rl = RateLimiter(max_weight_per_min=1500)
    assert rl.stats()['tokens_available'] == 1500.0


def test_acquire_consumes_weight():
    rl = RateLimiter(max_weight_per_min=100)
    assert rl.acquire(weight=10, timeout=1) is True
    assert rl.stats()['calls_made'] == 1
    assert rl.stats()['total_weight_consumed'] == 10


def test_budget_exhaustion_waits_for_refill():
    rl = RateLimiter(max_weight_per_min=60)
    # Consume entire budget
    assert rl.acquire(weight=60, timeout=0) is True
    # Second call must wait (or timeout) since budget exhausted
    assert rl.acquire(weight=60, timeout=0.5) is False
    # After refill at 60/min = 1 token/sec, wait ~1.1s for 1 token
    time.sleep(1.1)
    assert rl.acquire(weight=1, timeout=1) is True


def test_zero_weight_always_succeeds():
    rl = RateLimiter(max_weight_per_min=10)
    assert rl.acquire(weight=0) is True


def test_timeout_returns_false():
    rl = RateLimiter(max_weight_per_min=5)
    assert rl.acquire(weight=5, timeout=0) is True
    assert rl.acquire(weight=5, timeout=0) is False  # No budget, no wait -> False


if __name__ == '__main__':
    test_initial_tokens_full()
    test_acquire_consumes_weight()
    test_budget_exhaustion_waits_for_refill()
    test_zero_weight_always_succeeds()
    test_timeout_returns_false()
    print("✅ test_rate_limiter.py: all tests passed")