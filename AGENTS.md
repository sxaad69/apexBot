# Apex Hunter V14 — Session Memory

> Updated from the **Sep 4–5 2026 forensic session** ("why did P&L die after Aug 20").
> Supersedes the Aug 20 session doc. All findings below were verified against
> exchange data (`/fapi/v1/income`, positionRisk) and the bot's own file logs —
> not the bot's DB alone. **The four approved fixes are deployed** (Sep 4
> 21:17/21:31 UTC restarts) — see Fix Plan table for per-fix verification status;
> two items still need a market event to fully verify.

---

## 🧭 Current Snapshot (Sep 4 2026, 21:05 UTC)

- **Deployed:** local `main` == `origin/main` == `final:~/apexBot` == `863aac8` (fix(tier,c1,wss) batch). Service `apex-bot` restarted **Sep 4 21:31:57 UTC** (PID 481091) with the fixes; `PYTHONWARNINGS=ignore::DeprecationWarning` active in the unit (old unit backed up at `/tmp/apex-bot.service.bak-*`).
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

## 🐛 Bugs Found Sep 4 (fixes deployed Sep 4 21:31 UTC — commit `863aac8`)

### 1. Tier trailing clientAlgoId collision — **FIXED, full verification pending a +8% runner**
- **Was:** entry trailing and EVERY tier upgrade shared one static ID (`apex{trade_id}TR`, `core/entry.py:464` + `core/exits.py:304`); keep-alive (`cd3a082`) keeps the old trailing OPEN → Binance **-4116** on every attempt. Measured: **11,078× -4116 + 312× -2021 in ~39h** (龙虾 8,641 / SKR 1,987 / BR 450), exactly 1 success. E1's in-loop retry (3× + 0.5s sleeps) blocked the shared sentinel thread ~2s/tick per storming symbol.
- **Fix:** tier-scoped IDs (`apex{tid[-10:]}TR{tier}`), adopt-before-place (`_find_open_trailing_by_client_id` in `exchange/algo_orders.py` heals lost-response placements), one attempt per tick + per-position cooldown (`TIER_RETRY_COOLDOWN=60s`, config.py).
- **Verified:** code deployed, compiles, config loads; 0× -4116 since restart. **NOT yet verifiable:** an actual successful upgrade + fallback retirement needs a position crossing +8% (none was in band at deploy: BR +4.9% was closest). Watch: `🎯 TRAILING TIER UPGRADE` / `[tier] ... adopted` lines, and that -4116 never returns.

### 2. D4 loss-streak seed was double-broken dead code + streak=1 death-spiral — **FIXED & VERIFIED (seed) / config verified (gate)**
- **Was:** `_seed_symbol_loss_streak` (`main.py:704`) read `self.db` on ApexHunterBot (exists only on `PaperTradingEngine`, `main.py:108`) → silent no-op since Aug 20; also wrote the bot's dict while the entry gate reads the engine's dict (`main.py:51`). `FUTURES_MAX_LOSS_STREAK` defaulted to **1** → with a working seed, 69 symbols would be permanently blacklisted (no decay).
- **Fix:** seed now uses `self.engine.db`, writes `engine.symbol_loss_streak`, and windows exits to `SYMBOL_BLACKLIST_WINDOW_HOURS=72` so blacklists decay; streak default 1→**2**.
- **Verified:** restart log `[D4] Seeded symbol loss-streak blacklist: 6 blocked symbol(s) in last 72h from 13 closed trade(s): {Q, SKR, RONIN, 1000XEC, US, XAN}` — matches the pre-deploy SQL prediction exactly. Config loads as streak=2 / window=72. **NOT yet verifiable:** the C1 gate blocking at exactly 2 consecutive SL losses needs a real 2-loss symbol.

