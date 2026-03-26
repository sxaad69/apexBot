# APEX HUNTER V14 — Automated Futures Trading Bot

Apex Hunter V14 is an institutional-grade automated trading bot built for Binance Futures. It operates in a strict, layered, "Verify Before Write" architecture ensuring every trade decision is validated across capital, risk, strategy, and exchange confirmation before being committed to the database.

---

## 🏗️ System Execution Flow

Each 60-second tick follows this exact pipeline:

```
[Binance Balance Sync]
       ↓
[Market Discovery (Top 200 by Volume)]
  + Force-include all OPEN positions
       ↓
[For each pair → 6 Strategy Engines in parallel]
       ↓
[Signal Generated? → 11-Layer Risk Validation Chain]
       ↓
[Approved? → Capital Allocation (Confidence-Tiered)]
       ↓
[Live Entry → Exchange → TradeManager.record_entry()]
       ↓
[Per-Position Monitoring: SL / TP / Trailing checks]
       ↓
[Exit Trigger → Exchange Close → TradeManager.record_exit()]
       ↓
[SQLite DB updated ONLY after Binance confirms ZERO position]
```

---

## 💰 Capital Allocation

| Parameter | .env Variable | Default |
|---|---|---|
| Virtual Capital (Paper) | `FUTURES_VIRTUAL_CAPITAL` | 100 USDT |
| Per-Trade Size | `FUTURES_POSITION_SIZE_PERCENT` | 10% |
| Opportunity Reserve | Hardcoded | 20% held back |
| Reserve Unlock Confidence | Hardcoded | ≥ 90% confidence |

### Confidence-Based Position Tiers
| Confidence | Position Size |
|---|---|
| ≥ 90% | 15% of capital |
| ≥ 80% | 12% of capital |
| ≥ 70% | 10% of capital |
| < 70% | 7% of capital |

> **Capital Balance**: The bot syncs the Binance **`total`** USDT balance on every startup (not just `free`), preventing double-counting margin that shrank the effective capital pool.

---

## 🔭 Market Discovery

The bot calls `get_top_trading_pairs` every cycle to build its monitoring list:
1. Fetches 24h volume tickers from Binance Futures.
2. Excludes stablecoins and perp tokens (USDC, USDT, BUSD, etc.).
3. Sorts by volume, takes the top N pairs (`FUTURES_AUTO_TOP_N`, default 200).
4. **Critically**: Any symbol with an `OPEN` or `PENDING_EXIT` trade in the DB is **force-appended** to the list regardless of volume rank, preventing stop-loss bypass.

---

## 📐 Strategy Engine (A1–A6)

Signals from all strategies flow through the Triple-Check Symbol Guard before entering the 11-layer Risk Chain:

| Strategy | Name | Key Indicators | ADX Req | Confidence Range |
|---|---|---|---|---|
| **A1** | EMA + MACD Crossover | EMA 9/21, MACD, EMA 200 Guard | ≥ 30 | 0.60 – 0.85 |
| **A2** | RSI Divergence | EMA + RSI | ≥ 25 | Variable |
| **A3** | Momentum Scalp | EMA 5/13, BB Squeeze, Volume Spike | ≥ 30 | 0.65 – 0.95 |
| **A4** | Trend Following | Triple EMA 9/21/50/200 | ≥ 30 | Fixed 0.90 |
| **A5** | Market Microstructure | Order Flow, Bid-Ask Imbalance | ≥ 25 | Variable |
| **A6** | Orderbook WSS | Real-Time L2 Orderbook (ccxt.pro) | ≥ 30 | 0.70 – 1.00 |

