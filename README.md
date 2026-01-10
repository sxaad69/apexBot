# APEX HUNTER V14 🚀

**Multi-Exchange Cryptocurrency Trading Bot with 4 Competing Strategies**

Automated trading system supporting futures, spot, and arbitrage across 10+ exchanges with comprehensive risk management and Telegram notifications.

---

## ⚡ QUICK START (5 Minutes to First Backtest)

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Setup configuration
cp .env.example .env
nano .env  # Add your API keys

# 3. Test connections
python test_connection.py

# 4. Run backtest (NO API KEYS NEEDED)
python backtest.py

# 5. View results and start trading!
```

---

## 📋 TABLE OF CONTENTS

1. [Features](#features)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Testing Connections](#testing-connections)
5. [Backtesting](#backtesting)
6. [Running Simulations](#running-simulations)
7. [Going Live](#going-live)
8. [Dashboard](#dashboard)
9. [Telegram Bots](#telegram-bots)
10. [Troubleshooting](#troubleshooting)

---

## 🎯 FEATURES

### Trading Modules
- ✅ **Futures Trading** - Leveraged trading (up to 10x) on KuCoin
- ✅ **Spot Trading Logger** - Strategy validation without leverage on Binance
- ✅ **Arbitrage Scanner** - Cross-exchange opportunities on 10+ exchanges

### Strategies (All Implemented & Ready)
- ✅ **Strategy A1:** EMA Only (Simple, Fast)
- ✅ **Strategy A2:** EMA + RSI + Volume (Selective, Higher Win Rate)
- ✅ **Strategy A3:** EMA + ATR (Adaptive Stops)
- ✅ **Strategy A4:** Multi-Timeframe (Trend Following, Highest Win Rate)

### Safety Features
- ✅ **Triple Safety Lock** - All trading disabled by default
- ✅ **11-Layer Risk Management** - Stop loss, daily limits, drawdown protection
- ✅ **Circuit Breaker** - Auto-halt on consecutive losses
- ✅ **Paper Trading Mode** - Test with fake money first

### Communication
- ✅ **3 Telegram Bots** - Separate notifications for Futures/Spot/Arbitrage
- ✅ **Real-time Alerts** - Trade entries, exits, opportunities
- ✅ **Daily Summaries** - Performance reports

---

## 📦 INSTALLATION

### Requirements
- Python 3.11+
- 512MB RAM minimum
- Internet connection

### Install Dependencies

```bash
pip install -r requirements.txt
```

**What gets installed:**
- `ccxt` - Multi-exchange trading library
- `pandas` - Data processing
- `requests` - API communication
- `python-dotenv` - Environment configuration
- `python-telegram-bot` - Telegram notifications

---

## ⚙️ CONFIGURATION

### 1. Create .env File

```bash
cp .env.example .env
```

### 2. Essential Settings

```env
# CRITICAL: Start with testnet!
EXCHANGE_ENVIRONMENT=testnet

# Which exchanges to use
FUTURES_EXCHANGE=kucoin
SPOT_EXCHANGE=binance

# SAFETY: Keep these as 'no' until ready
FUTURES_TRADING_ENABLED=no
SPOT_TRADING_ENABLED=no
ARBITRAGE_TRADING_ENABLED=no
```

### 3. Add API Keys

**For KuCoin Futures:**
1. Go to https://www.kucoin.com/account/api
2. Create API Key with Futures Trading permission
3. Add to .env:
```env
KUCOIN_API_KEY=your_key_here
KUCOIN_API_SECRET=your_secret_here
KUCOIN_API_PASSPHRASE=your_passphrase_here
```

**For Binance Spot:**
1. Go to https://www.binance.com/en/my/settings/api-management
2. Create API Key with Spot Trading permission
3. Add to .env:
```env
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
```

### 4. Setup Telegram Bots (Optional but Recommended)

1. Open Telegram, search for `@BotFather`
2. Send `/newbot` and follow instructions
3. Copy the bot token
4. Start a chat with your bot
5. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
6. Find your `chat_id` in the response
7. Add to .env:

```env
TELEGRAM_FUTURES_ENABLED=true
TELEGRAM_FUTURES_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_FUTURES_CHAT_ID=your_chat_id

