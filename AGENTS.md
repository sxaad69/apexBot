# Apex Hunter V14 — Session Memory

> Updated from the **Sep 4–5 2026 forensic session** ("why did P&L die after Aug 20").
> Supersedes the Aug 20 session doc. All findings below were verified against
> exchange data (`/fapi/v1/income`, positionRisk) and the bot's own file logs —
> not the bot's DB alone. **No code changes have been applied yet** — the fix
> plan is analysis-complete and awaiting approval.

---

## 🧭 Current Snapshot (Sep 4 2026, 21:05 UTC)

- **Deployed:** local `main` == `final:~/apexBot` == `97dfcbb` (ops prune). Service `apex-bot` up since **Sep 3 06:14 UTC**, NRestarts=0.
- **Equity:** $151.44 (6 open positions, ~$291 notional, all A6 entries, lev 1–4x)
- **Persisted peak:** $269.91 → **drawdown −43.9%**
- **P&L since Aug 1:** +$21.72 lifetime. **ALL profit was pre-Aug-20** (Aug 1–20: +$41.06 over 531 closes). Aug 21→Sep 4: **−$19.34** over 151 closes.
- **Binance bans:** **ZERO** since Sep 2 (no 418, no cooldown engaged). Only 4 soft 429s — one 6-second burst on "fetch top pairs" at 00:03 UTC Sep 4. The rate-limit defenses (cold-start warmup, ban-aware load_markets, shared token bucket) are holding.
- **Host:** AWS SSH alias `final`, 2 vCPU / 2GB RAM, load ~0.15–0.39, **disk 89% full (1.6GB free)**.
  - `~/apexBot` = mainnet (branch `main`, service `apex-bot`)
  - `~/apexBotTestnet` = testnet (branch `feat/exchange-side-sl`, service `apex-bot-testnet`) — NOT re-verified this session
  - `~/apexBot/venv/bin/python3` shared venv (Python 3.14, ccxt 4.5.70)
- **DBs:** mainnet `data/apex_hunter.db` (trades/settings), `data/activity_log.db` (activity_log incl. `sweep_summary` JSONs + `paper_signals`).

---

## 🏗️ Architecture (fast orientation)

- **Bot:** Binance USDT-M Futures, "Verify Before Write". Since 2025-12-09 ALL conditional orders go through the **Algo Order API** (`fapiPrivatePostAlgoOrder`, algoType=CONDITIONAL) — legacy `/fapi/v1/order` rejects them (-4120).
- **Strategy A6 (only live strategy):** orderbook imbalance via WSS (wall ≥0.65) + 15m filters. Entries come from the **WSS path only** — the REST sweep scans ~500 symbols but logged **0 entries across 4,241 sweeps** (Sep 3–4); sweeps are for scanning/rejection data only.
- **A6 WSS watch cap:** `A6_MAX_WATCH_SYMBOLS = 150` (hardcoded `config/config.py:116`, NOT env-overridable). Was 400; cut Sep 2 after the GIL hang (root cause separately fixed in `2f51930`).
- **A9 (paper-only on mainnet):** `STRATEGY_A9_PAPER=true` in `.env` — fires ~150 signals/day, places zero orders.
- **Exit:** `PriorityExitSentinel` thread → `check_exits` (sentinel tick ~1.5s) → exchange-side SL/TP/trailing polling + `update_trailing_tier` (tiered trailing) + software fallback.
- **Risk chain:** 11 layers; `TIERED_RISK_ENABLED=true`, elite conf gate 0.90, tier gates at 50%/70% drawdown, drawdown-adjusted sizing (67%/33%) and leverage.
- **Config gotcha:** edit `config/config.py` (ACTIVE), never root `config.py` (legacy duplicate).

---

## 📉 The Aug-20 P&L Cliff (forensic conclusion)