### Strategy Notes
- **A1**: Requires confirmed EMA crossover AND MACD histogram agreement AND price must be on correct side of 200 EMA. Tight ADX ≥ 30.
- **A3**: "2-of-3" confirmation model (EMA cross, BB Squeeze breakout, Volume Spike). Very selective.
- **A4**: The most selective strategy. Requires 210+ candles for warm-up. All 5 of the last 5 candles must align across 3 EMAs. Fixed 0.90 confidence triggers the Opportunity Reserve.
- **A6**: Runs a dedicated background WebSocket thread (ccxt.pro). Monitors live orderbook imbalance across all tracked pairs. Triggers when bid/ask imbalance > ±40%.
- **A1, A2, A4, A5, A6**: All actively running in parallel, scaling up to 100 concurrent positions to gather maximum statistical volume.

---

## 🛡️ 11-Layer Risk Validation Chain

Layers are applied sequentially. Any `None` return immediately cancels the trade.

| # | Layer | File | Status | What It Does |
|---|---|---|---|---|
| 1 | Position Sizing | `position_sizing.py` | ✅ Active | Calculates size. Hard-rejects if size > 50% of capital. |
| 2 | Leverage Control | `leverage_control.py` | ✅ Active | ATR-volatility ceiling × Confidence scale. Reduces cap during drawdown. |
| 3 | Stop-Loss Management | `stop_loss_management.py` | ✅ Active | Sets SL as `min(FUTURES_STOP_LOSS_PERCENT, MAX_EQUITY_RISK/Leverage)`. |
| 4 | Daily Loss Limit | `daily_loss_limit.py` | ✅ Active | Halts all new trades if daily P&L < -(`MAX_DAILY_LOSS_PERCENT`%). |
| 5 | Maximum Drawdown | `maximum_drawdown.py` | ✅ Active | Dual-tier: blocks normals at 50% drawdown, full halt at 70%. |
| 6 | Correlation Risk | `correlation_risk.py` | ✅ Active | Blocks any new trade if open positions ≥ `FUTURES_MAX_OPEN_POSITIONS`. |
| 7 | Volatility Adjustment | `volatility_adjustment.py` | ⚠️ Stub | Pass-through. Not implemented. |
| 8 | Liquidity Check | `liquidity_check.py` | ⚠️ Stub | Pass-through. Not implemented. |
| 9 | Rate Limit | `rate_limit.py` | ⚠️ Stub | Pass-through. Not implemented. |
| 10 | Portfolio Circuit Breaker | `portfolio_circuit_breaker.py` | ✅ Active | Halts all new trades for 30 min if aggregate unrealized P&L drops below -10%. |
| 11 | Capital Preservation | `capital_preservation.py` | ✅ Active | Hard blocks any trade if balance < 10% of `INITIAL_CAPITAL`. |

---

## 🔄 Position Management

### Entry
1. **Triple-Check Symbol Guard**: Checks in-memory `positions` dict, SQLite, then Binance live positions. Blocks if any are OPEN.
2. **Isolated Margin Mode**: `set_margin_mode(ISOLATED)` is called before every entry on Binance.
3. **Leverage Sync**: `set_leverage(N)` is called on Binance to match calculated leverage.
4. **Hard SL on Exchange**: Bot immediately places a `STOP_MARKET reduceOnly` order on Binance after the market entry for hardware-level protection.
5. **DB Record**: `TradeManager.record_entry()` saves only after the order response is confirmed.

### Exit
1. **Price-Level Check**: Every tick checks `current_price >= TP` or `current_price <= SL`.
2. **Trailing TP**: Activates at `TRAILING_TP_ACTIVATION` profit %, then ratchets using a trough/peak trailing mechanism.
3. **Market Close**: `CCXTExchangeClient.close_position()` places a market order.
4. **Zero-Position Verification**: DB is only marked `CLOSED` after Binance confirms zero contracts.
5. **Orphan Sweeper**: On startup, any trade open in DB but missing on Binance is auto-reconciled.

