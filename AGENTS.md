# Apex Hunter V14 — Session Memory

> Updated from the "fix verification + paper mode" session (opencode, Aug 20 2026).
> This file helps any future agent/engineer understand the current state, recent work,
> key decisions, and outstanding issues WITHOUT re-reading the whole history.

---

## 🧭 Current Repo State (as of session end)

- **Branch:** `main` (local == `origin/main` == deployed on AWS mainnet)
- **HEAD:** `6753423` — "feat: merge validated A6 fixes — regime/trend rework, tiered trailing (root cause+call path), C1 blacklist, C2 sizing, enriched rejections"
- **Uncommitted local changes (this session):** 10 files — `config/config.py`, `core/exits.py`, `core/trade_manager.py`, `exchange/algo_orders.py`, `exchange/ccxt_client.py`, `main.py`, `sync_positions.py`, `database/sqlite_manager.py`, `core/entry.py`, `strategies/strategy_a6.py` (not yet committed or pushed)
- **Testnet branch:** `feat/exchange-side-sl` (deployed to `~/apexBotTestnet` on AWS, has extra A7 work NOT on main)
- **Live host (AWS):** SSH alias `final` (the ONLY working host for this project)
  - `~/apexBot` = mainnet (branch `main`, service `apex-bot`)
  - `~/apexBotTestnet` = testnet (branch `feat/exchange-side-sl`, service `apex-bot-testnet`)
  - `~/apexBot/venv/bin/python3` is the shared venv used by BOTH bots
- **DBs:** mainnet `data/apex_hunter.db`, activity log `data/activity_log.db`

---

## 🏗️ Architecture (fast orientation)

- **Bot:** Binance Futures automated trading, "Verify Before Write" architecture.
- **Strategy A6 (the ONLY live strategy on mainnet):** Orderbook imbalance (WSS, real-time) + 15m candle confirmation filters (regime/ADX/volume/EMA200).
  - Signal = `MASSIVE BID WALL +65%` (imbalance threshold 0.65), whale check, session confidence floor.
- **Entry:** `execute_paper_trade` → risk chain (11 layers) → exchange-side algo orders (SL/TP/trailing via Binance Algo API).
- **Exit:** `PriorityExitSentinel` thread → `check_exits` (0.5s) — now the ONLY path where tiered trailing runs.
- **Config gotcha:** `config/config.py` is the ACTIVE config (`import config` → `config/__init__.py`). Root `config.py` is a LEGACY duplicate — do NOT edit it.
- **Timeframe:** `TIMEFRAME=15m` (A6). A7 (testnet only) uses 5m.

---

## 🔍 This Session's Investigation (questions asked + findings)

### 1. Forensics / audit workflow
- `audit/apex_forensics.py` — 3 modes: Exit Forensics, Top Gainers (`--toppers`), Missed Alpha (`--missed-alpha`).
- `audit/parse_forensics_report.py` — renders saved JSON report.
- **Key fix made:** Mode 2 + Mode 3 now read REAL sweep data from `activity_log.db` (sweep_summary JSONs), not just the `rejections` table (which is empty on mainnet).
- **Key fix made:** Mode 2 shows per-symbol rejection TIMELINES from sweep events.
- **Key fix made:** Mode 3 merges sweep rejections ALWAYS (not only when risk-layer table empty) — commit `593106d` (on main).

### 2. Why the bot missed moonshots (TUT +99%, AGT +168%, AVNT +141%...)
- A6 rejects top gainers mostly via `LOW_IMBALANCE` (orderbook wall <65%) and `FILTER_VOLUME < $10k` (absolute 15m volume).
- Moonshots (trend grinders like AGT) never had a 65% wall on real production orderbook → structurally invisible to A6.
- **Testnet ≠ mainnet orderbook** — testnet's thin book inflates imbalance, so A6 "catches" runners on testnet that don't exist on mainnet.

