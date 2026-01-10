# 🛡️ APEX HUNTER V14 - Risk Management System

## ✅ ALL RISK LAYERS NOW ACTIVE IN PAPER TRADING!

Your bot now validates **EVERY** trade through all **11 risk management layers** before execution.

---

## 📊 The 11-Layer Risk System

### ✅ Layer 1: Position Sizing
**What it does:**
- Limits position size to configured percentage of capital
- Default: 10% of capital per trade
- Prevents over-concentration in a single position

**Configuration:**
```env
FUTURES_POSITION_SIZE_PERCENT=10
```

**Will Reject:**
- Trades larger than 10% of capital
- Trades that would exceed MAX_POSITION_SIZE

---

### ✅ Layer 2: Leverage Control
**What it does:**
- Limits leverage based on confidence and drawdown
- **Dynamic leverage:** Higher confidence = higher leverage
- Reduces leverage automatically during drawdowns

**Configuration:**
```env
FUTURES_MAX_LEVERAGE=10
```

**Example:**
- Confidence 50% → 5x leverage
- Confidence 75% → 7.5x leverage
- Confidence 100% → 10x leverage
- **During 10% drawdown** → Leverage reduced by 50%

**Will Reject:**
- Trades with leverage > MAX_LEVERAGE
- Trades with excessive leverage during drawdowns

---

### ✅ Layer 3: Stop Loss Management
**What it does:**
- Ensures every trade has a stop loss
- Validates stop loss is reasonable (not too tight/wide)

**Configuration:**
```env
FUTURES_STOP_LOSS_PERCENT=2
```

**Will Reject:**
- Trades without stop loss
- Stop loss > 5% away from entry

---

### ✅ Layer 4: Daily Loss Limit
**What it does:**
- Tracks daily P&L across all strategies
- Halts trading if daily loss exceeds limit
- Resets at midnight

**Configuration:**
```env
FUTURES_MAX_DAILY_LOSS_PERCENT=5
```

**Will Reject:**
- New trades if daily loss >= 5% of capital
- All trades until next trading day

**Status Check:**
```python
# Bot automatically halts if daily loss limit hit
```

---

### ✅ Layer 5: Maximum Drawdown
**What it does:**
- Tracks drawdown from peak balance
- Progressively reduces position size during drawdown
- Halts trading at maximum drawdown

**Configuration:**
```env
FUTURES_MAX_DRAWDOWN_PERCENT=15
```

**Drawdown Response:**
- **0-5% drawdown:** Normal trading
- **5-10% drawdown:** Reduce position size to 67%
- **10-15% drawdown:** Reduce position size to 33%
- **15%+ drawdown:** HALT ALL TRADING

**Will Reject:**
- New trades if drawdown >= 15%
- Automatically adjusts position sizes during drawdown

---

### ✅ Layer 6: Correlation Risk
**What it does:**
- Prevents opening too many correlated positions
- Checks if new position correlates with existing ones

**Configuration:**
```env
CORRELATION_THRESHOLD=0.7
```

**Will Reject:**
- Positions with >70% correlation to existing positions
- Multiple BTC-related positions (BTC, wBTC, BTCB all correlated)

**Example:**
- Already long BTC/USDT → Rejects new long on wBTC/USDT
- Already long ETH/USDT → May reject new long on stETH/USDT

---

### ✅ Layer 7: Volatility Adjustment
**What it does:**
- Reduces position size during high volatility
- Increases stop loss distance in volatile markets

**Configuration:**
```env
VOLATILITY_LOOKBACK_PERIODS=20
```

**Adjustments:**
- **Low volatility:** Normal position sizing
- **High volatility (>2x normal):** Reduce position size by 50%
- **Extreme volatility (>3x normal):** Reduce position size by 75%

**Will Reject:**
- Large positions during extreme volatility
- Tight stop losses in volatile conditions

---

### ✅ Layer 8: Liquidity Check
**What it does:**
- Ensures sufficient market liquidity before trading
- Checks order book depth

**Configuration:**
```env
MIN_LIQUIDITY_DEPTH=10000
```

**Will Reject:**
- Trades on pairs with <$10,000 liquidity
- Large orders that would move the market significantly

---

### ✅ Layer 9: Rate Limit
**What it does:**
- Prevents too many trades in short period
- Protects against API rate limits
- Prevents over-trading

**Will Reject:**
- More than X trades per minute
- Rapid-fire trading that looks like a bug

---

### ✅ Layer 10: Circuit Breaker
**What it does:**
- Emergency halt system
- Triggers on critical failures or losing streaks
- Requires manual intervention to resume

**Configuration:**
```env
CONSECUTIVE_LOSSES_THRESHOLD=5
FLASH_CRASH_THRESHOLD=-10
```

**Triggers:**
- **5 consecutive losses** → Halt for 1 hour
- **10% flash crash** → Immediate halt
- **Critical API errors** → Halt until resolved

**Will Reject:**
- ALL trades when circuit breaker is triggered
- Requires bot restart or manual override

---

### ✅ Layer 11: Capital Preservation
**What it does:**
- Final safety check before trade execution
- Ensures minimum capital remains
- Prevents complete account wipeout

**Will Reject:**
- Trades that would reduce capital below 20% of initial
- Trades during severe drawdown
- Risk of total capital loss

---

## 🎯 How It Works in Your Bot