### Trailing Take Profit (Ratchet System)
- **Activation**: Position profit reaches `TRAILING_TP_ACTIVATION` (default: 3%).
- **Tracking**: `peak_price` (for BUY) or `trough_price` (for SELL) is tracked and persisted to SQLite metadata.
- **Trigger**: Closes when price retreats `TRAILING_TP_DISTANCE` (default: 1.5%) from peak.
- **Restart Safety**: `peak_price`, `trough_price`, and `trailing_tp_active` are hydrated from SQLite metadata on every bot restart.

---

## ⚙️ Key Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `EXCHANGE_ENVIRONMENT` | `testnet` or `mainnet` | `testnet` |
| `FUTURES_MAX_OPEN_POSITIONS` | Position count cap | `30` |
| `FUTURES_POSITION_SIZE_PERCENT` | Per-trade margin % | `10` |
| `FUTURES_STOP_LOSS_PERCENT` | Max price drop % for SL | `2.0` |
| `MAX_EQUITY_RISK_PERCENT` | Max equity loss % per trade | `20` |
| `FUTURES_MAX_LEVERAGE` | Dynamic leverage ceiling | `10` |
| `FUTURES_MAX_DRAWDOWN_PERCENT` | Hard halt drawdown | `70` |
| `MAX_DAILY_LOSS_PERCENT` | Daily halt loss | `8` |
| `FUTURES_VIRTUAL_CAPITAL` | Paper trading balance | `100` |
| `FUTURES_AUTO_TOP_N` | Max pairs to monitor | `200` |
| `FUTURES_MARGIN_MODE` | ISOLATED or CROSS | `ISOLATED` |
| `TRAILING_TP_ACTIVATION` | TP ratchet activation % | `3.0` |
| `TRAILING_TP_DISTANCE` | TP ratchet trailing distance % | `1.5` |
| `LOSS_CB_ENABLED` | Enable portfolio circuit breaker | `true` |
| `LOSS_CB_PCT` | Portfolio unrealized loss % trigger | `10.0` |
| `LOSS_CB_COOLDOWN_MINUTES` | Halt duration on CB trigger | `30` |

---

## 🗄️ Database (SQLite)

**Location**: `data/apex_hunter.db`

**Key Tables**:
- `trades`: All open and closed positions with `exchange_order_id`, `entry_price`, `exit_price`, `reason`, `pnl_amount`, `pnl_percent`, `leverage`, `metadata` (JSON blob for trailing TP state).
- `rejections`: All risk layer rejections logged for forensic auditing.
- `settings`: Key-value store for persistent state (`peak_balance`, `paper_total_capital`).

---

## 🔍 Audit Tools

| Script | Purpose |
|---|---|
| `python3 review_positions.py` | Real-time DB vs Binance verification (Order ID, Size in USD, SL/TP, ROE%) |
| `python3 audit_closed_trades.py` | Deep historical P&L accuracy vs Binance fill data |
| `python3 sync_positions.py` | Active reconciliation: auto-fixes desync |
| `python3 emergency_liquidate.py` | 🚨 Kill switch: Market-closes all positions immediately |

---

## 🚀 Deployment

### Run in Background (Systemd)
```bash
sudo systemctl start apex-bot.service
sudo systemctl status apex-bot.service
sudo journalctl -u apex-bot -f   # Live logs
```

### Switch to Mainnet
1. Update `.env`: `EXCHANGE_ENVIRONMENT=mainnet`
2. Replace Testnet API keys with real Binance keys.
3. Restart: `sudo systemctl restart apex-bot.service`

---

## 📱 Telegram Notifications

Configure three bots in `.env` (`TELEGRAM_FUTURES_BOT_TOKEN`, `TELEGRAM_FUTURES_CHAT_ID`).

Notifications sent:
- 📊 Hourly report (activity summary)
- 🎯 Trade Entry (Symbol, Side, Price, Leverage, SL/TP)
- ✅/❌ Trade Exit (P&L, Reason, Duration)
- 🚀/💰 Trailing updates (Activation and Ratchet events)

---

**APEX HUNTER V14 — Institutional-Grade Futures Trading Engine** 🤖