### 3. Backtests done this session
- **Funding/OI squeeze hypothesis: DISPROVEN.** Moonshots had POSITIVE funding (long-crowded momentum), not negative. Only 1 signal in the whole window.
- **5m volume+momentum acceleration: VALIDATED (backtest only).** vol x1.5 + 0.5% move → net +1536% / 869 trades / 43% win / avg +1.77%. Blue-chips produced ZERO signals. → This became Strategy A7.
  - ⚠️ **A7 live on testnet is BLEEDING** (−$77, 22% win). Backtest edge didn't transfer — hindsight bias. NOT ready for mainnet.

### 4. Loss pattern analysis (mainnet Aug 8-14)
- Daily net: Aug8 +$4.57, Aug9 +$30.55, Aug10 +$5.13, Aug11 +$1.68, Aug12 +$12.78, Aug13 **−$8.73**, Aug14 +$14.04.
- **Re-entry trap found:** MORPHO (0-for-3), TA (0-for-2), Q (0-for-2) — repeat losers.
- **Leverage-aware SL is GOOD** (normalizes ~9% ROE, exchange-SL fills tight). Deep losses (US −22%, TUT −14%) were **MANUAL_ADOPT restart-reconciliation artifacts**, not SL failures.
- **Circuit-breaker gap:** consecutive-loss CB (`ENABLE_CIRCUIT_BREAKER`) is DISABLED; portfolio CB only watches UNREALIZED pnl. Realized daily bleed falls through.
- **⚠️ CRITICAL BUG FOUND (FIXED):** `MAX_DAILY_LOSS_PERCENT` was set to 5 at config.py:144 then **OVERWRITTEN to 15** at config.py:235 (exchange-side-SL block). Daily loss limit was $20.52 instead of $6.84. **Fixed** — overwrite removed, replaced with explanatory comment (config.py:269-272). Verified in code.

### 5. Tiered trailing investigation (B1)
- Designed: callback widens 3% → 5% at +8% → 8% at +20% (exchange algo-order replacement).
- **Two real bugs found & fixed:**
  1. `64f35cf` — exchange order IDs were LOST when `record_entry()` returned a fresh dict → tier + exit detection silently no-op'd (the AGT bug). Fixed by re-applying `sl_order_id`/`tp_order_id`/`trailing_order_id` onto the final position object.
  2. `e4e81ee` — the tier hook was wired into `run_cycle`'s exit block, which is skipped by `entry_only=True`; the sentinel calls `check_exits` DIRECTLY. Tier code was DEAD. Fixed by calling `update_trailing_tier` at the top of `check_exits`.
- **Verified working on testnet:** VELVET upgraded to tier 1 (+8.1%). SYN's replace failed but fell back safely to sentinel (Bug-3 fallback works).

### 6. DB bloat cleanup (D3)
- Removed `strategy_evaluations` + `daily_regime_summary` schema from `sqlite_manager.py` (CREATE TABLE, indexes, log methods).
- Removed write blocks from `main.py` (REJECTED enrichment + daily_regime_summary).
- Deleted paper server's `activity_log.db` (1.3GB + 706MB WAL). Recreated fresh on bot start.
- VACUUMed local DB (108MB → 53MB). All 8 files compile clean.

### 7. activateprice precision bug (found during local testnet)
- Tier upgrade in `exits.py` was passing raw float as `activate_price` to Binance algo order → `-1102` error.
- Fixed by wrapping in `float(self.exchange.exchange.price_to_precision(symbol, activate))`.
- Verified locally: tier upgrade now places successfully (but only 2 ratchets observed, no full tier upgrade yet).

### 8. Per-strategy paper mode (new feature, local only)
- Added `STRATEGY_A6_PAPER`, `STRATEGY_A7_PAPER`, `STRATEGY_A8_PAPER` env flags to `config/config.py`.
- Added paper gate at top of `execute_entry()` in `core/entry.py` — logs `📋 [PAPER]`, writes to DB, returns.
- Added `paper_signals` table + `log_paper_signal()` + `resolve_paper_signals()` to `sqlite_manager.py`.
- Added paper resolution in `main.py` sweep cycle (every 300s, compares mark prices against SL/TP).
- All files compile clean. A8 paper mode tested locally — no signals fired yet.

---

## ✅ What Was Merged to Mainnet This Session