| Era | Closes/day | Win rate | Net |
|---|---|---|---|
| Aug 1–20 | ~33 | ~48–57% | **+$41.06** (best: Aug 9 +24.7, Aug 18–20 +42.5 combined) |
| Aug 21–27 | ~18 | 20–49% | −11.8 (repeat-loser churn: PIEVERSE 0/9, MOCA 0/8, Q 0/6) |
| Aug 28–30 | ~0 | — | momentum-gate blackout (Aug 28 02:17→Aug 29 04:27 blocked EVERY A6 entry) + zero-position days |
| Aug 31–Sep 2 | ~1 | — | GIL hang, bot down Sep 1 → Sep 3 06:14 UTC |
| Sep 3–4 | ~7 | 33–50% | +0.3 |

**Five stacked causes:** (1) Aug 20–22 deploy cluster (safety merge + sync fixes + `0d442cc` tier-flip signal system) broke entry selectivity → activity collapsed ~75% and win rate cratered; (2) trailing-tier upgrades broken since Aug 29 (see bug #1) → winners give back; (3) A9 shipped as paper-only → the new alpha source never trades; (4) signal pipe narrowed (WSS 400→150 + Aug 27/31 throttles); (5) account 43.9% underwater amplifies drawdown-adjusted throttles. **Note:** "every day green till Aug 20" is survivorship memory — Aug 10–17 had 6 red days (Aug 13 −$17.5).

---

## 🐛 Bugs Found Sep 4 (NOT yet fixed)

### 1. Tier trailing clientAlgoId collision — **P0, storming live right now**
- Entry trailing and EVERY tier upgrade share one static ID: `apex{trade_id[-10:]}TR` (`core/entry.py:464`, `core/exits.py:304`). The keep-alive design (`cd3a082`, Aug 29) keeps the old trailing OPEN when placing the new tier → Binance rejects with **-4116 ClientOrderId duplicated** every time.
- Measured: **11,078× -4116 + 312× -2021 in ~39h** (Sep 3 06:14 → Sep 4 21:04). Exactly **1 success** (and only on the 3rd retry — Binance dup-check is racy). Symbols: 龙虾 8,641 / SKR 1,987 / BR 450. **BR was an open position storming at analysis time.**
- Side damage: E1's retry loop (3 attempts + 2×0.5s sleeps) runs INSIDE the sentinel tick → blocks the shared sentinel thread ~1–1.5s/tick per storming symbol → delays exit detection for ALL positions; floods logs; wastes rate budget.
- Net effect: tier upgrades are dead → winners never widen their callback → the AGT/AVNT give-back problem the tiers were built to fix is back.

### 2. D4 loss-streak seed is double-broken dead code + streak=1 death-spiral risk
- `_seed_symbol_loss_streak` (`main.py:704`, called `main.py:837` on `ApexHunterBot`) does `getattr(self, 'db', None)` — **`self.db` exists only on `PaperTradingEngine`** (`main.py:108`), not on the bot → silently returns, never seeds, never logs.
- Second bug: even if it ran, it writes `self.symbol_loss_streak` on the **bot** while the entry gate (`core/entry.py:109`) reads the **engine's** dict (`main.py:51`).
- `FUTURES_MAX_LOSS_STREAK` defaults to **1** (`config/config.py:283`, not set in final's `.env`) → ONE SL exit blocks a symbol for the session (confirmed live: XAN blocked at streak=1, Sep 4 13:12).
- Simulated against live DB: a working seed at streak=1 would blacklist **69 symbols** with NO time-decay (blocked symbol can't win → can't reset → blocked forever). Currently two bugs cancel each other; fixing the seed alone is HARMFUL. Note `'stop_loss' in 'trailing_stop'` is True → trailing exits count as SL losses in C1.

### 3. Observability gaps
- The `rejections` table stopped being written Aug 22 (D3 removed the writer — by design). Risk-layer rejection visibility now lives ONLY in `sweep_summary` JSONs in `activity_log.db`.
- journald effective coverage ≈ **4 hours** — churned by a `datetime.utcnow()` DeprecationWarning firehose (Python 3.14; `database/sqlite_manager.py:800`, `bot_logging/mongo_logger.py:361`). Bot's OWN file logs (`logs/`, 7-day retention via `cleanoldlogs.sh`) are the real forensic source and are grep-able.
- Sentinel telemetry `rate_budget=?` is broken (`main.py:698` reads `available_weight` which the limiter doesn't expose) — we're blind on remaining weight.

### Verified NON-issues (don't re-litigate)
- **peak_balance propagation works** (main.py:476 → risk_manager → MaximumDrawdownLayer) — layer correctly sees the $269.91 peak / 43.9% DD after restarts.
- **Zero Binance bans** since Sep 2; 429 defenses holding; the Sep 1–2 outage was internal GIL starvation, not a ban.
- The 429 burst at 00:03 UTC: midnight UTC is the bot's most rate-stressed minute.

---

## 🔬 A9 Verdict (paper data, 953 resolved signals Aug 28–Sep 4)

**No edge — keep parked.** 48.1% win rate, symmetric exits (median SL = TP = 2.55%), **−0.18%/trade expectancy BEFORE fees**. Sliced by confidence / ADX / volume-ratio / move-size: no bucket clears fees (best: conf<0.75 → +0.13%; move<1% → +0.05%; worst: conf 0.80–0.85 → −0.74%, i.e. the confidence score is anti-predictive there). Median hold ~7h. Microcap spread+slippage would make live worse. Keep `STRATEGY_A9_PAPER=true` — it's a free ongoing research dataset. Same lesson as A7.

---

## ✅ Fix Plan (analysis-complete, awaiting approval — do in this order)

| # | Item | Type | Risk | Why |
|---|---|---|---|---|
| 1 | **Logging:** `Environment=PYTHONWARNINGS=ignore::DeprecationWarning` in `apex-bot.service` | config | ~zero | Halves stderr/journal churn; disk is 89%; makes everything after verifiable. Do FIRST. |
| 2 | **Tier fix:** tier-scoped client IDs (`apex{tid[-8:]}TR{tier}`, ≤15 chars) + on -4116 adopt the existing open trailing as success + per-position retry cooldown (~60s) instead of blocking per-tick retries | code (`core/exits.py`, error visibility in `exchange/algo_orders.py`) | low | Stops the live storm; restores winner protection. Fallback (old stays armed) unchanged. |
| 3 | **Blacklist bundle:** `FUTURES_MAX_LOSS_STREAK=2` (env, final `.env`) + repair D4 seed (use `self.engine.db`, write `self.engine.symbol_loss_streak`) + 72h time window on seeded exits | env + code | low IF bundled | Stops repeat-loser churn without the 69-symbol death spiral. MUST land together. |
| 4 | **WSS cap:** make `A6_MAX_WATCH_SYMBOLS` env-overridable, step 150→250, observe load/bans 24h | code + env | low | Restores signal surface. Expectation: won't restore Aug volume alone (scoring gates dominate; collapse began at 400 streams). |
| — | **A9** | none | — | Parked (see verdict). |
| — | **50% DD gate — USER DECISION** | config | — | At equity ≤ **$134.95** (=50% DD from $269.91 peak), TIERED_RISK gate blocks every signal with conf <0.90 — A6 confs run 0.81–0.87 → near-total shutdown, one bad day away ($16.5). Intended risk-off, or recalibrate? |

---

## 📋 Key Config Values (mainnet, verified Sep 4)

| Var | Value | Note |
|---|---|---|
| `TRADING_MODE` / `EXCHANGE_ENVIRONMENT` | live / production | |
| `EXCHANGE_SIDE_SL` / `ENABLE_EXCHANGE_STOPS` | true / true | Algo API (CONDITIONAL) |
| `FUTURES_MAX_OPEN_POSITIONS` | 15 | |
| `A6_MAX_WATCH_SYMBOLS` | 150 | **hardcoded** config.py:116, not env |
| `STRATEGY_A9_ENABLED` / `STRATEGY_A9_PAPER` | true / **true** | A9 = shadow mode on mainnet |
| `FUTURES_MAX_LOSS_STREAK` | **1 (default!)** | not in final `.env`; config.py:283. Docs said 2. Death-spiral risk w/ working seed |
| `TIERED_RISK_ENABLED` | true (hardcoded) | |
| `NORMAL_SIGNAL_THRESHOLD` / `ELITE_SIGNAL_THRESHOLD` | 50 / 70 (% DD) | 50% gate blocks conf<0.90 |
| `ELITE_CONFIDENCE_LEVEL` | 0.90 | A6 confs 0.81–0.87 → would be blocked |
| `MAX_DRAWDOWN_PERCENT` / `FUTURES_MAX_DRAWDOWN_PERCENT` | 70 | sizing 67%/33% at 23.1/46.9 DD |
| `INITIAL_CAPITAL` | 136.78 ("Real Wallet Baseline") | layer peak starts here, ratchets to recovered peak |
| `settings.peak_balance` (DB) | 269.91 | recovered at startup ✓ |
| `TRAILING_TIER_1/2_AT` | 8 / 20 | upgrades broken — bug #1 |
| `TIER_REPLACE_RETRIES` | 3 | E1 — harmful as implemented (blocks sentinel) |

---

## 📌 Gotchas / Lessons (don't repeat)

**New from Sep 4 session:**
- **Binance Algo API: `clientAlgoId` must be unique among OPEN orders.** Keep-alive (place-new-before-cancel-old) + a static per-trade ID = -4116 every time. Use tier/attempt-scoped IDs; treat -4116 as "already exists" and adopt.
- **Never block-sleep inside the sentinel tick path.** E1's 2×0.5s sleeps per failing symbol stall exit detection for ALL positions. Use per-position cooldown/backoff state.
- **`self.db` lives on `PaperTradingEngine`, not `ApexHunterBot`** — `getattr(self, 'db')` on the bot silently returns None. D4's dead code shipped because the silent-return has no log. Silent fallbacks need a log line.
- **`'stop_loss' in 'trailing_stop'` is True** — C1's substring check counts trailing exits as SL losses. Intended? Document it.
- **Host is UTC; user's local date is ahead** — "today" differs between the two; check `date -u` first.
- **journald ≈ 4h coverage only; the bot's file logs (`logs/`, 7-day) are the forensic source.** Bot names the log file at process start (current: `apex_hunter_20260903.log` despite being Sep 4) — grep by pattern, not filename.
- **The REST sweep never enters trades** — all entries come from the WSS signal path. Sweep-level rejection tallies (LOW_IMBALANCE etc.) are the sweep's own evaluations, not the WSS path's.
- **log file writes stop being representative if the daily-file name misleads** — mtime, not name, tells freshness.

**Carried forward (still true):**
- `record_entry()` returns a fresh dict — re-apply exchange order IDs onto the final `self.positions[...]` object or they're lost.
- The sentinel calls `check_exits` directly, not `run_cycle` — exit/trailing logic must live in `check_exits` or it's dead code.
- Two `config.py` files — edit `config/config.py`, never root `config.py`.
- Testnet orderbook is NOT representative of mainnet (thin book inflates imbalance — the A7 lesson).
- `activateprice` must go through `price_to_precision` or Binance returns -1102.
- `.env` is git-ignored; the server copy is the live one — back up before changes.
- Two bots share one venv (`~/apexBot/venv/bin/python3`) — run from the correct working dir.
- Binance deprecates v2 endpoints (`/fapi/v1/positionRisk`, `/fapi/v1/account` → 404 as of Sep 4); use v3 (`fapiPrivateV3GetPositionRisk`, `fapiPrivateV3GetAccount`).

---

## 🗂️ Untracked side dirs (in repo root, NOT part of the bot)

- `backtest/` — A7 discriminator/SL-stress scripts. `tmp/` — signal-economics & exit-rule sims. `test agency/` — unrelated ComfyUI/YouTube project. `.freebuff/` — tool state. `graphify-out/` — codebase knowledge graph (Aug 27–29 run: 1687 nodes / 4059 edges; `GRAPH_REPORT.md` + `graph.html`).