### 3. Observability gaps — **warning firehose FIXED; two residual gaps**
- **FIXED:** `PYTHONWARNINGS=ignore::DeprecationWarning` in `apex-bot.service` (commit `54e5af6`) — 0 DeprecationWarning lines in journald post-restart (was ~2 per log event).
- Residual: the `rejections` table is still not written (D3 design — risk-layer rejections live only in `sweep_summary` JSONs), and sentinel telemetry `rate_budget=?` is still broken (`main.py:698` reads `available_weight`, which the limiter doesn't expose).
- Note: every restart shows ~12–14 "Rate limiter timeout" warnings during the 3-min cold-start warmup — that's the designed 30%→100% ramp (baseline: 14 in the whole previous 39h run), not a regression.

### Verified NON-issues (don't re-litigate)
- **peak_balance propagation works** (main.py:476 → risk_manager → MaximumDrawdownLayer) — layer correctly sees the $269.91 peak / 43.9% DD after restarts.
- **Zero Binance bans** since Sep 2; 429 defenses holding; the Sep 1–2 outage was internal GIL starvation, not a ban.
- The 429 burst at 00:03 UTC: midnight UTC is the bot's most rate-stressed minute.

---

## 🔬 A9 Verdict (paper data, 953 resolved signals Aug 28–Sep 4)

**No edge — keep parked.** 48.1% win rate, symmetric exits (median SL = TP = 2.55%), **−0.18%/trade expectancy BEFORE fees**. Sliced by confidence / ADX / volume-ratio / move-size: no bucket clears fees (best: conf<0.75 → +0.13%; move<1% → +0.05%; worst: conf 0.80–0.85 → −0.74%, i.e. the confidence score is anti-predictive there). Median hold ~7h. Microcap spread+slippage would make live worse. Keep `STRATEGY_A9_PAPER=true` — it's a free ongoing research dataset. Same lesson as A7.

---

## ✅ Fix Plan — EXECUTED Sep 4 (commits `54e5af6`, `863aac8`, `79c57a6`, `9c7dfe5`); per-item verification

| # | Item | Commit | Verified | Pending |
|---|---|---|---|---|
| 1 | Logging: PYTHONWARNINGS in unit | `54e5af6` | ✅ 0 DeprecationWarnings | — |
| 2 | Tier client-ID fix + adopt-on-duplicate + 60s cooldown | `863aac8` | ✅ 0× -4116 since | 🕐 real tier upgrade event |
| 3 | C1 bundle: streak=2 + D4 seed repair + 72h window | `863aac8` | ✅ seed matches prediction | 🕐 block at exactly 2 losses |
| 4 | WSS cap env-overridable → 250 | `863aac8` | ✅ config=250 | ⚠️ stream count not logged |
| 5 | **Paper exit engine** (production exits on 1m bars) | `79c57a6` | ✅ metadata migrated, 59/90 signals state-tracked on first pass | 🕐 resolutions accumulating |
| 6 | **A9 gates**: skip 0.80–0.85 band, >8% extension filter, reversal brake (last-20/12h/35%) | `9c7dfe5` | ✅ config loads; gates live | 🕐 skip lines in journal |
| 7 | **Activation cap** 4% price (`TRAILING_ACTIVATION_PRICE_CAP`) | `9c7dfe5` | ✅ act@1x 4.0% (was 15%), act@2x 4.0%, act@4x 3.75% | 🕐 next A6 entry's journal line |
| — | A9 | paper | replay-validated: paper edge 3x overstated; live tier = no-op without cap; 0.80–0.85 band toxic | — |
| — | 50% DD gate — USER DECISION (open) | — | at equity ≤$134.95 conf<0.90 blocked | — |

---

## 📋 Key Config Values (mainnet, verified Sep 4)

| Var | Value | Note |
|---|---|---|
| `TRADING_MODE` / `EXCHANGE_ENVIRONMENT` | live / production | |
| `EXCHANGE_SIDE_SL` / `ENABLE_EXCHANGE_STOPS` | true / true | Algo API (CONDITIONAL) |
| `FUTURES_MAX_OPEN_POSITIONS` | 15 | |
| `A6_MAX_WATCH_SYMBOLS` | **250** (env-overridable) | was hardcoded 150; 400 pegged a core pre-2f51930 |
| `STRATEGY_A9_ENABLED` / `STRATEGY_A9_PAPER` | true / **true** | A9 = shadow mode on mainnet |
| `FUTURES_MAX_LOSS_STREAK` | **2** (default, env-overridable) | was 1; safe now that D4 seed works + window decays |
| `SYMBOL_BLACKLIST_WINDOW_HOURS` | 72 | D4 seed window (new, Sep 5) |
| `TIER_RETRY_COOLDOWN` | 60 | new, Sep 5 — one tier attempt per tick, then back off |
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

**New from Sep 4–5 sessions:**
- **Paper mode now mimics production exits** — `core/paper_exit_engine.py` resolves paper signals on 1m bars with the live state machine (hard SL first, TP, tier trailing with production formulas, fees+slippage). Legacy mark-compare is only a fallback. Paper rows carry `metadata` (ALTER'd column) with armed/peak/tier/exit_reason — compare paper and live with the same queries.
- **A9's confidence caps at exactly 0.85** (0.50 + 0.20 vol + 0.15 momentum) — it can never reach the 0.90 HOT tier, and its "0.85+" bucket = maxed-formula signals. The 0.80–0.85 band is its formula's high end and was toxic on real-fill replay (−0.38%/trade) → now skipped (A9_SKIP_CONF_BAND).
- **A9 has NO whale factor** (that's A6). A6's whale layer is provably dead on mainnet: $150k single prints in a 100-trade/5-min window essentially don't exist (99.95% zeros across 518k evaluations; manual scans: top prints $242–$40k). Whale is a confidence BONUS and a count≥2 conflict guard — never a gate — so zero-whale entries are by design.
- **The brake window must age out by time**: paused entries produce no outcomes, so a pure last-N window would freeze the brake on forever. `recent_paper_winrate` only counts outcomes resolved in the last 12h. When A9 goes live, the brake must switch from paper_signals to realized trades.
- **Replay-before-deploy**: `tmp/a9_tier_validation.py` + `tmp/a9_validation_results.json` + cached klines replay any exit/entry change on 640 real signals in minutes. Extension data: `tmp/a9_extension_data.json`. The ≥12% extension bucket is positive (real grinders) — don't "fix" the >8% gate into a band gate on one month of data.
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
