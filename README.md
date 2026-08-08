# APEX HUNTER V14 — Automated Binance Futures Trading Bot

Apex Hunter V14 is an institutional-grade automated trading bot built for Binance Futures. It operates in a strict, layered, **"Verify Before Write"** architecture: every trade decision is validated across capital, risk, strategy, and exchange confirmation *before* anything is committed to the database. The bot is **live on Binance mainnet** (production), running 24/7 on an AWS EC2 instance via systemd.

---

## 🚀 Deployment Overview (Production)

| Item | Value |
|---|---|
| Host | AWS EC2 (VPS) |
| Repo (server) | `~/apexBot` |
| Venv | `~/apexBot/venv` |
| Service | `apex-bot.service` — `ExecStart=venv/bin/python3 main.py --interval 30` |
| Env | `APEX_CONFIRM_LIVE=YES`, `.env` at `~/apexBot/.env` |
| Mode | `TRADING_MODE=live`, `EXCHANGE_ENVIRONMENT=production` |
| Balance | Live Binance mainnet wallet (~$130 USDT baseline, `FUTURES_VIRTUAL_CAPITAL=136.78`) |

> 🔒 **Note**: actual server address/credentials (SSH host, key paths, API keys) are
> intentionally **not** in this public README. Keep them in a private note or local `.env`.

### ⚠️ CRITICAL: Two `config.py` Files
There are **two** configuration modules and they are NOT interchangeable:

- **`config/config.py`** — the **ACTIVE** one. `import config` resolves to `config/__init__.py`, which imports `Config` from `config/config.py`. All runtime behavior comes from here.
- **`config.py`** (repo root) — **legacy/duplicate**. Changes to it have **NO effect** unless they are also made in `config/config.py`.

> Any LLM or engineer editing settings MUST edit `config/config.py`, not root `config.py`.

### Deploy Workflow (used for every change)
```bash
# 1. Edit locally, commit, push
git add -A && git commit -m "..." && git push
# 2. Pull on the server and restart the service
ssh <server> "cd ~/apexBot && git pull && sudo systemctl restart apex-bot"
# 3. Verify
ssh <server> "sudo journalctl -u apex-bot -f"     # live logs
ssh <server> "cd ~/apexBot && tail -50 logs/apex_hunter_$(date +%Y%m%d).log"
ssh <server> "tail -20 logs/apex_error.log"
```

---

## 🏗️ System Execution Flow

Each tick (default interval 30–60s) follows this pipeline:

```
[Binance Balance Sync]
       ↓
[Market Discovery — top N by 24h volume, force-include all OPEN positions]
       ↓
[Per symbol → Strategy Engine(s) — A1–A6]
       ↓
[Signal generated? → Risk Validation Chain (layered)]
       ↓
[Approved? → Capital Allocation (confidence-tiered)]
       ↓
[Live Entry → Binance → TradeManager.record_entry()  (Verify Before Write)]
       ↓
[Per-Position Monitoring: SL / TP / Trailing via PriorityExitSentinel thread]
       ↓
[Exit Trigger → Market Close → TradeManager.record_exit()]
       ↓
[SQLite DB updated ONLY after Binance confirms ZERO position]
```

The exit monitor is a dedicated **`PriorityExitSentinel` thread** (in `main.py`, `_run_priority_exit_thread`): it checks every position against SL/TP/trailing every ~0.5s over the WebSocket feed, independent of the main loop. Full position sweeps run every ~30–40s as backup.

---

## 📐 Strategy Engine (A1–A6)

| Strategy | Name | Key Indicators | Status (live) |
|---|---|---|---|
| **A1** | EMA + MACD Crossover | EMA 9/21, MACD, EMA 200 guard | `STRATEGY_A1_ENABLED=false` |
| **A2** | RSI Divergence | EMA + RSI | `STRATEGY_A2_ENABLED=false` |
| **A3** | Momentum Scalp | EMA 5/13, BB Squeeze, Volume Spike | `STRATEGY_A3_ENABLED=false` |
| **A4** | Trend Following | Triple EMA 9/21/50/200, 210-candle warm-up | `STRATEGY_A4_ENABLED=false` |
| **A5** | Market Microstructure | Order Flow, Bid-Ask Imbalance | `STRATEGY_A5_ENABLED=false` |
| **A6** | Orderbook WSS | Real-time L2 orderbook (ccxt.pro), imbalance trigger | **`STRATEGY_A6_ENABLED=true`** |

> Only **A6** is currently enabled in live production (`A6_ALLOW_SHORT=false`). A1–A5 are off.

Strategy sources live in `strategies/` (`strategy_a1.py` … `strategy_a6.py`), with `base_strategy.py` and `filters.py` as shared infrastructure.

---

## 🛡️ Risk Validation Chain

