# Refactor Verification Plan — Refactored Testnet

**Branch:** `refactor/main-cleanup` (`3c4e681`)
**Goal:** Prove the 32 moved methods behave identically to the monolith.
**Method:** Watch the live testnet for real events + targeted probes. Each check = a code path that must fire.

---

## A. Market Data (core/market_data.py — 3 methods)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| A1 | `get_top_pairs_by_volume` — discovery works | log: `Found 100 top pairs` + `Top 10:` | ✅ seen 17:55 |
| A2 | `fetch_market_data` — 15m OHLCV fetch+cache | sweeps run, no -1003 rate-limit bans | |
| A3 | `fetch_market_data` 5m (A7 path) | A7 signals appear (vol_ratio/bar_move in rejections) | |
| A4 | `_get_candle_ttl_seconds` — cache TTL | no excessive REST calls; sweep duration stable (~40s) | |
| A5 | Blue-chip exclusion filter | no BTC/ETH/BNB entries | |
| A6 | Position-injection (open symbols added) | `Position-Aware Sync: Added N active trade symbols` | |

## B. Exchange Algo Orders (exchange/algo_orders.py — 6 methods)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| B1 | `_place_exchange_conditional` — SL placed | log: `HARD STOP PLACED` on next entry | |
| B2 | `_place_exchange_conditional` — TP placed | log: `HARD TP PLACED` | |
| B3 | `_place_exchange_conditional` — trailing placed | log: `NATIVE TRAILING STOP PLACED` | |
| B4 | `_get_cached_open_algo_ids` — exit detection | log: `EXCHANGE-SIDE EXIT DETECTED` on close | |
| B5 | `_cancel_exchange_conditional` — orphan cleanup | `Cleared lingering` / cancelled after exit | |
| B6 | `_market_supports_trailing_stop` — capability check | trailing placed on supported symbols, skipped on others | |
| B7 | `_algo_symbol` — symbol normalization | orders placed with correct raw symbols (no -4120 errors) | |

## C. Exits & Trailing (core/exits.py — 10 methods)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| C1 | `check_exits` — SL software check | SL closes work (SHELL −9.66% seen) | ✅ |
| C2 | `check_exits` — TP software check | a TAKE_PROFIT close fires | |
| C3 | `check_position_exit` — exit decision | PENDING_EXIT retries work | |
| C4 | `update_trailing_stops` — bot-side ratchet | `TRAILING ACTIVATED` / `RATCHET` logs | |
| C5 | `update_trailing_tier` — tier upgrade | `TRAILING TIER UPGRADE` log (runner ≥8%) | |
| C6 | `update_trailing_take_profit` — TP ratchet | `EXCHANGE TP RATCHET` log | |
| C7 | `_persist_tp_update` / watermarks | DB metadata persists | |
| C8 | `_update_symbol_loss_streak` — C1 blacklist | `RE-ENTRY BLACKLIST` after 2 SLs | |
| C9 | `calculate_dynamic_leverage` | `Volatility Sync: ... Assigned Leverage` on entries | |
| C10 | trailing-stop exit | `trailing_stop` reason on winners (AKE seen) | ✅ |

## D. Entry (core/entry.py — 1 method, the big one)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| D1 | `execute_paper_trade` — full entry pipeline | entries from ALL strategies | ✅ A6/A4/A7/A3 |
| D2 | Risk chain (11 layers) applies | `TRADE REJECTED by risk` when blocked | |
| D3 | Live order placement | `LIVE ENTRY SUCCESS` + exchange position | |
| D4 | Exchange-SL bridge (IDs persisted) | `HARD STOP/TP/TRAILING PLACED` + DB order ids | |
| D5 | Position tracking | positions dict + DB `OPEN` rows | ✅ |
| D6 | Capital deduction | `BALANCE DEDUCTED` + balance moves | |

## E. Reporting (core/reporting.py — 11 methods)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| E1 | `_check_and_send_hourly_report` | hourly report fires (Telegram) | |
| E2 | `_aggregate_hourly_report_data` | report has data | |
| E3 | `_send_*_hourly_report` (futures/spot/arb) | no crash (this was the datetime crash — now fixed) | |
| E4 | `print_summary` | `print_summary()` at shutdown / restart | |

## F. Sync (core/sync.py — 1 method)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| F1 | `_sync_open_trades` — startup reconciliation | `Reconciling N DB trades` on restart | |
| F2 | Garbage collection (missing-on-exchange) | `Garbage Collection: X missing from Exchange` | |
| F3 | Hydration | `HYDRATED position:` on restart | |

## G. Orchestration (main.py retained methods)

| # | Endpoint / behavior | How to verify | Status |
|---|---|---|---|
| G1 | `run_cycle` — sweep loop | `SWEEP COMPLETE` every ~40s | ✅ |
| G2 | `_run_priority_exit_thread` — sentinel | `Sentinel Monitoring` every ~10s | ✅ |
| G3 | `run` — main loop | service stays up (no crash-loop) | ✅ |

---

## Verification checklist protocol
1. **Watch real events** — grep the journal for each log marker as trades happen
2. **Probe** — restart the bot to verify F (sync/hydration) + E4 (print_summary)
3. **Force-check** — wait for a runner ≥8% to verify C5 (tier upgrade)
4. **Document** — mark each ✅ in this file as it's confirmed

## ⚠️ Known testnet-environment constraint (NOT a refactor bug)
- Fresh testnet account hits Binance **`-4045 "Reach max stop order limit"`** when ~18 positions
  each request 3 algo orders (SL+trailing+TP = 54) — the account's concurrent algo cap.
- Effect: most `TRAILING_STOP_MARKET` (and some SL/TP) placements fail → bot correctly
  falls back to **sentinel trailing** (the safety path). Hard SL/TP place on a subset.
- Verified `_market_supports_trailing_stop` returns True correctly (capability check OK).
- **This is an account limit, not a refactor regression.** To get full algo-SL coverage on
  testnet, reduce concurrent positions OR accept sentinel fallback for testing.

## Confirmed so far (2026-08-14)
- A1 discovery ✅  | A6 position-injection ✅ | C1 SL exit ✅ (SHELL −9.66%) | C10 trailing exit ✅ (AKE +7.73%)
- D1 all strategies entering ✅ | G1 sweeps ✅ | G2 sentinel ✅ | G3 service stable ✅
- B6 capability check ✅ | B7 no -4120 errors ✅ | Fallback path (sentinel) ✅
- C9 Volatility-Sync leverage ✅ (18×) | D3 LIVE ENTRY SUCCESS ✅ (18×)
- F1/F3 sync + hydration ✅ (`Reconciling 19 DB trades` + `HYDRATED position` ×19 on restart)
- Rename `execute_paper_trade` → `execute_entry` ✅ (deployed + verified via live entries)


## Acceptance criteria
- [ ] All A-F endpoints fire with the expected log markers
- [ ] At least 1 trade closed via each exit path (SL / TP / trailing)
- [ ] At least 1 A7 signal fires (5m path exercised)
- [ ] A restart completes clean (sync + hydration + no errors)
- [ ] 0 errors in journal across a 24h window