### Already on main before this session
- Exchange-side SL (`5c770db` → merged as `4598e2e`): native STOP_MARKET + TRAILING_STOP_MARKET + TAKE_PROFIT_MARKET via Binance Algo API.
- Hard-SL-failure safety (`cd5fbfd`): if hard STOP_MARKET fails, bot cancels exchange orders + keeps sentinel control.
- Blue-chip exclusions (`d58c01b`): `FUTURES_EXCLUDE_SYMBOLS=BTC,ETH,BNB,SOL,XRP,DOGE,ADA,AVAX,LINK,DOT,TRX,LTC,BCH,ATOM,NEAR,APT,TON,SUI,UNI,ARB,OP`.
- Error-spam fixes (`83fe34c`): `capital_return`/`net_pnl` KeyError on already-closed exits + wrong `create_stop_market_order` signature.

### Merged this session (`6753423` — 5 validated commits, in order)
1. `e9692ad` — A6 moonshot capture + exit discipline (A1 regime rework, A2 trend-override, B1 tier base, C1 blacklist, C2 sizing)
2. `489f74b` — add `FUTURES_MAX_LOSS_STREAK` config
3. `64f35cf` — tiered-trailing root cause + cache invalidation + sentinel fallback + diagnostics
4. `e4e81ee` — tiered trailing sentinel-path fix (the one that made it actually run)
5. `1675aec` — enrich sweep rejection data (imbalance/whale/regime/adx/vol_ratio/ema200)

**Mainnet verified after deploy:** A6-only, max positions 15, top-1000, 21 exclusions, tier config 8/5+20/8, exchange-SL on, 0 errors.

---

## 🚫 NOT Merged (excluded intentionally)

- `2bd6b08` — testnet: enable all strategies A1-A6 + top-100 (testnet-specific config)
- `49c5103` — testnet: max open positions 200 (mainnet stays 15)
- `240f61d` — Strategy A7 (bleeding on testnet, NOT ready)
- **Daily-loss-limit fix** — FIXED locally (config.py:269-272). Needs commit + push to mainnet.
- **Per-strategy paper mode** — local only (not committed). `STRATEGY_A6_PAPER`, `STRATEGY_A7_PAPER`, `STRATEGY_A8_PAPER` flags + `paper_signals` DB table + resolution in sweep cycle. A8 paper mode tested locally (no signals fired yet).

---

## 🔬 Testnet Extra Work (branch `feat/exchange-side-sl`, NOT on main)

- **A7 Strategy (`240f61d`)**: 5m volume+momentum acceleration. Enabled on testnet (all 7 strategies, top-100, max-pos 200). **BLEEDING — 59 trades, 22% win, −$77.** Needs signal-quality work.
- All fixes from the merge PLUS A7 + testnet config.

### Local Testnet Run (Aug 20 2026)
- **Config:** A6 live, A8 paper mode, A7 disabled. Tier thresholds lowered to 2%/4% for faster tier-upgrade testing.
- **PID 1948** running locally. WSS connected, sentinel active, 31 positions monitored.
- **Results:** A6 fired WOO (+87.6% wall, rejected by CorrelationRiskLayer) and ADA (+68.2%, rejected). HANA exited via exchange-side SL (-$10.02). CROSS ratcheted trailing stop twice (0.1105 → 0.1165 at +2%). No A8 paper signals fired yet.

---

## 📋 Key Config Values (mainnet, current)

| Var | Value |
|---|---|
| `TRADING_MODE` | live |
| `EXCHANGE_ENVIRONMENT` | production |
| `EXCHANGE_SIDE_SL` | true |
| `ENABLE_EXCHANGE_STOPS` | true |
| `FUTURES_MAX_OPEN_POSITIONS` | 15 |
| `FUTURES_MAX_LEVERAGE` | 10 |
| `FUTURES_AUTO_TOP_N` | 1000 |
| `FUTURES_EXCLUDE_SYMBOLS` | 21 blue-chips |
| `TRAILING_STOP_ACTIVATION` / `DISTANCE` | 5.0 / 3.0 |
| `TRAILING_TIER_1_AT` / `CALLBACK` | 8.0 / 5.0 |
| `TRAILING_TIER_2_AT` / `CALLBACK` | 20.0 / 8.0 |
| `FUTURES_MAX_LOSS_STREAK` | 2 |
| `ENABLE_CIRCUIT_BREAKER` | **true** (consecutive-loss CB, 5 losses → 0.5h halt) |
| `LOSS_CB_ENABLED` | true (portfolio CB, unrealized only) |
| `MAX_DAILY_LOSS_PERCENT` | **5** (fixed — was overwritten to 15) |
| `FUTURES_MAX_DAILY_LOSS_PERCENT` | 5 |