TELEGRAM_SPOT_ENABLED=true
TELEGRAM_SPOT_BOT_TOKEN=987654321:XYZwvuTSRqponMLKjihGFEdcba
TELEGRAM_SPOT_CHAT_ID=your_chat_id

TELEGRAM_ARBITRAGE_ENABLED=true
TELEGRAM_ARBITRAGE_BOT_TOKEN=555666777:ABCxyzDEFghiJKLmnoQRStuVWX
TELEGRAM_ARBITRAGE_CHAT_ID=your_chat_id
```

---

## 🧪 TESTING CONNECTIONS

### Test All Exchanges

```bash
python test_connection.py
```

**What it tests:**
- ✅ Futures exchange (KuCoin) - Balance, positions, markets
- ✅ Spot exchange (Binance) - Balance, markets, prices
- ✅ Telegram bots - Sends test messages

### Test Specific Exchange

```bash
# Test Binance only
python test_connection.py --exchange binance

# Test KuCoin futures
python test_connection.py --exchange kucoin --type futures

# Test Telegram bots only
python test_connection.py --telegram
```

### Expected Output

```
✅ Configuration loaded
✅ KUCOIN (futures): Connected (127 markets)
✅ BINANCE (spot): Connected (2,847 markets)
✅ Futures Telegram bot connected: @your_futures_bot
✅ Spot Telegram bot connected: @your_spot_bot
✅ Arbitrage Telegram bot connected: @your_arb_bot
```

---

## 📊 BACKTESTING

**NO API KEYS NEEDED FOR BACKTESTING** - It uses public market data!

### Basic Backtest (All Strategies)

```bash
python backtest.py
```

This runs all 4 strategies on BTC/USDT for the last 30 days.

### Custom Backtests

```bash
# Test specific strategy
python backtest.py --strategy A2

# Test different timeframe
python backtest.py --days 90

# Test specific dates
python backtest.py --start 2024-01-01 --end 2024-06-30

# Test different pair
python backtest.py --symbol ETH/USDT

# Test with different capital
python backtest.py --capital 1000

# Combine options
python backtest.py --strategy A4 --symbol BTC/USDT --days 180 --capital 500
```

### Understanding Results

```
BACKTEST RESULTS: A2: EMA+RSI+Volume
=====================================
Total Trades:     47
Wins:             29 (61.7%)
Losses:           18

Total Return:     +23.45%
Final Capital:    $123.45
Total P&L:        $+23.45

Average Win:      $2.15
Average Loss:     $-1.20
Profit Factor:    1.79
Max Drawdown:     8.32%
```

**What to look for:**
- ✅ Win Rate > 55%
- ✅ Total Return > 10% (per month)
- ✅ Max Drawdown < 15%
- ✅ Profit Factor > 1.5

### Strategy Comparison

When you run all strategies, you'll see:

```
STRATEGY COMPARISON
===================
Strategy              Trades   Win%     Return       Max DD
------------------------------------------------------------
A4: Multi-Timeframe   23       73.9%    +31.20%     6.10%   🏆
A2: EMA+RSI+Volume    47       61.7%    +23.45%     8.32%
A3: EMA+ATR           38       55.3%    +18.90%    10.45%
A1: EMA Only          68       48.5%    +12.30%    14.20%

