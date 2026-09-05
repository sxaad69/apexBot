"""Layer 3: Stop Loss & Leverage Synchronization"""
from typing import Dict, Any, Optional

class StopLossManagementLayer:
    def __init__(self, config, logger):
        self.config = config
        self.logger = logger
    
    def evaluate(self, trade_params: Dict[str, Any], account_state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        entry_price = trade_params.get('entry_price', 0)
        side = trade_params.get('side', 'buy').lower().strip()
        strategy_tag = trade_params.get('strategy', '')
        
        # 1. Fetch Global Target ROE (The Disaster Limit we want to synchronize to)
        global_target_roe = getattr(self.config, 'GLOBAL_STOP_LOSS_ROE', 10.0)
        
        # Override for specific strategies if configured
        if "A5" in strategy_tag or "A6" in strategy_tag:
            override_key = f"{strategy_tag.split(':')[0]}_STOP_LOSS_ROE"
            global_target_roe = getattr(self.config, override_key, 10.0)

        # 2026-09-05 elite risk uplift: conf >= ELITE_CONFIDENCE_LEVEL trades get a
        # bigger ROE budget (ELITE_STOP_LOSS_ROE, default 15) -> 6x at the 2.5%
        # raw-stop floor instead of 4x. Applies ONLY to the band that historically
        # printed (+$57.70 all-time vs -25.56 for 0.85-0.90); every other band keeps
        # the strategy's 10% budget. The MAX_ROE_DRAWDOWN guard below still bounds
        # the final stop — see the max() there.
        confidence = trade_params.get('confidence', 0.5)
        elite_roe = float(getattr(self.config, 'ELITE_STOP_LOSS_ROE', 0) or 0)
        if elite_roe > 0 and confidence >= float(getattr(self.config, 'ELITE_CONFIDENCE_LEVEL', 0.90)):
            global_target_roe = elite_roe

        # Max leverage boundaries (Phase-1 core fix: working cap is now
        # confidence-aware via config.get_leverage_cap — 5x base, 6x conf>=0.90 —
        # NOT the absolute FUTURES_MAX_LEVERAGE which is only the emergency ceiling).
        max_leverage = getattr(self.config, 'get_leverage_cap', None)
        if callable(max_leverage):
            max_leverage = max_leverage(confidence)
        else:
            max_leverage = getattr(self.config, 'FUTURES_MAX_LEVERAGE', 10)
        
        # 2. Check if strategy provided a technical Stop Loss (ATR)
        strategy_sl = trade_params.get('stop_loss')
        
        if strategy_sl and entry_price > 0:
            # Calculate the percentage distance of the ATR stop loss
            distance_percent = abs(entry_price - strategy_sl) / entry_price * 100

            # Phase-1 core fix: enforce a minimum RAW stop distance. At high
            # leverage the ATR stop collapses to wick-noise distance (0.71% at
            # 10x, 0-for-17 SLs). Wide the stop to the floor if the strategy
            # placed it closer; leverage then recomputes OFF the wider distance,
            # so the ROE risk stays bounded without forcing leverage down to 1x.
            min_raw = getattr(self.config, 'MIN_RAW_STOP_PERCENT', 2.5)
            if distance_percent < min_raw:
                self.logger.warning(f"[{strategy_tag}] ATR stop too tight ({distance_percent:.2f}% raw < {min_raw}% floor). Widening.")
                distance_percent = min_raw
                if side == 'buy':
                    strategy_sl = entry_price * (1 - distance_percent / 100)
                else:
                    strategy_sl = entry_price * (1 + distance_percent / 100)
            
            if distance_percent > 0:
                # 3. Volatility-Adjusted Leverage
                # Leverage = Target ROE Loss / Distance %
                calculated_leverage = global_target_roe / distance_percent
                
                # Cap leverage to safety limits
                final_leverage = max(1, min(int(calculated_leverage), max_leverage))
                
                # Verify the final ROE loss to ensure it doesn't violate MAX_ROE_DRAWDOWN
                # 2026-09-05: the effective ceiling is max(drawdown_cap, target_roe) so
                # the elite 15% budget isn't silently tightened back to a 1.67% wick
                # stop (the Aug-13 failure mode). Non-elite paths: max(10, 10) = 10 —
                # behavior unchanged.
                max_roe_drawdown = max(
                    getattr(self.config, 'MAX_ROE_DRAWDOWN', 20.0),
                    global_target_roe,
                )
                actual_roe_loss = distance_percent * final_leverage
                
                # If even at 1x leverage the ATR stop loses too much, tighten the stop loss
                if actual_roe_loss > max_roe_drawdown:
                    self.logger.warning(f"[{strategy_tag}] Strategy ATR too wide ({actual_roe_loss:.1f}% ROE). Tightening to {max_roe_drawdown}% Max.")
                    distance_percent = max_roe_drawdown / final_leverage
                    if side == 'buy':
                        strategy_sl = entry_price * (1 - distance_percent / 100)
                    else:
                        strategy_sl = entry_price * (1 + distance_percent / 100)
                    actual_roe_loss = distance_percent * final_leverage
                
                # Apply synchronized parameters
                trade_params['stop_loss'] = strategy_sl
                trade_params['leverage'] = final_leverage
                trade_params['stop_loss_percent'] = distance_percent
                trade_params['stop_loss_roe'] = actual_roe_loss
                
                self.logger.info(f"[{strategy_tag}] Volatility Sync: ATR Distance {distance_percent:.2f}% | Assigned Leverage: {final_leverage}x | Risk: {actual_roe_loss:.1f}% ROE")
        else:
            # Fallback: Strategy didn't provide a stop loss, enforce strict math
            leverage = trade_params.get('leverage', 1)
            distance_percent = global_target_roe / leverage
            
            if side == 'buy':
                fallback_sl = entry_price * (1 - distance_percent / 100)
            else:
                fallback_sl = entry_price * (1 + distance_percent / 100)
            
            trade_params['stop_loss'] = fallback_sl
            trade_params['stop_loss_percent'] = distance_percent
            trade_params['stop_loss_roe'] = global_target_roe
            
            self.logger.debug(f"[{strategy_tag}] Strict Math Fallback: SL Distance {distance_percent:.2f}% | Risk: {global_target_roe}% ROE")
        
        return trade_params
