# Satellite Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a WebSocket-driven, zero-latency trailing stop and exit sentinel.

**Architecture:** We will create a background WebSocket manager that subscribes to Binance's global mark price stream. The Sentinel loop will be decoupled from the heavy REST scanner and will instead poll the WebSocket memory dictionary every 0.5s. The 3-second delay filter will be removed, and instant SQLite persistence will be added to prevent state amnesia during syncs.

**Tech Stack:** Python, websockets, asyncio, pytest.

---

### Task 1: Uncage the Moonshot (Remove 3s Filter)

**Files:**
- Modify: `risk/layers/trailing_stop.py`
- Test: `tests/test_trailing_stop.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import MagicMock
from risk.layers.trailing_stop import TrailingStopLayer

def test_instant_activation():
    # Setup mock config and layer
    config = MagicMock()
    config.TRAILING_STOP_ACTIVATION = 5.0
    config.TRAILING_STOP_DISTANCE = 3.0
    layer = TrailingStopLayer(config, MagicMock(), MagicMock(), MagicMock())
    
    position = {
        'trade_id': '123',
        'symbol': 'LAB/USDT',
        'side': 'buy',
        'entry_price': 100.0,
        'stop_loss': 90.0,
        'highest_price': 100.0,
        'trailing_stop_active': False
    }
    
    # Simulate an 8% spike
    layer.update_position_ratchet(position, 108.0)
    
    # Assert it activates immediately (no 3s delay)
    assert position['trailing_stop_active'] is True
    assert position['stop_loss'] == 108.0 * (1 - 0.03)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_trailing_stop.py -v`
Expected: FAIL (because it requires 3 seconds of sustain)

- [ ] **Step 3: Write minimal implementation**
In `risk/layers/trailing_stop.py`:
Delete lines ~91-94 (the `trailing_activation_time` check).

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_trailing_stop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_trailing_stop.py risk/layers/trailing_stop.py
git commit -m "fix: remove 3s delay filter from trailing stop"
```

---

### Task 2: Immediate Database Persistence

**Files:**
- Modify: `main.py`
- Test: `tests/test_main_persistence.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from unittest.mock import MagicMock
from main import ApexHunter  # Adjust import based on actual class name

def test_persist_stop_watermark_called():
    bot = ApexHunter(MagicMock())
    bot.positions = {
        'LAB/USDT:USDT': {
            'trade_id': '123',
            'symbol': 'LAB/USDT',
            'side': 'buy',
            'entry_price': 100.0,
            'highest_price': 100.0,
            'stop_loss': 90.0,
            'trailing_stop_active': False,
            'strategy': 'A6'
        }
    }
    bot._persist_stop_watermark = MagicMock()
    bot.config.TRAILING_STOP_ACTIVATION = 5.0
    bot.config.TRAILING_STOP_DISTANCE = 3.0
    
    # 8% spike
    bot.update_trailing_stops('LAB/USDT', 108.0)
    
    # Assert _persist_stop_watermark was called with the new peak
    bot._persist_stop_watermark.assert_called_with('123', peak=108.0)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_main_persistence.py -v`
Expected: FAIL (mock not called)

- [ ] **Step 3: Write minimal implementation**
In `main.py`, duplicate `_persist_tp_watermark` and name it `_persist_stop_watermark`. Update `meta['trailing_stop_peak_price']` and `meta['trailing_stop_active']`.
In `update_trailing_stops`, call `self._persist_stop_watermark(position['trade_id'], peak=current_price)` where `highest_price` is updated and activated.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_main_persistence.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_main_persistence.py main.py
git commit -m "fix: add immediate database persistence for trailing stops"
```

---

### Task 3: The WebSocket Manager

**Files:**
- Create: `exchange/wss_manager.py`
- Test: `tests/test_wss_manager.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
import json
from exchange.wss_manager import BinanceFuturesWSSManager

def test_wss_manager_parsing():
    manager = BinanceFuturesWSSManager()
    
    payload = json.dumps([
        {"s": "BTCUSDT", "p": "65000.50"},
        {"s": "LABUSDT", "p": "4.60"}
    ])
    
    manager._handle_message(payload)
    
    assert manager.live_prices["BTC/USDT"] == 65000.50
    assert manager.live_prices["LAB/USDT"] == 4.60
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_wss_manager.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Write minimal implementation**
Create `exchange/wss_manager.py` with an async loop connecting to `wss://fstream.binance.com/ws/!markPrice@arr@1s`. It parses `s` (symbol) and `p` (price), converts `BTCUSDT` to `BTC/USDT`, and updates `self.live_prices`.

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_wss_manager.py -v`
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add tests/test_wss_manager.py exchange/wss_manager.py
git commit -m "feat: implement global mark price websocket manager"
```

---

### Task 4: Decouple the Sentinel

**Files:**
- Modify: `main.py`
- Test: `tests/test_sentinel.py`

- [ ] **Step 1: Write the failing test**
```python
import pytest
from unittest.mock import MagicMock
from main import ApexHunter

def test_sentinel_uses_wss():
    bot = ApexHunter(MagicMock())
    bot.wss_manager = MagicMock()
    bot.wss_manager.live_prices = {"LAB/USDT": 5.0}
    
    bot.positions = {
        'LAB/USDT:USDT': {'symbol': 'LAB/USDT', 'side': 'buy'}
    }
    
    bot.update_trailing_stops = MagicMock()
    
    # Manually run one iteration of the new sentinel logic
    bot._sentinel_tick()
    
    bot.update_trailing_stops.assert_called_with('LAB/USDT', 5.0)
```

- [ ] **Step 2: Run test to verify it fails**
Run: `pytest tests/test_sentinel.py -v`

- [ ] **Step 3: Write minimal implementation**
In `main.py`:
1. Start `BinanceFuturesWSSManager` in `run()`.
2. Rewrite `_run_priority_exit_thread` into `_sentinel_tick()` (for testability) and the while loop.
   - Iterate `self.engine.positions`.
   - Fetch price from `self.wss_manager.live_prices`.
   - Call `update_trailing_stops` and `update_trailing_take_profit`.
   - Call `check_position_exit` and execute if needed.
   - `time.sleep(0.5)`

- [ ] **Step 4: Run test to verify it passes**
Run: `pytest tests/test_sentinel.py -v`

- [ ] **Step 5: Commit**
```bash
git add tests/test_sentinel.py main.py
git commit -m "refactor: decouple sentinel to use wss for 0ms exits"
```
