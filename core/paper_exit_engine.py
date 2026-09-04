"""
Paper Exit Engine — production-faithful resolution of paper signals.

The legacy resolver (sqlite_manager.resolve_paper_signals) compared mark
prices against SL/TP once per sweep: no intrabar path, no trailing, no tiers,
no fees, no slippage. That overstated A9's September edge ~3x versus an
honest 1-minute replay (see AGENTS.md, Sep 5 validation). This engine runs
the LIVE exit state machine on 1-minute bars instead:

  - hard STOP_MARKET first (pessimistic intrabar: if one bar touches both the
    stop and a profit level, the stop wins)
  - TAKE_PROFIT_MARKET second
  - tier-based TRAILING_STOP_MARKET with the exact production formulas:
      activation = max(LEV_FLOOR, TARGET_ROE / leverage),
      capped by TRAILING_ACTIVATION_PRICE_CAP
      callback   = 1% (lev>=5) / 2% (lev 2-4) / 3% (lev 1)
      tiers      = +8% profit -> callback+1%, +20% -> callback+2%
  - every fill pays taker fees + configurable slippage

Per-signal state (armed/peak/tier/last_bar_ms) persists in
paper_signals.metadata, so resolution is incremental across sweeps: each pass
only walks the bars that closed since the last one. Win/loss + exit reason
land in the same columns the forensics queries already use, with the
simulated path recorded in metadata.
"""

import json
import time
from typing import Dict, List, Optional


def simulate_bar_walk(bars: List, entry_price: float, sl: float, tp: float, side: str,
                      leverage: int, act_pct: float, slip: float = 0.0005,
                      fee_rt: float = 0.10, state: Optional[Dict] = None):
    """Walk 1-minute bars through the production exit state machine.

    bars: ccxt ohlcv rows [ts, open, high, low, close, volume] (ascending).
    state: persisted {"armed","peak","tier","last_bar"} from previous sweeps.
    Returns (result_dict_or_None, new_state). result has exit/entry-relative
    net pnl_pct (fees included, slippage inside the fills) and exit_reason.
    """
    if side and side.lower() == 'sell':
        # mirror the world for shorts
        def px(v):
            return entry_price * 2 - v
        sl, tp = px(sl), px(tp)
    else:
        def px(v):
            return v

    e = entry_price
    cb_base = 1.0 if leverage >= 5 else (2.0 if leverage >= 2 else 3.0)
    activation = e * (1 + act_pct / 100.0)
    state = state or {"armed": False, "peak": e, "tier": 0, "last_bar": 0}
    armed, peak, tier = bool(state.get("armed")), float(state.get("peak") or e), int(state.get("tier") or 0)

    for k in bars:
        ts, o, hi, lo, c = int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])
        if ts <= state.get("last_bar", 0):
            continue

        if side and side.lower() == 'sell':
            hi_w, lo_w = px(lo), px(hi)   # widened world: high becomes low
        else:
            hi_w, lo_w = hi, lo
        hi_s, lo_s = px(hi), px(lo)       # signed-world prices for peak math

        # 1. hard stop (pessimistic-first)
        if lo <= sl:
            fill = sl * (1 - slip) if not (side and side.lower() == 'sell') else sl * (1 + slip)
            pnl = ((fill / e) - 1) * 100
            res = {"pnl_pct": round(pnl, 4), "exit_price": round(fill, 10), "exit_reason": "stop_loss",
                   "armed": armed, "tier": tier, "peak": round(peak, 10)}
            state["last_bar"] = ts
            return res, state
        # 2. take profit
        if hi >= tp:
            fill = tp * (1 - slip) if not (side and side.lower() == 'sell') else tp * (1 + slip)
            pnl = ((fill / e) - 1) * 100
            res = {"pnl_pct": round(pnl, 4), "exit_price": round(fill, 10), "exit_reason": "take_profit",
                   "armed": armed, "tier": tier, "peak": round(peak, 10)}
            state["last_bar"] = ts
            return res, state
        # 3. trailing: arm at activation, trail the extreme by the tier callback
        if not armed and hi_w >= activation:
            armed = True
            peak = hi_s
        elif armed:
            peak = max(peak, hi_s)
        if armed:
            cb = cb_base + {0: 0.0, 1: 1.0, 2: 2.0}.get(tier, 0.0)
            trigger = peak * (1 - cb / 100.0)
            if lo_w <= trigger:
                fill = trigger * (1 - slip) if not (side and side.lower() == 'sell') else trigger * (1 + slip)
                pnl = ((fill / e) - 1) * 100
                res = {"pnl_pct": round(pnl, 4), "exit_price": round(fill, 10), "exit_reason": "trailing_stop",
                       "armed": armed, "tier": tier, "peak": round(peak, 10)}
                state["last_bar"] = ts
                return res, state
        # 4. tier upgrades on bar close (same +8/+20 thresholds as live)
        prof = ((px(c) / e) - 1) * 100
        if tier == 0 and prof >= 8:
            tier = 1
        elif tier == 1 and prof >= 20:
            tier = 2
        state["last_bar"] = ts

    state.update({"armed": armed, "peak": peak, "tier": tier})
    return None, state