Layers are applied sequentially; a `None`/reject from any layer cancels the trade. Files live in `risk/layers/`.

| # | Layer | File | Status |
|---|---|---|---|
| 1 | Position Sizing | `position_sizing.py` | ✅ Active |
| 2 | Leverage Control | `leverage_control.py` | ✅ Active |
| 3 | Stop-Loss Management | `stop_loss_management.py` | ✅ Active |
| 4 | Daily Loss Limit | `daily_loss_limit.py` | ✅ Active |
| 5 | Maximum Drawdown | `maximum_drawdown.py` | ✅ Active |
| 6 | Correlation Risk | `correlation_risk.py` | ✅ Active (caps open positions) |
| 7 | Volatility Adjustment | `volatility_adjustment.py` | ⚠️ Stub |
| 8 | Liquidity Check | `liquidity_check.py` | ⚠️ Stub |
| 9 | Rate Limit | `rate_limit.py` | ⚠️ Stub |
| 10 | Portfolio Circuit Breaker | `portfolio_circuit_breaker.py` | ✅ Active |
| 11 | Capital Preservation | `capital_preservation.py` | ✅ Active |
| — | Trailing Stop | `trailing_stop.py` | ✅ Active |
| — | Portfolio Profit Ratchet | `portfolio_profit_ratchet.py` | ⛔ **DISABLED** (`PROFIT_RATCHET_ENABLED=false`) |

**Portfolio Profit Ratchet (GlobalRatchet)** was disabled on 2026-08-06 because it was producing *missed alpha* (it gated trades that later won, ~+235% of the forensics missed-alpha). The flag lives in `config/config.py` (`PROFIT_RATCHET_ENABLED`, `PROFIT_RATCHET_ACTIVATION/TRAILING/FLOOR/COOLDOWN/SLIPPAGE_BUFFER`).

---

## 💰 Capital Allocation

| Parameter | Config | Current value |
|---|---|---|
| Virtual/live capital baseline | `FUTURES_VIRTUAL_CAPITAL` | `136.78` USDT |
| Per-trade size | `FUTURES_POSITION_SIZE_PERCENT` | `4.0` % |
| Max leverage | `FUTURES_MAX_LEVERAGE` | `10` |
| Max open positions | `FUTURES_MAX_OPEN_POSITIONS` | `15` |
| Max equity risk per trade | `MAX_EQUITY_RISK_PERCENT` | `5.0` % |
| Global ROE stop-loss shield | `GLOBAL_STOP_LOSS_ROE` | `10.0` % |
| Take-profit | `TAKE_PROFIT_PERCENT` / `FUTURES_TAKE_PROFIT_PERCENT` | `10.0` % |
| Max daily loss | `MAX_DAILY_LOSS_PERCENT` | `15.0` % |
| Max drawdown halt | `FUTURES_MAX_DRAWDOWN_PERCENT` | `70.0` % |
| Margin mode | `FUTURES_MARGIN_MODE` | `ISOLATED` |

The bot syncs Binance **`total`** USDT balance (not just `free`) on startup, preventing double-counting of margin.

---

## 🔭 Market Discovery

`get_top_trading_pairs` builds the monitoring universe every cycle:
1. Fetches 24h volume tickers from Binance Futures.
2. Excludes stablecoins (USDC, USDT, BUSD, …).
3. Sorts by volume → top `FUTURES_AUTO_TOP_N` (currently `1000` = full universe of qualifying perps).
4. **Force-appends** any symbol with an `OPEN`/`PENDING_EXIT` trade in the DB regardless of rank, so SL/TP exits are never missed.

---

## 🔄 Position Management

### Entry
1. **Triple-Check Symbol Guard**: in-memory positions dict, SQLite, then Binance live positions — blocks duplicates.
2. **Isolated margin**: `set_margin_mode(ISOLATED)` before every entry.
3. **Leverage sync**: `set_leverage(N)` matches computed leverage.
4. **DB record**: `TradeManager.record_entry()` saves only after the exchange confirms the fill.

### Exit (all bot-side; no exchange stop orders)
> `ENABLE_EXCHANGE_STOPS=false` — SL/TP/trailing are monitored **by the bot** (`PriorityExitSentinel`), not placed as `STOP_MARKET` orders on the exchange. That is by design; `sl_order_id`/`tp_order_id` are expected to be `None`.

1. **Price-level check** every ~0.5s: `current >= TP` or `current <= SL`.
2. **Trailing stop**: activates at `TRAILING_STOP_ACTIVATION` (5%) profit, then trails at `TRAILING_STOP_DISTANCE` (3%) from peak/trough.
3. **Market close**: `CCXTExchangeClient.close_position()` places a reduce-only market order.
4. **Zero-position verification**: DB marked `CLOSED` only after Binance confirms zero contracts.
5. **Orphan sweeper**: startup reconciliation for trades open in DB but missing on exchange.

