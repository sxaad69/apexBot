# Apex Bot — Fix Inventory & Data-Backed Decisions

> Consolidated from the 2026-08-19 investigation session.
> This file records every identified issue, the data we gathered to evaluate it,
> and the decisions taken (including what we explicitly ruled OUT and why).

---

## 🧭 Current Live State (end of session)

- **All 7 positions closed** (ZEC, M, BEL, RE, PEOPLE, GUA, BR) — 0 open.
- **0 open algo orders** (15 cancelled + 1 stale HEI SL cleared).
- **Wallet: $159.55 USDT**, 0 unrealized.
- **Bot stopped** (`apex-bot` inactive). Left stopped pending fix decisions.
- **Reminder:** `MAX_DAILY_LOSS_PERCENT` overwrite bug still UNFIXED on mainnet (see D1).

---

## ✅ Data-Backed Decisions (RESOLVED this session)

### DECISION 1 — Stop-loss is NOT the problem (ruled OUT)
**We ran a full 1m-OHLCV tiered-stop simulation across all 119 losers** (batched, cached in `/tmp/tier_sim_v2`), testing flat raw stops at 10/20/30/40/50% and computing MFE for every trade.

Findings:
- **74% of losers went at least +1% in our favor**; avg MFE +6.27%. So signals DO catch moves.
- **Only 24% of losers that survive a 10% raw stop reach +5%** — the other 76% never make real profit even if held. Wider stops rescue only ~20 genuine "good signal killed by tight stop" trades (龙虾 +55%, CROSS +51%, AIO +40%, AVAAI +30%...).
- **The majority of losers are bad signals, not stop problems.** Widening the stop recovers nothing for ~64 of them.

**Conclusion: Do NOT change the stop-loss. It is not the source of losses. The real issue is signal quality / entry selection.** *(Leverage inversion was also ruled out — see Decision 2.)*

### DECISION 2 — Leverage inversion (high ATR → high lev) is OUT OF SCOPE
Simulated inverting `leverage_control.py` (high ATR → higher leverage) on all winners:
- **~62-65% of winners would be EATEN** by the tight `10%/lev` stop at 6-10x. Only the ~35 clean runners (ALPINE, ACU, CROSS...) survive high leverage.
- The top-5 winners (clean momentum) misled us — most winners are choppy and would be stopped out.

**Conclusion: Do NOT invert ATR→leverage. It would destroy ~2/3 of winning trades.**

### DECISION 3 — No decoupling of stop-loss from ROE
Widening/decoupling the stop (ATR-based stop + higher ROE budget) would just make each losing trade cost MORE. Zero evidence it converts to more wins. **Period.**

### DECISION 4 — Early-momentum EXIT filter does NOT help (ruled OUT)
Simulated "exit if negative/weak at 5/15/30min" across all 215 trades. Even though losers fade immediately (a real pattern), cutting on it also cuts 23-67 recovering winners:
- Exit-if-negative@5m: net only **+$3.31**.
- Exit-if-negative@15m: net **−$20.26**; all other thresholds (below +0.5/+1%) are worse (−$35 to −$63).

**Conclusion: the early-dip pattern is a symptom, not a tradeable exit signal. Do NOT add a time-based early-exit.**

### DECISION 5 — Loser pattern = low-volatility / weak-trend entries (signal-quality issue)
Compared winners vs losers on entry features + post-entry price action (cached 1m OHLCV + `strategy_evaluations`):
- **Post-entry fade:** winners push +1.14% by 5m / +2.41% by 60m; losers flat-to-negative (−0.08% / −0.45%). ~47-53% of losers negative by 15-60m vs ~22-29% of winners.
- **ATR% (volatility) is the clearest discriminator:** ATR% <1% → **28% win**; ATR% 3-5% → **70% win**. Winners avg ATR% 2.38 vs losers 1.60.
- **ADX:** mid-range 35-45 → **27% win** (danger zone); 45-55 → **58% win**. Winners avg ADX 45.9 vs losers 41.3.
- **NOT differentiators:** imbalance (~0.73 both) and confidence (~0.88 both; high-lev losers had HIGHER confidence). The A6 signal inputs that trigger entries (imbalance + confidence) do not actually predict winners.

**Conclusion: the real problem is SIGNAL QUALITY — the strategy enters low-volatility, weak-trend setups. Proposed fix: volatility floor (min ATR%) + trend-strength floor (ADX) at entry. NOTE: entry-feature evidence based on ~71 matched trades (28W/43L); needs validation on full set before committing to thresholds.**

---

## 📋 Open Fix Items (priority order)