🏆 Best Strategy: A4: Multi-Timeframe
```

**Strategy Selection Tips:**
- **A1 (EMA Only):** Most trades, good for learning
- **A2 (EMA+RSI+Volume):** Balanced, good win rate
- **A3 (EMA+ATR):** Adapts to volatility
- **A4 (Multi-Timeframe):** Highest win rate, fewer trades

---

## 🎮 RUNNING SIMULATIONS

### Paper Trading (Simulation with Live Data)

```bash
# Coming in next update
python main.py --mode paper
```

**Paper trading uses:**
- ✅ Real live market data
- ✅ Real strategy signals
- ✅ Simulated order execution
- ✅ No real money risk

---

## 💰 GOING LIVE

### ⚠️ CRITICAL: Safety Checklist

Before enabling live trading:

- [ ] Backtested ALL strategies on 6+ months data
- [ ] Paper traded for at least 1-2 weeks
- [ ] Win rate > 55% in paper trading
- [ ] Tested on testnet/sandbox environment
- [ ] Telegram bots working and sending notifications
- [ ] Understand the risks (you can lose money)
- [ ] Start with minimum capital ($50-100 USDT)
- [ ] Set daily loss limits
- [ ] Monitor actively for first week

### Enable Live Trading

**Step 1:** Switch to production environment

```env
EXCHANGE_ENVIRONMENT=production
```

**Step 2:** Enable trading (one at a time!)

```env
# Start with just futures
FUTURES_TRADING_ENABLED=yes

# Keep others disabled initially
SPOT_TRADING_ENABLED=no
ARBITRAGE_TRADING_ENABLED=no
```

**Step 3:** Set conservative limits

```env
FUTURES_VIRTUAL_CAPITAL=50  # Start small!
FUTURES_MAX_LEVERAGE=3      # Don't use full 10x
FUTURES_MAX_DAILY_LOSS_PERCENT=3
FUTURES_MAX_OPEN_POSITIONS=1
```

**Step 4:** Run the bot

```bash
python main.py
```

**Step 5:** MONITOR CLOSELY

- Check Telegram every hour
- Review trades daily
- Adjust if losing
- Scale up slowly if winning

---

## 📺 DASHBOARD

### Starting the Dashboard

```bash
# Install streamlit if not already installed
pip install streamlit

# Start dashboard
streamlit run dashboard/app.py
```

**Access:**
Open browser to http://localhost:8501

### Dashboard Features

**Live Stats:**
- Current P&L (today, week, month, all-time)
- Open positions
- Recent trades
- Strategy performance comparison

**Charts:**
- Equity curve
- Daily P&L
- Win rate over time
- Strategy comparison

**Backtest Viewer:**
- Run backtests from dashboard
- Compare strategies visually
- Export results to CSV

### Dashboard Filters

- **Date Range:** Today, 7D, 30D, Custom
- **Strategy:** All, A1, A2, A3, A4
- **Module:** Futures, Spot, Arbitrage, All

---

## 🤖 TELEGRAM BOTS

### Notification Types

**Futures Bot:**
```
🎯 FUTURES TRADE ENTRY
Symbol: BTC/USDT
Side: BUY
Entry: $96,234.50
Size: $10.00
Leverage: 10x
Stop Loss: $94,309.61
Take Profit: $98,159.39
Strategy: A2: EMA+RSI+Volume
⏰ 2026-01-09 10:30:15
```

**Spot Bot:**
```
📍 SPOT SIGNAL
Signal: BUY BTC/USDT
Price: $96,234.50
Amount: $10.00
Stop Loss: $94,309.61
Take Profit: $98,159.39
Strategy: A2: EMA+RSI+Volume
Trading: DISABLED (Logging Only)
⏰ 2026-01-09 10:30:15
```

**Arbitrage Bot:**
```
🔍 ARBITRAGE OPPORTUNITY
Type: Cross-Exchange
Pair: BTC/USDT
Buy: BINANCE @ $96,200.00
Sell: KUCOIN @ $96,400.00
Spread: 0.21%
Net Profit: $2.10 (2.1% after fees)
⏰ 10:30:15
```

### Managing Notifications

- Mute individual bots if too noisy
- Check daily summaries at midnight
- Critical alerts always come through
- Can disable via .env anytime

---

## 🔧 TROUBLESHOOTING

### "Configuration error: 'Config' object has no attribute..."

**Fix:** Update your .env file with latest .env.example

```bash
# Backup your current .env
cp .env .env.backup