---

## ⚙️ Key Configuration (config/config.py + .env)

| Variable | Purpose | Current |
|---|---|---|
| `TRADING_MODE` | `paper` / `simulation` / `live` | `live` |
| `EXCHANGE_ENVIRONMENT` | `testnet` / `production` | `production` |
| `BINANCE_API_KEY` / `BINANCE_API_SECRET` | Live mainnet keys (in `.env`) | set |
| `TELEGRAM_FUTURES_BOT_TOKEN` / `TELEGRAM_FUTURES_CHAT_ID` | Futures notifications | set |
| `TIMEFRAME` | Candle timeframe | `15m` |
| `FUTURES_AUTO_TOP_N` | Universe size | `1000` |
| `OHLCV_LIMIT` | Candles fetched per symbol | `210` |
| `OHLCV_BATCH_MAX` | Stale-candle refreshes per sweep | `100` |
| `A6_MAX_WATCH_SYMBOLS` | Cap on A6 orderbook WSS subs | `400` |
| `DISCOVERY_MAX_WORKERS` | Discovery concurrency | `3` |
| `RATE_LIMIT_MAX_WEIGHT_PER_MIN` | Binance weight budget | `1500` (cap 2400) |
| `POSITIONS_CACHE_TTL` | REST positions cache | `5.0s` |
| `TICKER_CACHE_TTL` | REST ticker cache | `5.0s` |
| `BALANCE_CACHE_TTL` | Balance cache | `60.0s` |
| `LOSS_CB_ENABLED` | Portfolio circuit breaker | `true` |
| `LOSS_CB_PCT` | Unrealized-loss trigger | `10.0%` |
| `LOSS_CB_COOLDOWN_MINUTES` | Halt duration | `30` |
| `PROFIT_RATCHET_ENABLED` | Portfolio profit ratchet | `false` |
| `ENABLE_EXCHANGE_STOPS` | Exchange-side SL/TP orders | `false` |

---

## 🗄️ Database (SQLite)

**Location**: `data/apex_hunter.db` (hardcoded in `database/sqlite_manager.py` — note `SQLITE_DB_NAME` in config is NOT the path used).

**Tables**:
- `trades` — open/closed positions: `exchange_order_id`, `entry_price`, `exit_price`, `reason`, `pnl_amount`, `pnl_percent`, `leverage`, `metadata` (trailing state), `entry_time`.
- `rejections` — risk-layer rejection log (input to Missed Alpha forensics).
- `settings` — key-value state (`peak_balance`, `paper_total_capital`).
- `active_positions`, `portfolio_ratchets`, `circuit_breaker_events`, `metrics` — supporting state.

Also: `data/activity_log.db`, `data/ohlcv_cache/` (forensics OHLCV cache), `data/telegram_history.json`.

---

## 🔍 Audit & Forensics (the `audit/` folder)

Two tools; **no production impact** (read-only against DB/exchange).

### 1. `audit/apex_forensics.py` — the *producer*
Three analysis modes in one tool, writing JSON reports to `data/reports/forensics_report_YYYY-MM-DD.json`:

- **MODE 1 — Exit Forensics** (default): per closed trade — TP/SL validation, ride quality, dollar capture, forward-looking 4h post-exit analysis, trailing effectiveness.
- **MODE 2 — Top Gainers Retrospective** (`--toppers`): the market's real top gainers/losers during the window, cross-referenced with traded symbols & watchlist.
- **MODE 3 — Missed Alpha** (`--missed-alpha`): rejected signals — would they have profited in the next 4h? Reports missed alpha per rejection layer.
- **`--settle`**: runs all 3 modes for the previous UTC day, writes the permanent JSON, purges the OHLCV cache.

Usage:
```bash
python3 audit/apex_forensics.py --from 2026-08-06 --to 2026-08-07   # mode 1
python3 audit/apex_forensics.py --all --from 2026-08-07              # all 3 modes
python3 audit/apex_forensics.py --settle                            # daily settle
python3 audit/apex_forensics.py --fetch-only --days 1               # pre-fetch OHLCV
python3 audit/apex_forensics.py --cache-status                      # cache coverage
python3 audit/apex_forensics.py --purge-cache                       # clear cache
```

OHLCV is cached per symbol+interval in `data/ohlcv_cache` (watermark-based delta fetches; Binance rate budgeted via a token bucket).

### 2. `audit/parse_forensics_report.py` — the *renderer*
Reads a `forensics_report_YYYY-MM-DD.json` and prints a human-readable overview (executive summary + all 3 mode sections). No DB access.

```bash
python3 audit/parse_forensics_report.py --date 2026-08-07   # find by date
python3 audit/parse_forensics_report.py --report <path>     # explicit file
python3 audit/parse_forensics_report.py -o /tmp/overview.txt
```