### Before Every Trade:
```python
1. Strategy generates signal (e.g., "BUY BTC at $90,000")
2. Calculate dynamic leverage (based on confidence)
3. ✅ Layer 1: Check position sizing
4. ✅ Layer 2: Validate leverage
5. ✅ Layer 3: Verify stop loss
6. ✅ Layer 4: Check daily loss limit
7. ✅ Layer 5: Check drawdown status
8. ✅ Layer 6: Analyze correlation
9. ✅ Layer 7: Adjust for volatility
10. ✅ Layer 8: Verify liquidity
11. ✅ Layer 9: Check rate limits
12. ✅ Layer 10: Check circuit breaker
13. ✅ Layer 11: Final capital check

If ALL layers pass → Trade executes ✅
If ANY layer fails → Trade REJECTED ❌
```

### After Every Trade:
```python
1. Calculate P&L with leverage
2. Update strategy capital
3. Update peak balance (for drawdown tracking)
4. Record win/loss with risk manager
5. Check if daily loss limit reached
6. Check if circuit breaker should trigger
```

---

## 📱 Telegram Notifications

You'll see in your notifications:
```
🎯 FUTURES TRADE ENTRY

Symbol: BTC/USDT
Side: BUY
Entry Price: $90,725.10
Leverage: 7x  ← Dynamic leverage
✅ Risk Approved  ← Passed all 11 layers
```

If rejected:
```
⚠️ TRADE REJECTED

Symbol: ETH/USDT
Reason: Daily loss limit reached (-5.2%)
Action: Trading halted until tomorrow
```

---

## 🛡️ Your Configuration

Current settings from `.env`:
```env
# Position Sizing
FUTURES_POSITION_SIZE_PERCENT=10      # 10% per trade
FUTURES_MAX_LEVERAGE=10               # Max 10x leverage

# Stop Loss / Take Profit
FUTURES_STOP_LOSS_PERCENT=2           # 2% stop loss
FUTURES_TAKE_PROFIT_PERCENT=4         # 4% take profit

# Daily/Drawdown Limits
FUTURES_MAX_DAILY_LOSS_PERCENT=5      # Halt at -5% daily
FUTURES_MAX_DRAWDOWN_PERCENT=15       # Halt at -15% drawdown

# Risk Controls
CORRELATION_THRESHOLD=0.7             # Max 70% correlation
MIN_LIQUIDITY_DEPTH=10000             # $10k minimum liquidity

# Circuit Breaker
CONSECUTIVE_LOSSES_THRESHOLD=5        # Halt after 5 losses
FLASH_CRASH_THRESHOLD=-10             # Halt on -10% crash
```

---

## 📊 Example Scenarios

### Scenario 1: Normal Trade (Approved ✅)
```
Strategy: A2 (EMA+RSI)
Signal: BUY ETH/USDT at $3,095
Confidence: 75%
Capital: $100

Risk Checks:
✅ Position size: $10 (10% ✓)
✅ Leverage: 7.5x (75% of 10x max ✓)
✅ Stop loss: 2% ($3,033 ✓)
✅ Daily loss: -$2 (not at limit ✓)
✅ Drawdown: -3% (below 15% ✓)
✅ Correlation: Low (no correlated positions ✓)
✅ Volatility: Normal (no adjustment needed ✓)
✅ Liquidity: $500M (sufficient ✓)
✅ Rate limit: OK ✓
✅ Circuit breaker: Inactive ✓
✅ Capital: $100 (above minimum ✓)

→ TRADE EXECUTED ✅
```

### Scenario 2: Rejected by Daily Loss Limit (❌)
```
Strategy: A3 (Fast Scalp)
Signal: SELL SOL/USDT at $136
Capital: $95 (started at $100)

Risk Checks:
✅ Position size: $9.50 (10% ✓)
✅ Leverage: 6x (60% of 10x max ✓)
✅ Stop loss: 2% ✓
❌ Daily loss: -$5 (LIMIT REACHED! -5%)

→ TRADE REJECTED ❌
Reason: Daily loss limit exceeded
```

### Scenario 3: Rejected by Drawdown (❌)
```
Strategy: A1 (EMA Only)
Signal: BUY BTC/USDT at $90,000
Capital: $83 (peak was $105)

Risk Checks:
✅ Position size: $8.30 (10% ✓)
❌ Drawdown: -21% (EXCEEDS 15% LIMIT)

→ TRADE REJECTED ❌
Reason: Maximum drawdown exceeded
Bot halted until recovery
```

### Scenario 4: Rejected by Correlation (❌)
```
Open Position: LONG BTC/USDT (7x leverage)

New Signal: LONG wBTC/USDT

Risk Checks:
✅ Position size: OK ✓
✅ Leverage: OK ✓
❌ Correlation: 95% with BTC/USDT (EXCEEDS 70%)

→ TRADE REJECTED ❌
Reason: High correlation with existing position
```

---

## 🎯 Summary

**Your bot is NOW protected by:**
- ✅ 11 layers of risk management
- ✅ Dynamic leverage based on confidence
- ✅ Automatic drawdown protection
- ✅ Daily loss limits
- ✅ Circuit breaker system
- ✅ Correlation checks
- ✅ Volatility adjustments
- ✅ Liquidity validation

**Every single trade** goes through all 11 layers before execution.

**Paper trading mode** validates everything just like live trading, so you can test the risk system safely!

---

## 📱 Monitor Risk Status

Check logs for risk rejections:
```bash
flyctl logs -f | grep "REJECTED"
```

Your Telegram will show:
- ✅ when trades are approved
- ❌ when trades are rejected (with reason)
- ⚠️ when limits are approached
- 🚨 when circuit breaker triggers

---

**Your capital is protected! 🛡️**