### A. Trailing/Exit correctness (the ZEC disaster) — BLOCKER, fix before going live
- **A1. `-2021 "Order would immediately trigger"` on tier re-place** — `core/exits.py:200` passes `activate_price=current_price`; Binance rejects when the market already reached it. Killed tier upgrades on ZEC, HEI, RE, M. Fix: re-arm at a price safely ahead of the mark, or reuse the previous trailing's activation.
- **A2. Phantom close — `get_positions()` false "ZERO"** — `exits.py:580` + `trade_manager.py:32-52` trust a TTL-cached/rate-limited/empty fallback as "position gone," recording a close with NO sell (ZEC +71% phantom, `4/USDT` entry ghost, M/RE orphan). Fix: require a real reduce-only fill or direct non-cache fetch before confirming a close.
- **A3. Software-managed positions lose protection after failed tier upgrade** — when tier re-place fails, code sets `exchange_trailing=False` + `trailing_order_id=None`, stranding the position; combined with A2 → phantom. Fix: robust sentinel ownership; never drop protection silently.

### B. Order lifecycle (algo accumulation / blocking)
- **B1. Exits don't cancel Algo orders** — `close_position`/`record_exit` don't hit `/fapi/v1/openAlgoOrders` cancel path → orphaned SL/trailing/TP accumulate on closed symbols (~14 seen, 16 total pre-liquidation). Fix: cancel a position's algos on exit.
- **B2. No periodic orphan-algo sweep** — `sync_positions.py run_sync` exists but never runs. Fix: wire a timer to reconcile open algos vs live positions.
- **B3. Tier/ratchet re-place can stack duplicate orders** when cancel fails (`-2011` stale IDs) → risks per-symbol algo cap blocking new callback orders. Fix: enumerate + dedupe a symbol's open algos before placing.

### C. Trailing design (PEOPLE-style give-back)
- **C1. Tier thresholds & callback measured on RAW %, not leveraged** — a 7x position at +43% leveraged (PEOPLE) never hits +8% raw tier gate → stays on tight 3% callback. **DECISION NEEDED:** key tiers on effective (leveraged) profit? At what values?
- **C2. Dead zone: no trailing between entry and +5% activation** — positions rising +0.5% to +5% then pulling back fall to hard SL (~9% ROE loss) with no protection. **DECISION NEEDED:** lower activation / break-even ratchet / scale by leverage?

### D. Prior outstanding (from AGENTS.md, still open)
- **D1. `MAX_DAILY_LOSS_PERCENT` overwrite bug** — `config/config.py:144` sets 5, `:235` overwrites to 15 → daily loss cap $20.52 not $6.84. **HIGHEST-PRIORITY safety bug.** Fix: remove the overwrite.
- **D2. `ENABLE_CIRCUIT_BREAKER` disabled** — consecutive-loss halt off; realized daily bleed falls through.
- **D3. DB bloat — `strategy_evaluations` duplicate** — 955MB, 1.92M rows, pure duplicate of sweep JSONs. Remove write blocks (`main.py:305/331`), drop table, decide `daily_regime_summary` (`main.py:984/989`).
- **D4. `_update_symbol_loss_streak` in-memory only** — resets on restart; optionally seed from DB.
- **D5. A7 strategy bleeding** — testnet only; decide rework vs park.

### E. Hygiene/optional
- **E1. Tier re-place retry wrapper** — self-heal transient `-2021`/`-2011`.
- **E2. MFE/MAE tracking dead** — `highest_price`/`lowest_price` never updated → `trade_outcomes` all zeros. (Separate from size.)

### F. Signal quality (NEW — the data-backed root cause of losses)
- **F1. Volatility floor at entry** — require minimum ATR% (quiet coins ATR%<1% win only 28%). **PROPOSED** — validate thresholds on full set first.
- **F2. Trend-strength floor (ADX)** — avoid weak-trend ADX 35-45 zone (27% win); prefer ADX 45+ (58% win). **PROPOSED** — validate.
- **F3. Re-examine signal drivers** — imbalance & confidence do NOT predict winners; investigate what entry feature does (or accept fewer, higher-quality entries).

---

## 📊 Key Analysis Artifacts (on `final` server)
- `/tmp/tier_sim_v2/` — per-trade 1m OHLCV caches + flat-10%-stop results.
- `/tmp/tier_sim_all/` — earlier (flawed for multi-trade symbols) tier simulation.
- `tier_sim.py`, `losers_all.py`, `flat10_v2.py`, `win5.py` — analysis scripts (on `final`, repo root).

## 🔑 Key decisions pending
1. **C1** — trailing tier gates: raw % vs effective (leveraged) %? Values?
2. **C2** — dead-zone fix: which option?
3. **F1/F2** — validate ATR% floor + ADX floor on full trade set; set thresholds.
4. **Scope of next deploy** — must-fix cluster (A1,A2,A3,B1,B2,B3) + safety (D1,D2)?
5. Whether to include DB-bloat cleanup (D3) in same deploy.
6. Signal-quality work (F1/F2/F3) — separate effort from the mechanical bug fixes.
