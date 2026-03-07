# APEX HUNTER V14 - Automated Trading System

Apex Hunter V14 is an institutional-grade, multi-strategy automated trading bot designed for futures markets. It features a triple-safety risk management chain, real-time market microstructure analysis, and forensic trade tracking.

## 🏗 System Workflow Architecture

The bot follows a rigid end-to-end execution flow:

1.  **Market Scanning**: Fetches top 30-200 pairs (configurable) filtered by $1M min volume and **Global Stablecoin Exclusion** (USDC, USDT, DAI, etc.).
2.  **Strategy Engine**: Orchestrates 6 concurrent strategies (A1-A6).
3.  **Indicator Filters**: Strictly enforces **ADX Trend Filters** and **ATR Dynamic Stops** (A1-A4).
4.  **Institutional Alpha (A6)**: Implements orderbook imbalance analysis via WSS for high-frequency edges.
5.  **Triple Safety Lock**: Signals must pass **11 Risk Management Layers** before execution.
6.  **Capital Allotment**: Tiered position sizing (7% to 15%) based on **Signal Confidence**.
7.  **Opportunity Reserve**: Holds 20% of capital in reserve for "Elite" (>=90% confidence) signals.
8.  **Exchange Sync**: The `TrailingStopLayer` performs live cancellation and replacement of SL orders on the exchange.
9.  **Forensic Persistence**: Every trade event is recorded in `apex_hunter.db` with accurate reason codes (e.g., `trailing_stop`).

---

## 🧪 Mandatory Verification Loop

> [!CAUTION]
> **DO NOT DEPLOY WITHOUT VERIFICATION**: You must run the following sequence from the project root before pushing to AWS.

### 1. High-Fidelity Logic Check
Simulates a full trading cycle with 5 strategies firing concurrently.
```bash
python3 tests/comprehensive_system_check.py
```

### 2. Trailing Stop Persistence Test
Verifies the ratchet logic and SQLite reason tracking.
```bash
python3 tests/test_trailing.py
```

### 3. Environment Dry-Run
```bash
python3 main.py --mode paper --interval 60
```

---

## 📉 Tactical Strategy Guide (A1-A6)

| Strategy | Focus | Core Indicators | Filter Logic |
|---|---|---|---|
| **A1** | Trend Following | EMA 9/21 + MACD | ADX > 25 |
| **A2** | RSI Momentum | EMA + RSI Divergence | ADX > 25 + Volume |
| **A3** | Volatility Scalp | Fast EMA + Bollinger Squeeze | ADX > 20 + 1.5x Volume |
| **A4** | Triple-EMA Trend | EMA 9/21/50/200 | ADX > 30 (Elite Selection) |
| **A5** | Market Making | Orderbook Depth (Bid/Ask) | Depth > USDT Threshold |
| **A6** | Institutional WSS | Real-time Book Imbalance | Book Skew Ratio > 2.0 |

---

## 🛡️ Zero-Trust Risk Management (11 Layers)

Every trade is validated through the following layers sequentially:

1.  **Position Sizing**: Confidence-based percentage of capital.
2.  **Leverage Control**: Throttled by confidence and account drawdown.
3.  **Stop-Loss Management**: Forces mandatory ATR-based SL.
4.  **Daily Loss Limit**: Automatic halt if daily P&L drops below limit.
5.  **Maximum Drawdown**: Progressively slashes sizing as drawdown increases.
6.  **Correlation Risk**: Blocks over-exposure to correlated assets (e.g., BTC/wBTC).
7.  **Volatility Adjustment**: Widens stops and reduces sizing in high-volatility regimes.
8.  **Liquidity Check**: Ensures sufficient orderbook depth for minimum slippage.
9.  **Rate Limit**: Prevents API over-utilization.
10. **Circuit Breaker**: Halts bot for configured hours after consecutive losses.
11. **Capital Preservation**: Final check to ensure minimum seed capital remains.

---

## 🚀 AWS Production Deployment

### 1. Server Setup
Run the automated setup script on your Ubuntu EC2 instance:
```bash
chmod +x scripts/setup_server.sh
./scripts/setup_server.sh
```
*Creates 2GB Swap file, installs TA-Lib, sets up venv, and creates directories.*

### 2. Manual Test
```bash
source venv/bin/activate
python3 main.py --mode paper
```

### 3. Background Service (Systemd)
To run the bot 24/7 as a system service:
```bash
sudo cp scripts/apex-bot.service /etc/systemd/system/
sudo systemctl enable apex-bot
sudo systemctl start apex-bot
```

### 4. Monitoring
```bash
# View real-time logs
journalctl -u apex-bot -f
sudo systemctl stop apex-bot.service 
sudo systemctl start apex-bot.service   
sudo systemctl restart apex-bot.service
sudo systemctl status apex-bot.service   # check if it's running ok
# Check forensic database entries
sqlite3 data/apex_hunter.db "SELECT * FROM trades LIMIT 10;"
```

