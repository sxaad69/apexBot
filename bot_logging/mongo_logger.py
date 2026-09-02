"""
MongoDB Logger
Extends the base logger to include MongoDB logging capabilities
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from .logger import Logger, LogCategory
from database.sqlite_manager import SQLiteManager
from config.config import Config


class MongoLogger(Logger):
    """
    Enhanced logger with MongoDB support
    Logs to both files and MongoDB database
    """

    def __init__(self, config):
        self.config = config
        super().__init__(config)

        # 1. Initialize SQLite (Sole Data Source)
        self.sqlite_enabled = True # Always enabled for unified storage
        try:
            self.db = SQLiteManager(config)
            print("🏦 SQLite Unified Storage active (EXCLUISVE MODE)")
        except Exception as e:
            print(f"❌ CRITICAL: SQLite initialization error: {e}")
            self.sqlite_enabled = False
            # We don't fallback to JSON/Mongo anymore as requested

        # 3. Logging flags
        self.async_logging_enabled = False

        self.log_to_db = False
        if hasattr(config, 'LOG_OUTPUT'):
            log_output = config.LOG_OUTPUT.lower()
            if log_output in ['both', 'db']:
                self.log_to_db = True

    def _log(self, category: LogCategory, level: int, message: str, **kwargs):
        """Override base _log to include database dispatching"""
        # 1. Standard file/console logging (Parent)
        super()._log(category, level, message, **kwargs)

        # 2. Database dispatching (Unified SQLite/Mongo)
        # Convert numeric level back to name for convenience
        import logging
        level_name = logging.getLevelName(level)
        self._log_to_mongodb(category.value, level_name, message, **kwargs)

    def _log_to_mongodb(self, category: str, level: str, message: str, **kwargs):
        """Unified Dispatcher: Log to SQLite (Primary) and MongoDB/JSON (Secondary)"""
        try:
            # 1. Skip redundant high-volume data already stored in structured tables
            if category in ['position_rejections', 'trade_execution']:
                return

            document = {
                'timestamp': datetime.utcnow(),
                'category': category,
                'level': level,
                'message': message,
                'metadata': kwargs or {}
            }
            
            # 2. Log to SQLite activity_log
            if self.sqlite_enabled:
                symbol = kwargs.get('symbol', 'SYSTEM')
                self.db.log_activity(category, symbol, message, document)

        except Exception as e:
            # Avoid recursive errors
            print(f"⚠️ Dispatch logging failed: {e}")

    def _should_log(self, level_name: str) -> bool:
        """Check if a message level should be logged based on config"""
        import logging
        target_level = getattr(logging, level_name.upper(), logging.INFO)
        current_level = getattr(logging, self.config.LOG_LEVEL.upper(), logging.INFO)
        return target_level >= current_level

    # ===== Override base logging methods to include MongoDB =====

    def api_call(self, method: str, url: str, status: Optional[int] = None, duration: Optional[float] = None, **kwargs):
        """Log API call details to both file and MongoDB"""
        # Call parent method for file logging
        super().api_call(method, url, status, duration, **kwargs)

        # Log to MongoDB (Respect level)
        if self.log_to_db and self._should_log('DEBUG'):
            self._log_to_mongodb('activity_log', 'DEBUG', f"API Call: {method} {url}",
                               type='api_call', status=status, duration_ms=f"{duration*1000:.2f}" if duration else None, **kwargs)

    def position_rejected(self, symbol: str, reason: str, layer: str, **kwargs):
        """Log rejected position with reason to all enabled stores"""
        # Call parent for console/file
        super().position_rejected(symbol, reason, layer, **kwargs)

        if self.log_to_db and self._should_log('INFO'):
            msg = f"Position Rejected: {symbol} | Reason: {reason} | Layer: {layer}"
            self._log_to_mongodb('position_rejections', 'INFO', msg, 
                               symbol=symbol, reason=reason, layer=layer, **kwargs)

    def token_usage(self, endpoint: str, tokens: int, total: int):
        """Log API token usage to all enabled stores"""
        super().token_usage(endpoint, tokens, total)
        if self.log_to_db and self._should_log('DEBUG'):
            self._log_to_mongodb('token_metrics', 'DEBUG', f"Token Usage: {endpoint}",
                               endpoint=endpoint, tokens=tokens, total=total)

    def risk_layer_triggered(self, layer: str, reason: str, action: str, **kwargs):
        """Log risk layer activation to all enabled stores"""
        super().risk_layer_triggered(layer, reason, action, **kwargs)
        if self.log_to_db and self._should_log('WARNING'):
            self._log_to_mongodb('risk_management', 'WARNING', f"Risk Layer Triggered: {layer}",
                               layer=layer, reason=reason, action=action, **kwargs)

    def trade_entry(self, symbol: str, side: str, size: float, price: float, leverage: int, market_type: str = 'futures', total_capital: float = 0.0, **kwargs):
        """Log trade entry to both file and MongoDB"""
        # Call parent method for file logging
        super().trade_entry(symbol, side, size, price, leverage, market_type, **kwargs)

        # Log complete trade entry to MongoDB
        document = {
            'symbol': symbol,
            'side': side,
            'entry_price': price,
            'position_size': size,
            'leverage': leverage,
            'strategy': kwargs.get('strategy', 'Unknown'),
            'confidence': kwargs.get('confidence', 0),
            'stop_loss': kwargs.get('stop_loss'),
            'take_profit': kwargs.get('take_profit'),
            'market_type': market_type,
            'timestamp': datetime.utcnow(),
            'metadata': kwargs
        }

        # 1. Log to SQLite (Robust Entry) - REMOVED (Redundant with TradeManager)
        # Note: TradeManager.record_entry already persists to SQLite. 
        # Redundant writes here cause ghost rows with empty IDs.
        pass

        # 2. Route to correct collection (Only if Mongo is actually enabled)
        if hasattr(self, 'mongo_manager') and self.mongo_manager:
            if market_type == 'spot':
                document['executed'] = True
                self.mongo_manager.insert_document('spot_signals', document)
            else:
                self.mongo_manager.insert_document('futures_trades', document)

    def trade_exit(self, symbol: str, pnl: float, pnl_percent: float, duration: str, market_type: str = 'futures', total_capital: float = 0.0, **kwargs):
        """Log trade exit to both file and MongoDB"""
        # Call parent method for file logging
        super().trade_exit(symbol, pnl, pnl_percent, duration, market_type, **kwargs)

        # Log complete trade exit to MongoDB/JSON
        document = {
            'type': 'exit',
            'symbol': symbol,
            'exit_price': kwargs.get('exit_price'),
            'pnl_amount': pnl,
            'pnl_percent': pnl_percent,
            'duration': duration,
            'reason': kwargs.get('reason', 'manual'),
            'strategy': kwargs.get('strategy', 'Unknown'),
            'entry_price': kwargs.get('entry_price'),
            'side': kwargs.get('side'),
            'leverage': kwargs.get('leverage', 1),
            'stop_loss': kwargs.get('stop_loss'),
            'take_profit': kwargs.get('take_profit'),
            'market_type': market_type,
            'timestamp': datetime.utcnow(),
            'metadata': {k: v for k, v in kwargs.items() if k not in ['exit_price', 'reason', 'strategy', 'entry_price', 'side', 'leverage', 'stop_loss', 'take_profit']}
        }

        # 1. Log to SQLite (Robust Exit)
        if self.sqlite_enabled and kwargs.get('trade_id'):
            exit_data = {
                'exit_price': kwargs.get('exit_price'),
                'pnl_amount': pnl,
                'pnl_percent': pnl_percent,
                'reason': kwargs.get('reason', 'manual'),
                'capital_at_exit': total_capital,
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': kwargs

            }
            self.db.close_trade(kwargs.get('trade_id'), exit_data)

        # 2. Route to correct collection (Legacy Fallback)
        if hasattr(self, 'mongo_manager') and self.mongo_manager:
            if market_type == 'spot':
                document['executed'] = True
                self.mongo_manager.insert_document('spot_signals', document)
            else:
                self.mongo_manager.insert_document('futures_trades', document)

    def performance_update(self, total_pnl: float, win_rate: float, total_trades: int, **kwargs):
        """Log performance metrics to both file and MongoDB"""
        # Call parent method for file logging
        super().performance_update(total_pnl, win_rate, total_trades, **kwargs)

        # Log to MongoDB
        self._log_to_mongodb('performance', 'INFO', "Performance Update",
                           total_pnl=f"{total_pnl:+.2f} USDT",
                           win_rate=f"{win_rate:.1f}%",
                           trades=total_trades, **kwargs)

    def system(self, message: str, **kwargs):
        """Log system event to both file and MongoDB"""
        # Call parent method for file logging
        super().system(message, **kwargs)

        # Log to MongoDB
        self._log_to_mongodb('system_events', 'INFO', message, **kwargs)

    def error(self, message: str, exc_info: bool = False, **kwargs):
        """Log error to both file and MongoDB"""
        # Call parent method for file logging
        super().error(message, exc_info, **kwargs)

        # Log to MongoDB
        self._log_to_mongodb('error_traces', 'ERROR', message, **kwargs)

    # ===== MongoDB-specific methods =====

    def log_trailing_stop(self, action: str, symbol: str, strategy: str,
                          current_price: float, profit_percent: float,
                          old_stop: float, new_stop: float, **kwargs):
        """Log trailing stop actions to MongoDB"""
        if not self.log_to_db or not self._should_log('DEBUG'):
            return

        document = {
            'type': 'trailing_stop',
            'action': action,  # 'activated' or 'updated'
            'symbol': symbol,
            'strategy': strategy,
            'current_price': current_price,
            'profit_percent': profit_percent,
            'old_stop_loss': old_stop,
            'new_stop_loss': new_stop,
            'highest_price': kwargs.get('highest_price'),
            'lowest_price': kwargs.get('lowest_price'),
            'position_side': kwargs.get('position_side'),
            'timestamp': datetime.utcnow(),
            'expires_at': datetime.utcnow() + timedelta(days=7),
            'metadata': kwargs
        }

        if hasattr(self, 'mongo_manager') and self.mongo_manager:
            self.mongo_manager.insert_document('activity_log', document)

    def log_spot_signal(self, signal: Dict[str, Any]):
        """Log spot trading signal to MongoDB"""
        document = {
            'symbol': signal.get('symbol'),
            'side': signal.get('side'),
            'entry_price': signal.get('entry_price'),
            'stop_loss': signal.get('stop_loss'),
            'take_profit': signal.get('take_profit'),
            'strategy': signal.get('strategy'),
            'confidence': signal.get('confidence'),
            'executed': signal.get('executed', False),
            'pnl_amount': signal.get('pnl_amount'),
            'pnl_percent': signal.get('pnl_percent'),
            'metadata': signal
        }

        if hasattr(self, 'mongo_manager') and self.mongo_manager:
            self.mongo_manager.insert_document('spot_signals', document)

    def save_active_positions(self, positions: Dict[str, Any], current_prices: Dict[str, float] = None):
        """Save/Update active positions in SQLite (Unified Storage)"""
        try:
            if positions is None:
                positions = {}
            
            # 1. Primary: SQLite
            if self.sqlite_enabled:
                self.db.save_active_positions_snapshot(positions, current_prices)
                
            # 2. Secondary: JSON for external script compatibility (fallback)
            try:
                from dashboard.json_manager import JSONManager
                json_manager = JSONManager(self.config)
                json_manager.save_active_positions(positions)
            except:
                pass
            
            return True
            
        except Exception as e:
            print(f"⚠️ Failed to update active positions snapshot: {e}")
            return False

    def log_arbitrage_opportunity(self, opportunity: Dict[str, Any]):
        """Arbitrage logging disabled in unified SQLite for now"""
        pass

    def save_market_analysis(self, date: str, hour: str, analysis_data: Dict[str, Any]) -> bool:
        """Save market analysis data to SQLite (Exclusive)"""
        try:
            if self.sqlite_enabled:
                symbol = analysis_data.get('trading_type', 'futures')
                price = 0.0 # Not applicable to summary
                self.db.save_analysis(symbol, price, analysis_data)
                return True
            return False
        except Exception as e:
            print(f"❌ SQLite analysis logging failed: {e}")
            return False

    def save_market_analysis_bulk(self, records: List[Dict[str, Any]]) -> bool:
        """Save market analysis data to SQLite in bulk"""
        try:
            if self.sqlite_enabled and records:
                self.db.save_analysis_bulk(records)
                return True
            return False
        except Exception as e:
            print(f"❌ SQLite bulk analysis logging failed: {e}")
            return False



    def save_hourly_metrics(self, date: str, hour: str, metrics_data: Dict[str, Any]) -> bool:
        """Save hourly trading metrics to SQLite (Exclusive)"""
        try:
            trading_type = metrics_data.get('trading_type', 'futures')
            if self.sqlite_enabled:
                inc_vars = {
                    'signals_generated': metrics_data.get('signals_generated', 0),
                    'trades_executed': metrics_data.get('trades_executed', 0),
                    'total_rejections': metrics_data.get('total_rejections', 0)
                }
                self.db.upsert_metrics(date, hour, trading_type, inc_vars)
                return True
            return False
        except Exception as e:
            print(f"❌ SQLite metrics logging failed: {e}")
            return False

    def debug(self, message: str, **kwargs):
        """Log debug message to console/file (DB debug logging removed)"""
        super().debug(message, **kwargs)

    def log_sweep_summary(self, sweep_stats: Dict[str, Any]):
        """
        Log aggregated sweep statistics to SQLite.
        Saves disk space by writing one row per minute instead of thousands.
        """
        if not self.log_to_db or not self.sqlite_enabled:
            return

        try:
            document = {
                'timestamp': datetime.utcnow().isoformat() + "Z",
                'sweep_duration_sec': sweep_stats.get('duration_sec', 0),
                'symbols_scanned': sweep_stats.get('symbols_scanned', 0),
                'entries_executed': sweep_stats.get('entries_executed', 0),
                'batch_cap_skipped': sweep_stats.get('batch_cap_skipped', 0),
                'strategy_rejections': sweep_stats.get('strategy_rejections', {}),
                'risk_rejections': sweep_stats.get('risk_rejections', {})
            }
            
            # Use log_activity to store the sweep summary
            self.db.log_activity('sweep_summary', 'SYSTEM', f"Sweep Complete: {document['symbols_scanned']} symbols scanned.", document)
            
            # Log high-level summary to console
            skipped_str = f" | ⏳ {document['batch_cap_skipped']} batch-skipped" if document['batch_cap_skipped'] > 0 else ""
            self.info(f"🧹 SWEEP COMPLETE | {document['symbols_scanned']} symbols | {document['entries_executed']} entries | duration: {document['sweep_duration_sec']:.1f}s{skipped_str}")
        except Exception as e:
            self.error(f"Failed to log sweep summary: {e}")

    def _get_layer_number(self, layer_name: str) -> int:
        """Get risk layer number from name"""
        layer_map = {
            'PositionSizingLayer': 1,
            'LeverageControlLayer': 2,
            'StopLossManagementLayer': 3,
            'DailyLossLimitLayer': 4,
            'MaximumDrawdownLayer': 5,
            'CorrelationRiskLayer': 6,
            'VolatilityAdjustmentLayer': 7,
            'LiquidityCheckLayer': 8,
            'RateLimitLayer': 9,
            'CircuitBreakerLayer': 10,
            'CapitalPreservationLayer': 11
        }
        return layer_map.get(layer_name, 0)

    # ===== Cleanup and maintenance =====

    def cleanup_old_logs(self, days: int = 15):
        """Clean up expired log entries in SQLite.

        Keeps the last `days` (default 15) of activity_log / strategy_signals
        and purges everything older, then VACUUMs to reclaim disk.
        """
        if self.sqlite_enabled:
            count = self.db.purge_old_activity(days=days)
            if count > 0:
                print(f"🧹 SQLite Cleanup: Purged {count} old entries")

    def get_mongodb_status(self) -> Dict[str, Any]:
        """Get storage status (SQLite only)"""
        return {
            'connected': self.sqlite_enabled,
            'type': 'SQLite (Unified)',
            'async_logging': False
        }

    def close(self):
        """SQLite close not explicitly required for standard operations"""
        pass