### Scheduled on AWS
- `apex-forensics.service` — `ExecStart=venv/bin/python3 audit/apex_forensics.py --settle`
- `apex-forensics.timer` — runs **daily at 23:55 UTC** (`OnCalendar=*-*-* 23:55:00`, 5-min jitter).

---

## 🔧 Other Operational Scripts

| Script | Purpose |
|---|---|
| `review_positions.py` | Real-time DB vs Binance verification (order ID, size, SL/TP, ROE%) |
| `sync_positions.py` | Active reconciliation — auto-fixes desync |
| `emergency_liquidate.py` | 🚨 Kill switch — market-closes all positions immediately |
| `scripts/cleanup_logs.py` | Log rotation/cleanup |
| `check_exit.py`, `check_pos.py`, `verify_tpsl.py` | Quick diagnostics |
| `top_gainers_retro.py` | Standalone gainers retrospective (superseded by forensics Mode 2) |
| `test_connection.py` | Connectivity/credentials test |

---

## 📱 Telegram Notifications

Configured via `.env` (`TELEGRAM_FUTURES_BOT_TOKEN`, `TELEGRAM_FUTURES_CHAT_ID`). Notifications:
- 📊 Hourly report (activity summary)
- 🎯 Trade Entry (symbol, side, price, leverage, SL/TP)
- ✅/❌ Trade Exit (P&L, reason, duration)
- 🚀/💰 Trailing activation & ratchet events

---

## 🚀 Operations Cheat-Sheet

```bash
# Status & logs (replace <server> with your SSH host)
ssh <server> "sudo systemctl status apex-bot"
ssh <server> "sudo journalctl -u apex-bot -f"
ssh <server> "cd ~/apexBot && tail -50 logs/apex_hunter_$(date +%Y%m%d).log"
ssh <server> "tail -20 ~/apexBot/logs/apex_error.log"

# Restart / stop
ssh <server> "sudo systemctl restart apex-bot"
ssh <server> "sudo systemctl stop apex-bot"

# Live positions & balance (read-only)
ssh <server> "cd ~/apexBot && ./venv/bin/python3 -c \"import config.config as c; ex=__import__('exchange.ccxt_client',fromlist=['CCXTExchangeClient']).CCXTExchangeClient(c.Config(), __import__('bot_logging.mongo_logger',fromlist=['MongoLogger']).MongoLogger(c.Config())); [print(p) for p in ex.exchange.fetch_positions() if abs(float(p.get('contracts',0)))>0]\" 2>&1 | grep -v INFO"

# DB queries
ssh <server> "sqlite3 ~/apexBot/data/apex_hunter.db \"SELECT trade_id,symbol,entry_price,status,entry_time FROM trades WHERE status='OPEN'\""
```

---

## 🧱 Repository Layout

```
apexBot/
├── main.py                  # entry point (--interval, --mode); exit sentinel thread
├── config/                  # ⚠️ ACTIVE config (config/config.py) + __init__.py
├── config.py                # ⚠️ LEGACY duplicate — do not edit
├── core/                    # trade_manager, position_manager, trading_engine, spot engine
├── exchange/                # ccxt_client (primary), base_client, api_manager, rate_limiter, wss_manager
├── strategies/              # strategy_a1..a6, base_strategy, filters
├── risk/layers/             # 11 risk layers + trailing_stop + portfolio_profit_ratchet
├── database/                # sqlite_manager (data/apex_hunter.db), mongo_manager, json_manager
├── bot_logging/             # logger, mongo_logger
├── notifications/           # Telegram
├── audit/                   # apex_forensics.py (producer), parse_forensics_report.py (renderer)
├── scripts/                 # ops/analysis helpers
├── tests/                   # unit tests (pytest)
├── data/                    # SQLite DBs, ohlcv_cache, reports
├── logs/                    # rotating logs (apex_hunter_YYYYMMDD.log, apex_error.log)
├── apex-bot.service         # systemd unit (bot)
├── apex-forensics.service   # systemd unit (daily settle)
├── apex-forensics.timer     # systemd timer (23:55 UTC)
└── .env / .env.example      # credentials & env config
```

---

## 🛠️ Development

- **Python 3.11+** (tested on 3.14.4). Dependencies in `requirements.txt` (ccxt, pandas, numpy, requests, websockets, aiohttp, pymongo/motor optional, streamlit dashboard, pytest).
- Run tests: `python -m pytest tests/`
- Local sandbox: `./venv/bin/python3 main.py --interval 30` (testnet/paper `.env`).
- Forensics/audit scripts are safe to run locally against a synced DB copy.

---

**APEX HUNTER V14 — Institutional-Grade Futures Trading Engine** 🤖