---

## 🔍 Forensic Alpha Auditor
The bot includes a dual-mode auditing system to identify "Missed Millions" (Opportunity Cost).

### 1. Unified Signaling Protocol
Every trade event is categorized and logged in `activity_log.db`:
- **STRATEGY_SKIP**: Logged when a strategy sees a move but hesitates due to ADX/ATR filters.
- **RISK_VETO**: Logged when a signal is fired but blocked by one of the 11 risk layers.

### 2. The "Lament" Audit Script
Run this script this weekend to find high-performing coins the bot missed:
```bash
python3 scripts/analyze_missed_alpha.py
```
**Output Report**:
- Identifies the "Winners of the Day" (symbols that went +20%).
- Cross-references logs to reveal which Risk Layer or Strategy Filter was the bottleneck.
- Reports "Theoretical ROI Left on Table."

---

## 🏦 Data Registry (SQLite)

- **Main Database**: `data/apex_hunter.db`
- **Forensic Table**: `trades` (Records `entry_price`, `exit_price`, `reason`, `leverage`, `confidence`).
- **Sync Phase**: On startup, the bot reconciles any "Ghost Trades" that closed while the process was down.

---

## 📱 Telegram Alerts
Configure your bot tokens in `.env` to receive:
- **🎯 Entries**: Price, Leverage, and Confidence.
- **🚨 Ratchets**: Trailing Stop updates.
- **🏁 Exits**: P&L, Reason, and Duration.
- **⚠️ Rejections**: Detailed risk layer blocking reasons.

---

---

## 🏦 Tiered Based Balance Management (Phase 14)

The system now implements a **Dynamic High-Water Mark Signal Gate** to protect both initial capital and realized profits. This creates a tiered "survival zone" that gates access to signals based on performance.

### 🧩 Architecture: The Signal Gate
The bot tracks your **Peak Balance** (highest equity reached). It then applies 3 distinct zones of protection:

| Equity Status | Signal Gating | Strategy Behavior |
| :--- | :--- | :--- |
| **Growth (>50% of Peak)** | **Open** | All strategies A1-A6 can execute normally. |
| **Preservation (30%-50%)** | **Elite Only** | Only signals with $\ge$ 90% confidence can open trades. |
| **Survival (≤ 30% of Peak)** | **Halt** | All trade execution is blocked to protect remaining capital. |

### ⚙️ Environment Configuration
Adjust these variables in your `.env` to tune the sensitivity:
- `TIERED_RISK_ENABLED`: Master activation toggle (`true`/`false`).
- `NORMAL_SIGNAL_THRESHOLD`: Percentage drawdown from peak to lock out normal trades (default: `50.0`).
- `ELITE_SIGNAL_THRESHOLD`: Percentage drawdown from peak for total halt (default: `70.0`).
- `ELITE_CONFIDENCE_LEVEL`: Confidence multiplier needed to bypass the 50% gate (default: `0.90`).


---

## 🛠 Production Stability & Logging (Phase 15)

The bot now features **Triple-Layer Isolation** and **Black Box Logging** to ensure 24/7 uptime even during exchange disconnects or local errors.

### Black Box Logging
*   **Dedicated Error Log**: All critical failures are mirrored to `logs/apex_error.log`. This file is mandatory and ignores the main `LOG_LEVEL` to ensure you never miss a crash.
*   **Log Files vs SQLite**: 
    *   **Text Logs**: Saved to `logs/apex_hunter_YYYYMMDD.log`.
    *   **Forensics**: Position rejections are **muted** in text logs to prevent clutter, but remain 100% available in your SQLite `rejections` table.
*   **Log Levels (.env)**:
    *   `LOG_LEVEL=DEBUG`: Ticker-level detail (High noise, for active debugging).
    *   `LOG_LEVEL=INFO`: Standard trading events (Entries, Exits, Performance). **[RECOMMENDED]**
    *   `LOG_LEVEL=ERROR`: Only critical alerts and crashes.

### Trading Resilience
*   **Triple Isolation**:
    1.  **Booking Isolation**: Booking crashes (SQLite/API) are caught locally and cannot stop the hunting engine.
    2.  **Management Isolation**: Errors in Trailing Stop or Take Profit updates are isolated from the symbol scanner.
    3.  **Global Safety Net**: A top-level catch ensures 100% persistence of any unhandled exception before restart, writing directly to `apex_error.log`.
*   **Live Entry Bridge**: The bot is now fully connected for live execution. Switching `MODE=live` in your `.env` will trigger real exchange orders.

---

**APEX HUNTER V14: Advanced Analytics & Disciplined Risk Management** 🚀
