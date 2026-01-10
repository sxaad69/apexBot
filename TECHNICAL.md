# APEX HUNTER V14 - TECHNICAL DOCUMENTATION

Complete technical reference for the Apex Hunter V14 trading system architecture, file structure, and implementation details.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [File Structure](#file-structure)
3. [Core Components](#core-components)
4. [Risk Management System](#risk-management-system)
5. [API Integration](#api-integration)
6. [Logging System](#logging-system)
7. [Data Flow](#data-flow)
8. [Extension Guide](#extension-guide)

---

## Architecture Overview

Apex Hunter V14 follows a modular architecture with clear separation of concerns:

```
┌─────────────────────────────────────────────────────────────┐
│                        Main Application                      │
│                         (main.py)                           │
└──────────────────────┬───────────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        │              │              │
        ▼              ▼              ▼
  ┌─────────┐   ┌─────────┐   ┌─────────────┐
  │ Trading │   │   Risk  │   │   Exchange  │
  │ Engine  │◄──┤ Manager │◄──┤   API      │
  └────┬────┘   └────┬────┘   └──────┬──────┘
       │             │                 │
       │             │                 │
       ▼             ▼                 ▼
  ┌─────────┐   ┌─────────┐   ┌──────────────┐
  │Position │   │11 Risk  │   │ KuCoin       │
  │ Manager │   │ Layers  │   │ Client       │
  └─────────┘   └─────────┘   └──────────────┘
       │             │                 │
       └─────────────┴─────────────────┘
                     │
              ┌──────┴───────┐
              │              │
              ▼              ▼
        ┌─────────┐   ┌──────────┐
        │ Logger  │   │ Telegram │
        └─────────┘   └──────────┘
```

---

## File Structure

### Sequential File Reference

This section describes each file in the project sequentially, explaining its purpose and key functionality.

---

### 1. Configuration Files

#### 1.1 `.env.example`
**Purpose**: Template for environment configuration  
**Location**: Root directory  
**Description**: Contains all configurable parameters with explanations. Users copy this to `.env` and fill in their values.

**Key Sections**:
- KuCoin API credentials
- Trading parameters
- Risk management limits
- Logging configuration
- Telegram settings
- Circuit breaker settings (including configurable halt duration)

**Usage**:
```bash
cp .env.example .env
# Edit .env with actual values
```

---

#### 1.2 `requirements.txt`
**Purpose**: Python package dependencies  
**Location**: Root directory  
**Description**: Lists all required Python packages with version pinning for reproducibility.

**Key Dependencies**:
- `requests`: HTTP client for API calls
- `python-telegram-bot`: Telegram integration
- `pandas/numpy`: Data analysis
- `python-dotenv`: Environment variable loading
- `cryptography`: API signature generation

---

#### 1.3 `config/__init__.py`
**Purpose**: Configuration module initialization  
**Location**: `config/`  
**Description**: Exports the `Config` class for use throughout the application.

---

#### 1.4 `config/config.py`
**Purpose**: Central configuration management  
**Location**: `config/`  
**Description**: Loads, validates, and provides access to all configuration parameters.

**Key Features**:
- Environment variable loading
- Configuration validation
- Type conversion (string to bool, int, float)
- Dynamic adjustments (drawdown-based sizing and leverage)
- Directory creation

**Key Methods**:
- `__init__()`: Load and validate configuration
- `get_drawdown_adjusted_position_size()`: Calculate position size multiplier
- `get_drawdown_adjusted_leverage()`: Calculate leverage adjustment
- `is_live_trading()`: Check if in live mode
- `is_production_environment()`: Check if production

**Usage Example**:
```python
from config import Config

config = Config()
if config.is_live_trading():
    # Live trading logic
    pass
```

---

### 2. Logging System

#### 2.1 `bot_logging/__init__.py`
**Purpose**: Logging module initialization  
**Location**: `bot_logging/`  
**Description**: Exports `Logger` and `LogCategory` classes.

---

#### 2.2 `bot_logging/logger.py`
**Purpose**: Dynamic category-based logging system  
**Location**: `bot_logging/`  
**Description**: Implements zero-overhead logging with independently controllable categories.

**Key Features**:
- 8 log categories (API calls, rejections, metrics, etc.)
- Zero overhead when disabled
- File rotation
- Multiple output targets (console, file, telegram)
- Runtime enable/disable

**Log Categories**:
1. API_CALLS - HTTP requests/responses
2. POSITION_REJECTIONS - Trade rejection reasons
3. TOKEN_METRICS - API usage tracking
4. RISK_MANAGEMENT - Risk layer activations
5. TRADE_EXECUTION - Trade lifecycle
6. PERFORMANCE - P&L metrics
7. SYSTEM_EVENTS - System status
8. ERROR_TRACES - Errors (always enabled)

**Key Methods**:
- `is_enabled(category)`: Check if category enabled
- `enable_category(category)`: Enable logging
- `disable_category(category)`: Disable logging
- `api_call()`: Log API request
- `position_rejected()`: Log rejected position
- `trade_entry()`: Log trade entry
- `trade_exit()`: Log trade exit
- `risk_layer_triggered()`: Log risk event

**Usage Example**:
```python
from logging import Logger, LogCategory

logger = Logger(config)
logger.api_call('GET', '/api/v1/positions', status=200, duration=0.25)
logger.position_rejected('BTCUSDT', 'Insufficient liquidity', 'LiquidityCheck')
```

---

### 3. Exchange Integration

#### 3.1 `exchange/__init__.py`
**Purpose**: Exchange module initialization  
**Location**: `exchange/`  
**Description**: Exports `KuCoinClient` and `APIManager`.

---

#### 3.2 `exchange/kucoin_client.py`
**Purpose**: KuCoin Futures API client  
**Location**: `exchange/`  
**Description**: Handles KuCoin authentication, request signing, and endpoint definitions.

**Key Features**:
- HMAC-SHA256 signature generation
- API v2 passphrase encryption
- Endpoint builders for all trading operations
- Request parameter formatting

**Key Methods**:
- `_generate_signature()`: Create request signature
- `_get_headers()`: Generate auth headers
- `get_account_overview()`: Fetch account data
- `get_ticker()`: Get price data
- `place_order()`: Place order
- `close_position()`: Close position
- `get_positions()`: Fetch open positions

**Usage Example**:
```python
from exchange import KuCoinClient

kucoin = KuCoinClient(config, logger)
order_data = kucoin.place_order('BTCUSDT', 'buy', 10, 100)
```

---

#### 3.3 `exchange/api_manager.py`
**Purpose**: HTTP request manager with retry logic  
**Location**: `exchange/`  
**Description**: Handles actual HTTP requests with error handling, rate limiting, and retries.

**Key Features**:
- Automatic retry on failures
- Rate limit enforcement
- Error threshold monitoring
- Request tracking
- Timeout handling

**Key Methods**:
- `request()`: Execute API request with retries
- `_check_rate_limit()`: Verify rate limit compliance
- `_check_error_threshold()`: Check error limits
- `_record_request()`: Track successful request
- `_record_error()`: Track failed request

**Usage Example**:
```python
from exchange import APIManager, KuCoinClient

api_manager = APIManager(config, logger)
kucoin = KuCoinClient(config, logger)

req = kucoin.get_account_overview()
response = api_manager.request(
    req['method'],
    req['url'],
    req['headers'],
    endpoint=req['endpoint']
)
```

---

### 4. Risk Management System

#### 4.1 `risk/__init__.py`
**Purpose**: Risk module initialization  
**Location**: `risk/`  
**Description**: Exports the main `RiskManager` class.

---

#### 4.2 `risk/risk_manager.py`
**Purpose**: Risk management coordinator  
**Location**: `risk/`  
**Description**: Orchestrates all 11 risk layers and evaluates trades sequentially.

**Key Features**:
- Sequential layer evaluation
- Trade approval/rejection
- Trade result recording
- Circuit breaker interface

**Key Methods**:
- `evaluate_trade()`: Pass trade through all layers
- `record_trade_result()`: Update layers with result
- `record_critical_failure()`: Trigger circuit breaker
- `is_trading_halted()`: Check if halted
- `update_peak_balance()`: Update drawdown calculation

**Usage Example**:
```python
from risk import RiskManager

risk_mgr = RiskManager(config, logger)

trade_params = {
    'symbol': 'BTCUSDT',
    'side': 'buy',
    'entry_price': 50000
}

account_state = {
    'total_balance': 20,
    'available_balance': 18,
    'drawdown_percent': 2.5,
    'open_positions_count': 1
}

approved = risk_mgr.evaluate_trade(trade_params, account_state)
if approved:
    # Execute trade
    pass
```

---

#### 4.3 `risk/layers/__init__.py`
**Purpose**: Risk layers module initialization  
**Location**: `risk/layers/`  
**Description**: Exports all 11 risk layer classes.

---

### 5. Risk Layers (Sequential)

All risk layers follow the same interface pattern:

```python
class LayerName:
    def __init__(self, config, logger):
        # Initialize with config and logger
        pass
    
    def evaluate(self, trade_params, account_state):
        # Evaluate trade
        # Return approved params or None if rejected
        pass
```

---

#### 5.1 `risk/layers/position_sizing.py`
**Layer**: 1  
**Purpose**: Calculate appropriate position size  
**Description**: Determines position size based on capital, risk percentage, and current drawdown.

**Logic**:
1. Get available capital
2. Apply position size percentage
3. Adjust for drawdown (reduces size as drawdown increases)
4. Enforce min/max limits
5. Reject if below minimum or drawdown too high

---

#### 5.2 `risk/layers/leverage_control.py`
**Layer**: 2  
**Purpose**: Control leverage usage  
**Description**: Enforces maximum leverage and reduces it based on drawdown.

**Logic**:
1. Get current drawdown
2. Calculate adjusted max leverage
3. Compare requested vs allowed
4. Reduce if necessary
5. Reject if leverage not allowed

---

#### 5.3 `risk/layers/stop_loss_management.py`
**Layer**: 3  
**Purpose**: Automatic stop-loss placement  
**Description**: Calculates and sets stop-loss prices for all positions.

**Logic**:
1. Get entry price and side
2. Calculate stop price (entry ± stop_loss_percent)
3. Add stop loss to trade params
4. Always approves (adds stop loss, doesn't reject)

---

#### 5.4 `risk/layers/daily_loss_limit.py`
**Layer**: 4  
**Purpose**: Enforce daily loss limits  
**Description**: Tracks daily P&L and halts trading when limit reached.

**Logic**:
1. Reset P&L if new trading day
2. Check cumulative daily loss
3. Compare to maximum allowed
4. Reject if limit exceeded
5. Continue if within limits

**State Tracking**:
- Maintains `daily_pnl`
- Tracks `current_date`
- Auto-resets at midnight

---

#### 5.5 `risk/layers/maximum_drawdown.py`
**Layer**: 5  
**Purpose**: Protect against excessive drawdown  
**Description**: Monitors peak-to-trough drawdown and halts when exceeded.

**Logic**:
1. Track peak balance (high water mark)
2. Calculate current drawdown from peak
3. Compare to maximum allowed
4. Reject and log if exceeded
5. Trigger circuit breaker on breach

**State Tracking**:
- Maintains `peak_balance`
- Updates peak on balance increases

---

#### 5.6 `risk/layers/correlation_risk.py`
**Layer**: 6  
**Purpose**: Prevent correlated position concentration  
**Description**: Limits number of correlated positions to prevent concentration risk.

**Logic**:
1. Check number of open positions
2. Compare to maximum allowed
3. Reject if limit reached
4. (Future: Calculate actual correlations)

---

#### 5.7 `risk/layers/volatility_adjustment.py`
**Layer**: 7  
**Purpose**: Adjust for market volatility  
**Description**: Modifies position sizing and stop distances based on recent volatility.

**Logic**:
1. Calculate recent volatility
2. Adjust position size down in high volatility
3. Widen stop losses in volatile markets
4. (Currently passthrough - ready for implementation)

---

#### 5.8 `risk/layers/liquidity_check.py`
**Layer**: 8  
**Purpose**: Verify sufficient liquidity  
**Description**: Ensures order book depth is sufficient for trade execution.

**Logic**:
1. Fetch order book data
2. Calculate depth on both sides
3. Estimate slippage
4. Reject if liquidity insufficient
5. (Currently passthrough - ready for implementation)

---

#### 5.9 `risk/layers/rate_limit.py`
**Layer**: 9  
**Purpose**: API rate limit management  
**Description**: Prevents API rate limit violations (handled by APIManager).

**Logic**:
- Passthrough layer
- Rate limiting enforced by APIManager
- Prevents trade if API at limit

---

#### 5.10 `risk/layers/circuit_breaker.py`
**Layer**: 10  
**Purpose**: Emergency trading halt  
**Description**: Automatically halts trading on critical failures or consecutive losses.

**Key Features**:
- **Configurable halt duration** via `TRADE_FAILURE_HALT_HOURS`
- Consecutive loss tracking
- Flash crash detection
- Critical failure recording
- Time-based halt management

**Logic**:
1. Check if currently halted
2. If halted, reject and show time remaining
3. Track consecutive losses
4. Trigger halt if threshold exceeded
5. Detect flash crash conditions
6. Auto-resume after halt period

**State Tracking**:
- `consecutive_losses`: Counter
- `halt_until`: DateTime of halt expiration
- `last_trade_time`: Timestamp tracking

**Configuration**:
```env
TRADE_FAILURE_HALT_HOURS=48  # Halt duration (0 to disable)
CONSECUTIVE_LOSSES_THRESHOLD=5
FLASH_CRASH_THRESHOLD=-10  # % drop to trigger
```

**Usage Example**:
```python
# Record trade result
circuit_breaker.record_trade_result(is_win=False)  # Increments counter

# Record critical failure
circuit_breaker.record_critical_failure("API connection lost")

# Check if halted
if circuit_breaker.is_halted():
    # Trading is halted
    pass
```

---

#### 5.11 `risk/layers/capital_preservation.py`
**Layer**: 11  
**Purpose**: Maintain minimum capital  
**Description**: Reserves minimum capital to prevent complete depletion.

**Logic**:
1. Calculate minimum threshold (10% of initial)
2. Check current balance
3. Reject if below threshold
4. Ensures capital for position management

---

### 6. Core Trading Components

#### 6.1 `core/__init__.py`
**Purpose**: Core module initialization  
**Location**: `core/`  
**Description**: Exports trading engine and position manager.

---

#### 6.2 `core/trading_engine.py`
**Purpose**: Main trading logic coordinator  
**Location**: `core/`  
**Description**: Orchestrates trading decisions, risk evaluation, and order execution.

**Key Responsibilities**:
- Signal generation/reception
- Risk evaluation
- Order placement
- Position monitoring
- P&L tracking

---

#### 6.3 `core/position_manager.py`
**Purpose**: Position tracking and management  
**Location**: `core/`  
**Description**: Tracks all open positions, calculates P&L, manages exits.

**Key Features**:
- Open position tracking
- Real-time P&L calculation
- Stop-loss monitoring
- Position history

---

### 7. Notification System

#### 7.1 `notifications/__init__.py`
**Purpose**: Notifications module initialization  
**Location**: `notifications/`  
**Description**: Exports Telegram bot class.

---

#### 7.2 `notifications/telegram_bot.py`
**Purpose**: Telegram integration  
**Location**: `notifications/`  
**Description**: Sends notifications and handles remote commands.

**Key Features**:
- Trade notifications
- Performance summaries
- System alerts
- Remote commands (/status, /pause, /resume, etc.)

---

### 8. Utility Functions

#### 8.1 `utils/__init__.py`
**Purpose**: Utils module initialization  
**Location**: `utils/`  
**Description**: Exports utility functions.

---

#### 8.2 `utils/helpers.py`
**Purpose**: Common utility functions  
**Location**: `utils/`  
**Description**: Shared helper functions used across the application.

**Functions**:
- `calculate_position_value()`: Position value calculation
- `format_timestamp()`: DateTime formatting
- `calculate_pnl_percent()`: P&L percentage
- `format_duration()`: Human-readable duration

---

### 9. Application Entry Point

#### 9.1 `main.py`
**Purpose**: Application entry point  
**Location**: Root directory  
**Description**: Initializes all components and starts the trading bot.

**Startup Sequence**:
1. Load configuration
2. Initialize logger
3. Initialize exchange clients
4. Initialize risk manager
5. Initialize trading engine
6. Start Telegram bot
7. Begin trading loop

---

## Data Flow

### Trade Execution Flow

```
1. Signal Generation
   ↓
2. Create Trade Parameters
   ↓
3. Risk Manager Evaluation
   ├→ Layer 1: Position Sizing
   ├→ Layer 2: Leverage Control
   ├→ Layer 3: Stop Loss
   ├→ Layer 4: Daily Loss Limit
   ├→ Layer 5: Maximum Drawdown
   ├→ Layer 6: Correlation Risk
   ├→ Layer 7: Volatility Adjustment
   ├→ Layer 8: Liquidity Check
   ├→ Layer 9: Rate Limit
   ├→ Layer 10: Circuit Breaker ← Configurable Halt
   └→ Layer 11: Capital Preservation
   ↓
4a. If Rejected → Log & Notify
4b. If Approved → Continue
   ↓
5. API Manager (Rate Limit Check)
   ↓
6. KuCoin Client (Order Preparation)
   ↓
7. HTTP Request (Order Placement)
   ↓
8. Position Manager (Track Position)
   ↓
9. Telegram Notification
   ↓
10. Logger (Record Trade)
```

### Configuration Flow

```
.env file
   ↓
dotenv.load()
   ↓
Config.__init__()
   ├→ Load variables
   ├→ Type conversion
   ├→ Validation
   └→ Directory creation
   ↓
Config object available to all components
```

---

## Extension Guide

### Adding a New Risk Layer

1. Create new file in `risk/layers/`:
```python
# risk/layers/my_new_layer.py
from typing import Dict, Any, Optional

class MyNewLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params, account_state):
        # Your logic here
        if should_reject:
            self.logger.position_rejected(...)
            return None
        return trade_params
```

2. Add to `risk/layers/__init__.py`:
```python
from .my_new_layer import MyNewLayer
__all__ = [..., 'MyNewLayer']
```

3. Add to `risk/risk_manager.py`:
```python
from .layers import ..., MyNewLayer

self.layers = [
    ...,
    MyNewLayer(config, logger),
]
```

### Adding New Log Category

1. Add to `LogCategory` enum in `bot_logging/logger.py`:
```python
class LogCategory(Enum):
    MY_CATEGORY = "my_category"
```

2. Add configuration in `.env.example`:
```env
LOG_MY_CATEGORY=true
```

3. Add to config loading in `config/config.py`:
```python
self.LOG_MY_CATEGORY = self._str_to_bool(os.getenv('LOG_MY_CATEGORY', 'true'))
```

4. Add to logger category states:
```python
self._category_states = {
    ...,
    LogCategory.MY_CATEGORY: self.config.LOG_MY_CATEGORY,
}
```

5. Create logging method:
```python
def my_category_log(self, message, **kwargs):
    self._log(LogCategory.MY_CATEGORY, logging.INFO, message, **kwargs)
```

---

## Performance Considerations

### Zero-Overhead Logging

When a log category is disabled:
```python
if not self._category_states.get(category, False):
    return  # Immediate return, no processing
```

This means disabled logging has near-zero performance impact.

### API Rate Limiting

- Tracks requests per endpoint
- Implements sliding window (1-hour)
- Respects `RATE_LIMIT_BUFFER` configuration
- Prevents rate limit violations proactively

### Memory Management

- Position history limited by configuration
- Log file rotation prevents disk overflow
- Old request data automatically cleaned

---

## Testing Recommendations

### Unit Testing

Test each component independently:
```python
# Test risk layer
layer = PositionSizingLayer(config, logger)
result = layer.evaluate(trade_params, account_state)
assert result is not None
assert 'position_size' in result
```

### Integration Testing

Test component interactions:
```python
# Test risk manager with all layers
risk_mgr = RiskManager(config, logger)
approved = risk_mgr.evaluate_trade(params, state)
```

### System Testing

Full end-to-end testing in simulation mode:
- Set `TRADING_MODE=simulation`
- Set `KUCOIN_ENVIRONMENT=sandbox`
- Run for 24-48 hours
- Monitor all logs
- Verify no unexpected behavior

---

## Configuration Best Practices

### Development
```env
TRADING_MODE=simulation
KUCOIN_ENVIRONMENT=sandbox
LOG_LEVEL=DEBUG
# Enable all log categories
LOG_API_CALLS=true
LOG_POSITION_REJECTIONS=true
...
```

### Production
```env
TRADING_MODE=live
KUCOIN_ENVIRONMENT=production
LOG_LEVEL=INFO
# Disable verbose categories
LOG_API_CALLS=false
LOG_TOKEN_METRICS=false
# Keep critical logs
LOG_RISK_MANAGEMENT=true
LOG_TRADE_EXECUTION=true
LOG_ERROR_TRACES=true
```

---

## Deployment Checklist

- [ ] Python 3.11+ installed
- [ ] Virtual environment created
- [ ] Dependencies installed
- [ ] `.env` configured with correct values
- [ ] KuCoin API credentials tested
- [ ] Telegram bot created and tested
- [ ] Sandbox testing completed (24-48h)
- [ ] Risk parameters reviewed
- [ ] Emergency procedures documented
- [ ] Monitoring setup configured
- [ ] Backup strategy implemented

---

## Maintenance

### Daily
- Review Telegram notifications
- Check log files for errors
- Verify API connectivity
- Monitor position count
- Check circuit breaker status

### Weekly
- Review performance metrics
- Analyze rejection logs
- Adjust risk parameters if needed
- Clean old log files
- Update peak balance

### Monthly
- Full system audit
- Update dependencies
- Review and optimize strategies
- Backup configuration and data
- Test emergency procedures

---

**Version**: 14.0  
**Last Updated**: 2026-01-02  
**Maintainer**: Apex Hunter Development Team

---

## Quick Reference

### Import Patterns
```python
from config import Config
from logging import Logger, LogCategory
from risk import RiskManager
from exchange import KuCoinClient, APIManager
from notifications import TelegramBot
```

### Common Operations
```python
# Initialize
config = Config()
logger = Logger(config)
risk_mgr = RiskManager(config, logger)

# Evaluate trade
approved = risk_mgr.evaluate_trade(params, state)

# Check if halted
if risk_mgr.is_trading_halted():
    # Handle halt

# Record result
risk_mgr.record_trade_result(is_win=True, pnl=10.5)
```

---

End of Technical Documentation