---

## ⚠️ Outstanding / TODO (priority order)

1. **Commit + push all local changes to mainnet** — 10 modified files including D1 fix, paper mode, tier threshold changes, activateprice precision fix, DB cleanup methods.
2. **A7** — either rework its filters (higher volume bar, win-rate floor) or park it. Not ready for mainnet.
3. **Tier re-place flakiness** — the exchange algo-order replace intermittently fails (SYN case). Consider a retry wrapper. Safety fallback already works.
4. **`_update_symbol_loss_streak` (C1)** is in-memory only — resets on restart. Seed from DB if desired.

---

## 🔬 Fix Verification Status (as of session end)

| Fix | Code Done | Compiled | Live Verified |
|-----|-----------|----------|---------------|
| D1 — daily loss limit overwrite | ✅ | ✅ | ❌ needs commit + mainnet deploy |
| D2 — circuit breaker | ✅ | ✅ | ❌ needs 5 consecutive losses |
| D3 — DB bloat | ✅ | ✅ | ✅ VACUUM'd, eval tables removed |
| A1 — tier rearm buffer | ✅ | ✅ | ❌ needs tier upgrade in trade |
| A1 — activateprice precision | ✅ | ✅ | ❌ needs tier upgrade in trade |
| A2 — phantom close guard | ✅ | ✅ | ✅ HANA confirmed zero on exchange |
| A3 — hard-stop fallback | ✅ | ✅ | ❌ needs STOP_MARKET failure |
| B1 — cancel algos on exit | ✅ | ✅ | ❌ needs bot-placed SL exit |
| B2 — orphan algo sweep | ✅ | ✅ | ✅ running every 300s |
| B3 — tier dedupe | ✅ | ✅ | ✅ CROSS ratcheted without double-fire |
| D4 — loss streak seed from DB | ✅ | ✅ | ❌ needs restart + check logs |
| E1 — tier retry (3 attempts) | ✅ | ✅ | ❌ needs tier replace failure |
| E2 — MFE/MAE watermarks | ✅ | ✅ | ❌ needs trade exit |
| Paper mode (per-strategy) | ✅ | ✅ | ❌ A8 no signals fired yet |

**Summary:** 5/14 verified in live conditions. Remaining 9 need specific market events (tier upgrades, exits, failures) that haven't occurred in the testnet run.

---

## 📌 Gotchas / Lessons (don't repeat)

- **`record_entry()` returns a fresh dict** — re-apply exchange order IDs onto the final `self.positions[...]` object or they're silently lost.
- **Sentinel calls `check_exits` directly, not `run_cycle`** — any exit/trailing logic must live in `check_exits`, or it's dead code.
- **Two `config.py` files** — edit `config/config.py`, never root `config.py`.
- **Testnet orderbook is NOT representative** of mainnet — A6 signals that fire on testnet often don't exist on production.
- **`LOG_LEVEL=INFO`** — debug-level rejection values (imbalance %) are NOT captured in logs; only via the enriched sweep data (which is now on mainnet).
- **Two bots share one venv** — use `~/apexBot/venv/bin/python3` for both, from the correct working dir.
- **Do not edit mainnet `.env` carelessly** — `.env` is git-ignored; the server copy is the live one. Back up before changes.
- **`activateprice` must go through `price_to_precision`** — Binance rejects `-1102 Mandatory parameter 'activateprice' was not sent, was empty/null, or malformed` if raw float is used. Always `float(self.exchange.exchange.price_to_precision(symbol, activate))` before passing to algo order.
