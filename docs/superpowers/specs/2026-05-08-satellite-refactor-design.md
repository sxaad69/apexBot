# Satellite Refactor Design Specification

## Overview
The "Satellite" refactor addresses three core systemic failures in the ApexBot futures trading engine:
1. **The 15-Second Blind Spot:** The bot misses sub-minute flash pumps because the position exit loop is coupled to the heavy 100-symbol REST scanning loop.
2. **The "Amnesia" Bug:** The Trailing Stop logic updates the `highest_price` peak in memory but fails to persist it to the SQLite database. Subsequent background syncs overwrite the memory, causing the trailing stop to reset to 0%.
3. **The "Dirty Tick" Filter:** The `TrailingStopLayer` requires a profit peak to be sustained for 3 continuous seconds before locking it in. This prevents the bot from capturing the absolute peak of volatile moonshots.

## Proposed Architecture

### 1. `exchange/wss_manager.py` (The Real-Time Feed)
- **Component:** A new `BinanceFuturesWSSManager` class running in a daemon thread.
- **Functionality:** Subscribes to the `!markPrice@arr@1s` WebSocket stream.
- **Data Structure:** Parses the array payload and updates a thread-safe dictionary `self.live_prices = {"BTC/USDT": 65000.5, "LAB/USDT": 4.60}`.

### 2. `main.py` (The Decoupled Sentinel)
- **Component:** Refactor of `_run_priority_exit_thread`.
- **Functionality:** 
  - Detaches from `engine.run_cycle`.
  - Reads `wss_manager.live_prices`.
  - Iterates exclusively over `self.engine.positions`.
  - Calls `update_trailing_stops` and executes exits instantly with a 0.5s sleep cycle.

### 3. `main.py` (Immediate DB Persistence)
- **Component:** Refactor of `update_trailing_stops`.
- **Functionality:** Adds `self._persist_stop_watermark(position['trade_id'], peak=current_price)` exactly when the 5% threshold is crossed or a new peak is reached, ensuring database alignment with memory.

### 4. `risk/layers/trailing_stop.py` (Uncaging the Moonshot)
- **Component:** Refactor of `update_position_ratchet`.
- **Functionality:** Removes the 3-second `trailing_activation_time` check. Profit ratchets are locked in the millisecond the threshold is breached.

## Automated Testing Strategy

To guarantee the reliability of these risk-critical systems, an automated test suite (`tests/test_satellite_refactor.py`) will be implemented using `pytest`.

### Test Case 1: WebSocket Array Parsing
- **Input:** A simulated `!markPrice@arr@1s` JSON payload containing 270 coins.
- **Expected Output:** The `live_prices` dictionary updates accurately in <5ms without blocking the main thread.

### Test Case 2: Instant Trailing Stop Activation (No Delay)
- **Setup:** A mock long position with an entry price of $100.
- **Action:** Feed a simulated price spike of $108 (8% profit).
- **Assertion:** The trailing stop activates immediately (True), the SL is ratcheted to +5%, and the 3-second timer is verified to be completely absent from the logic path.

### Test Case 3: Memory-to-Database Sync Verification
- **Setup:** A mock position with a 0% peak in the SQLite database.
- **Action:** The Sentinel feeds an 8% price spike to `update_trailing_stops`.
- **Assertion:** A mock database interceptor verifies that an `UPDATE trades SET metadata...` query is fired instantly, persisting the new 8% peak before any other background thread can read the old state.

## Blast Radius & Impact Analysis
- **API Limits:** Binance REST API `429 Too Many Requests` errors will drop to near zero for active positions.
- **Performance:** CPU overhead will increase slightly (parsing WSS JSON), but network I/O latency will drop significantly.
- **Risk Profile:** Stop losses will become much more aggressive, resulting in tighter profit lock-ins but potentially higher instances of getting stopped out on small pullbacks.
