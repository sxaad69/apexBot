"""
Layer 2: Leverage Control
Hybrid dynamic leverage: ATR-based maximum + Confidence scaling
- Step 1: Calculate max safe leverage from ATR (market volatility)
- Step 2: Scale within that max based on signal confidence
- Step 3: Never exceed MAX_LEVERAGE_ABSOLUTE (default 20x)
"""

from typing import Dict, Any, Optional


class LeverageControlLayer:
    """
    Layer 2: Hybrid Dynamic Leverage Control

    Formula:
      ATR_Safe_Max  = EQUITY_RISK_PCT / ATR_PCT        (volatility ceiling)
      Conf_Scale    = lerp(0.3, 1.0, confidence)       (quality scaling)
      Final_Leverage = min(ATR_Safe_Max * Conf_Scale, MAX_ABSOLUTE, drawdown_adjusted_max)
    """

    def __init__(self, config, logger):
        self.config = config
        self.logger = logger

    def _get_drawdown_adjusted_max(self, drawdown_pct: float) -> int:
        """Reduce maximum allowed leverage as drawdown increases."""
        # Phase-1 core fix: base off the WORKING cap (get_leverage_cap) not the
        # absolute emergency ceiling, so 5x/6x stays the default and drawdown
        # only tightens further from there.
        get_cap = getattr(self.config, 'get_leverage_cap', None)
        base_max = get_cap(0.90) if callable(get_cap) else getattr(self.config, 'FUTURES_MAX_LEVERAGE', 10)
        if drawdown_pct < 5:
            return base_max           # Full range available
        elif drawdown_pct < 10:
            return min(base_max, 10)  # Cap at 10x in moderate drawdown
        elif drawdown_pct < 15:
            return min(base_max, 5)   # Cap at 5x in heavy drawdown
        else:
            return 1                  # Preservation mode only

    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Evaluate and set dynamic leverage using ATR + Confidence hybrid model.
        """
        equity_risk_pct  = getattr(self.config, 'MAX_EQUITY_RISK_PERCENT', 3.0)
        get_cap          = getattr(self.config, 'get_leverage_cap', None)
        abs_max_leverage = get_cap(confidence) if callable(get_cap) \
            else getattr(self.config, 'FUTURES_MAX_LEVERAGE', 10)
        min_atr_pct      = getattr(self.config, 'MIN_ATR_PERCENT', 0.1)
        confidence       = trade_params.get('confidence', 0.5)
        current_drawdown = account_state.get('drawdown_percent', 0)
        symbol           = trade_params.get('symbol', 'UNKNOWN')
        strategy         = trade_params.get('strategy', 'Unknown')

        # --- Step 1: ATR-based maximum safe leverage ---
        atr = trade_params.get('atr', None)
        entry_price = trade_params.get('entry_price', 0)
        if atr and entry_price and entry_price > 0:
            atr_pct = (atr / entry_price) * 100
            atr_pct = max(atr_pct, min_atr_pct)  # Floor to avoid div/0
            atr_safe_max = equity_risk_pct / atr_pct
        else:
            # No ATR available — use conservative fallback
            atr_safe_max = 5.0
            self.logger.debug(f"[{strategy}] No ATR data for {symbol}, using conservative 5x ATR max")

        # --- Step 2: Scale within ATR max by confidence ---
        # Confidence 0.60 -> 30% of ATR max, Confidence 1.0 -> 100% of ATR max
        conf_clamped = max(min(confidence, 1.0), 0.0)
        conf_scale = 0.3 + (0.7 * conf_clamped)  # Range: 0.30 -> 1.00
        leverage_from_conf = atr_safe_max * conf_scale

        # --- Step 3: Apply all caps ---
        # Strategy-specific leverage cap (e.g. A5 limited to 5x)
        strategy_max = abs_max_leverage
        if "A5" in strategy:
            strategy_max = getattr(self.config, 'A5_MAX_LEVERAGE', 5)

        drawdown_max = self._get_drawdown_adjusted_max(current_drawdown)
        final_leverage = int(min(leverage_from_conf, abs_max_leverage, strategy_max, drawdown_max))
        final_leverage = max(final_leverage, 1)  # Always at least 1x

        # Log the full calculation for transparency
        atr_pct_display = (atr / entry_price * 100) if atr and entry_price else 0
        self.logger.debug(
            f"[{strategy}] {symbol} Dynamic Leverage -> "
            f"ATR: {atr_pct_display:.2f}% | ATR-Safe Max: {atr_safe_max:.1f}x | "
            f"Conf: {confidence:.2f} (scale: {conf_scale:.2f}) | "
            f"Drawdown Cap: {drawdown_max}x | Final: {final_leverage}x"
        )

        trade_params['leverage'] = final_leverage
        trade_params['leverage_breakdown'] = {
            'atr_safe_max': round(atr_safe_max, 2),
            'confidence_scale': round(conf_scale, 2),
            'drawdown_cap': drawdown_max,
            'final': final_leverage
        }

        return trade_params