# Copy new template
cp .env.example .env

# Copy your API keys from .env.backup to new .env
nano .env
```

### "Failed to fetch balance"

**Check:**
1. API keys are correct
2. API has correct permissions (Futures/Spot)
3. Using correct environment (testnet vs production)
4. IP not restricted on exchange

### "Telegram bot connection failed"

**Check:**
1. Bot token is correct
2. Chat ID is correct
3. You've started a chat with the bot
4. Bot token format: `123456789:ABCdef...`

### "No trades in backtest"

**Reasons:**
- Not enough historical data
- Strategy parameters too strict
- Market conditions don't match strategy
- Try different symbol or timeframe

### Exchange Connection Issues

```bash
# Test specific exchange
python test_connection.py --exchange binance

# Check if credentials loaded
python -c "from config import Config; c=Config(); print(c.BINANCE_API_KEY[:10])"
```

---

## 📚 ADDITIONAL RESOURCES

### Project Structure

```
apex-hunter-v14/
├── config/           # Configuration
├── strategies/       # 4 trading strategies (A1-A4)
├── exchange/         # CCXT exchange clients
├── core/             # Trading engine, arbitrage, spot logger
├── risk/             # 11-layer risk management
├── notifications/    # Telegram bots
├── database/         # SQLite (coming soon)
├── dashboard/        # Streamlit dashboard (coming soon)
├── test_connection.py # Connection tester
├── backtest.py       # Backtesting script
├── main.py           # Main entry point
└── README.md         # This file
```

### Key Files

- **test_connection.py** - Test exchanges and Telegram
- **backtest.py** - Backtest strategies
- **.env** - Your configuration (don't commit!)
- **requirements.txt** - Python dependencies

### Strategy Parameters

Edit in .env to customize:

```env
# Strategy A2 (EMA + RSI + Volume)
STRATEGY_A2_EMA_FAST=9
STRATEGY_A2_EMA_SLOW=21
STRATEGY_A2_RSI_PERIOD=14
STRATEGY_A2_RSI_LOW=40
STRATEGY_A2_RSI_HIGH=70
STRATEGY_A2_VOLUME_MULTIPLIER=1.2

# Strategy A3 (EMA + ATR)
STRATEGY_A3_ATR_PERIOD=14
STRATEGY_A3_ATR_MULTIPLIER=2.0

# Strategy A4 (Multi-Timeframe)
STRATEGY_A4_TIMEFRAME_HIGH=4h
STRATEGY_A4_TIMEFRAME_LOW=15m
```

---

## ⚖️ RISK DISCLAIMER

**IMPORTANT:** Cryptocurrency trading involves substantial risk of loss.

- You can lose all your invested capital
- Past performance does not guarantee future results
- This bot is for educational purposes
- Use at your own risk
- Start with money you can afford to lose
- Never invest more than you can afford to lose

**The developers are not responsible for any financial losses.**

---

## 🤝 SUPPORT

- **Issues:** Open a GitHub issue
- **Questions:** Check documentation first
- **Updates:** Watch the repository for updates

---

## 📝 LICENSE

MIT License - See LICENSE file for details

---

## 🎯 QUICK REFERENCE

```bash
# Test connections
python test_connection.py

# Test specific exchange
python test_connection.py --exchange binance

# Run backtest (all strategies)
python backtest.py

# Run backtest (specific strategy)
python backtest.py --strategy A2

# Run backtest (custom period)
python backtest.py --days 90

# Start dashboard
streamlit run dashboard/app.py

# Run bot (simulation mode)
python main.py --mode paper

# Run bot (live - BE CAREFUL!)
python main.py --mode live
```

---

**Ready to start? Run `python test_connection.py` to verify everything works!** 🚀
