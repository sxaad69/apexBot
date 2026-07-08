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
        
        # Max leverage boundaries
        max_leverage = getattr(self.config, 'FUTURES_MAX_LEVERAGE', 10)
        
        # 2. Check if strategy provided a technical Stop Loss (ATR)
        strategy_sl = trade_params.get('stop_loss')
        
        if strategy_sl and entry_price > 0:
            # Calculate the percentage distance of the ATR stop loss
            distance_percent = abs(entry_price - strategy_sl) / entry_price * 100
            
            if distance_percent > 0:
                # 3. Volatility-Adjusted Leverage
                # Leverage = Target ROE Loss / Distance %
                calculated_leverage = global_target_roe / distance_percent
                
                # Cap leverage to safety limits
                final_leverage = max(1, min(int(calculated_leverage), max_leverage))
                
                # Verify the final ROE loss to ensure it doesn't violate MAX_ROE_DRAWDOWN
                max_roe_drawdown = getattr(self.config, 'MAX_ROE_DRAWDOWN', 20.0)
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