class PaperExitEngine:
    """Incrementally resolves open paper signals through simulate_bar_walk."""

    def __init__(self, exchange_client, db, logger, config):
        self.exchange_client = exchange_client
        self.db = db
        self.logger = logger
        self.config = config

    def _leverage_for(self, confidence: float) -> int:
        # mirror of calculate_dynamic_leverage's tier rule (paper has no
        # reserve/drawdown context; the 2x/3x split is the part that matters
        # for activation distance)
        hot = float(getattr(self.config, 'TIER_HOT_CONF', 0.90))
        return 3 if confidence >= hot else 2

    def _act_pct(self, leverage: int) -> float:
        floor = float(getattr(self.config, 'TRAILING_ACTIVATION_LEV_FLOOR', 2.0))
        roe = float(getattr(self.config, 'TRAILING_ACTIVATION_TARGET_ROE', 15.0))
        cap = float(getattr(self.config, 'TRAILING_ACTIVATION_PRICE_CAP', 4.0) or 0)
        act = max(floor, roe / max(leverage, 1))
        if cap > 0:
            act = min(act, cap)
        return act

    def resolve_incremental(self, max_symbols: int = 24) -> int:
        """Resolve open paper signals against 1m bars. Returns resolved count.

        max_symbols is deliberately low: each get_ohlcv costs weight 5 from the
        SHARED token bucket the live sweep also draws from — the first pass with
        60 symbols tripped ~340 sweep-future timeouts (23:08 burst). 24/pass
        still covers ~90 opens in two sweeps."""
        try:
            open_signals = self.db.get_open_paper_signals()
        except Exception as e:
            self.logger.warning(f"[PAPER-ENGINE] open-signal query failed: {e}")
            return 0
        if not open_signals:
            return 0

        by_sym = {}
        for row in open_signals:
            by_sym.setdefault(row['symbol'], []).append(row)

        resolved = 0
        slip = float(getattr(self.config, 'PAPER_SLIPPAGE_BPS', 5.0)) / 10000.0
        fee_rt = float(getattr(self.config, 'PAPER_FEE_PERCENT', 0.05)) * 2
        checked = 0
        for symbol, rows in by_sym.items():
            if checked >= max_symbols:
                break
            checked += 1
            try:
                bars = self.exchange_client.get_ohlcv(symbol, '1m', limit=6) or []
                if not bars:
                    continue
                for row in rows:
                    try:
                        meta = {}
                        try:
                            meta = json.loads(row['metadata'] or '{}')
                        except Exception:
                            meta = {}
                        lev = self._leverage_for(float(row['confidence'] or 0))
                        res, new_state = simulate_bar_walk(
                            bars, float(row['entry_price']), float(row['stop_loss']),
                            float(row['take_profit']), row['side'], lev,
                            self._act_pct(lev), slip, fee_rt, meta)
                        meta.update(new_state)
                        if res:
                            pnl = res['pnl_pct']
                            if row['side'] and row['side'].lower() == 'sell':
                                pnl = -pnl
                            meta['exit_reason'] = res['exit_reason']
                            meta['sim_leverage'] = lev
                            self.db.update_paper_signal_exit(
                                row['id'], 'win' if pnl > 0 else 'loss',
                                res['exit_price'], pnl, json.dumps(meta))
                            resolved += 1
                            self.logger.info(
                                f"📋 [PAPER-ENGINE] {row['symbol']} resolved via "
                                f"{res['exit_reason']}: {pnl:+.2f}% (tier {res['tier']}, "
                                f"peak {res['peak']:.6g})")
                        else:
                            self.db.update_paper_signal_metadata(row['id'], json.dumps(meta))
                    except Exception as e:
                        self.logger.debug(f"[PAPER-ENGINE] {symbol} row error: {e}")
            except Exception as e:
                self.logger.debug(f"[PAPER-ENGINE] {symbol} fetch error: {e}")
        return resolved
