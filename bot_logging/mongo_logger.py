"""
MongoDB Logger
Extends the base logger to include MongoDB logging capabilities
"""

import asyncio
import json
from typing import Dict, Any, Optional
from datetime import datetime
from .logger import Logger, LogCategory
from database.json_manager import JSONManager
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

        # 1. Initialize SQLite FIRST (Robustness Priority)
        self.sqlite_enabled = getattr(config, 'SQLITE_ENABLED', True)
        self.db = None
        if self.sqlite_enabled:
            try:
                self.db = SQLiteManager(config)
                print("🏦 SQLite Storage active (Robust Trade Tracking)")
            except Exception as e:
                print(f"⚠️ SQLite initialization error: {e}")
                self.sqlite_enabled = False

        # 2. Determine MongoDB storage manager
        self.mongo_manager = None
        use_mongo_db = getattr(config, 'MONGODB_ENABLED', False)
                
        if use_mongo_db:
            try:
                from database.mongo_manager import MongoManager
                print("🍃 Attempting to connect to MongoDB Atlas...")
                self.mongo_manager = MongoManager(config)
                
                if not self.mongo_manager.is_connected:
                    self.system("⚠️ MongoDB connection failed, falling back to JSON storage")
                    self.mongo_manager = JSONManager(config)
                else:
                    self.system(f"🍃 MongoDB Atlas connected: {config.MONGODB_DATABASE}")
            except ImportError:
                self.system("⚠️ MongoDB drivers (pymongo/motor) not found, using JSON storage")
                self.mongo_manager = JSONManager(config)
            except Exception as e:
                self.system(f"⚠️ MongoDB initialization error: {e}")
                self.mongo_manager = JSONManager(config)
        
        # If no manager was set, use JSON
        if self.mongo_manager is None:
            self.mongo_manager = JSONManager(config)
            self.system("📂 Using local JSON storage (MongoDB Atlas disabled)")

        # 3. Logging flags
        self.async_queue = asyncio.Queue()
        self.async_logging_enabled = False
        if self.mongo_manager.is_connected:
            self.async_logging_enabled = True

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
            document = {
                'timestamp': datetime.utcnow(),
                'category': category,
                'level': level,
                'message': message,
                'metadata': kwargs or {}
            }
            
            # 1. Log to SQLite activity_log (Always if enabled)
            if self.sqlite_enabled:
                symbol = kwargs.get('symbol', 'SYSTEM')
                self.db.log_activity(category, symbol, message, document)

            # 2. Log to MongoDB/JSON fallback (Legacy/Secondary)
            if self.mongo_manager and self.mongo_manager.is_connected:
                collection_map = {
                    'api_calls': 'system_logs',
                    'position_rejections': 'risk_rejections',
                    'token_metrics': 'system_logs',
                    'risk_management': 'system_logs',
                    'trade_execution': 'system_logs',
                    'performance': 'system_logs',
                    'system_events': 'system_logs',
                    'error_traces': 'system_logs'
                }
                collection = collection_map.get(category, 'system_logs')
                self.mongo_manager.insert_document(collection, document)

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

    def trade_entry(self, symbol: str, side: str, size: float, price: float, leverage: int, market_type: str = 'futures', **kwargs):
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

        # 1. Log to SQLite (Robust Entry)
        if self.sqlite_enabled:
            trade_data = {
                'trade_id': kwargs.get('trade_id'),
                'symbol': symbol,
                'market_type': market_type,
                'side': side,
                'entry_price': price,
                'leverage': leverage,
                'stop_loss': kwargs.get('stop_loss'),
                'take_profit': kwargs.get('take_profit'),
                'strategy': kwargs.get('strategy'),
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': kwargs
            }
            self.db.open_trade(trade_data)

        # 2. Route to correct collection
        if market_type == 'spot':
            document['executed'] = True
            self.mongo_manager.insert_document('spot_signals', document)
        else:
            self.mongo_manager.insert_document('futures_trades', document)

    def trade_exit(self, symbol: str, pnl: float, pnl_percent: float, duration: str, market_type: str = 'futures', **kwargs):
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
                'timestamp': datetime.utcnow().isoformat(),
                'metadata': kwargs
            }
            self.db.close_trade(kwargs.get('trade_id'), exit_data)

        # 2. Route to correct collection (Legacy Fallback)
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
        """Log arbitrage opportunity to MongoDB"""
        document = {
            'type': opportunity.get('type'),
            'symbols': opportunity.get('symbols', []),
            'exchanges': opportunity.get('exchanges', []),
            'buy_price': opportunity.get('buy_price'),
            'sell_price': opportunity.get('sell_price'),
            'spread_percent': opportunity.get('spread_percent'),
            'profit_amount': opportunity.get('profit_amount'),
            'profit_percent': opportunity.get('profit_percent'),
            'executed': opportunity.get('executed', False),
            'fees': opportunity.get('fees'),
            'net_profit': opportunity.get('net_profit'),
            'metadata': opportunity
        }

        self.mongo_manager.insert_document('arbitrage_opportunities', document)

    def save_market_analysis(self, date: str, hour: str, analysis_data: Dict[str, Any]) -> bool:
        """Save market analysis data to SQLite (New) or MongoDB/JSON (Legacy)"""
        try:
            # 1. Prefer SQLite (Consolidated)
            if self.sqlite_enabled:
                # We save summary stats but we could also save individual pair data if needed
                symbol = analysis_data.get('trading_type', 'futures')
                price = 0.0 # Not applicable to summary
                self.db.save_analysis(symbol, price, analysis_data)

            # 2. Try MongoDB
            if self.mongo_manager.is_connected:
                document = {
                    'date': date,
                    'hour': hour,
                    'trading_type': analysis_data.get('trading_type', 'futures'),
                    'total_analyses': analysis_data.get('total_analyses', 0),
                    'timestamp': datetime.utcnow()
                }
                self.mongo_manager.insert_document('market_analyses', document)
                return True
            else:
                # 3. Fallback to JSON
                return self._save_market_analysis_json(date, analysis_data)

        except Exception as e:
            print(f"⚠️ Market analysis logging failed: {e}")
            return False

    def save_strategy_signals(self, date: str, hour: str, strategy_data: Dict[str, Any]) -> bool:
        """Save strategy signals data to SQLite (New) or MongoDB/JSON (Legacy)"""
        try:
            # 1. Prefer SQLite (Consolidated)
            if self.sqlite_enabled:
                # Loop through pairs in strategy data
                for symbol, data in strategy_data.items():
                    if isinstance(data, dict) and 'action' in data:
                        self.db.save_signal(
                            symbol, 
                            data.get('strategy', 'Unknown'), 
                            data.get('action'), 
                            data.get('confidence', 0.0), 
                            data
                        )

            # 2. Try MongoDB
            if self.mongo_manager.is_connected:
                document = {
                    'date': date,
                    'hour': hour,
                    'trading_type': strategy_data.get('trading_type', 'futures'),
                    **{k: v for k, v in strategy_data.items() if k not in ['date', 'hour', 'trading_type']},
                    'timestamp': datetime.utcnow()
                }
                self.mongo_manager.insert_document('strategy_signals', document)
                return True
            else:
                # 3. Fallback to JSON
                return self._save_strategy_signals_json(date, strategy_data)

        except Exception as e:
            print(f"⚠️ Strategy signals logging failed: {e}")
            return False

    def save_hourly_metrics(self, date: str, hour: str, metrics_data: Dict[str, Any]) -> bool:
        """Save hourly trading metrics using UPSERT to SQLite or MongoDB"""
        try:
            trading_type = metrics_data.get('trading_type', 'futures')
            
            # 1. Prefer SQLite for metrics (Robust)
            if self.sqlite_enabled:
                inc_vars = {
                    'signals_generated': metrics_data.get('signals_generated', 0),
                    'trades_executed': metrics_data.get('trades_executed', 0),
                    'total_rejections': metrics_data.get('total_rejections', 0)
                }
                self.db.upsert_metrics(date, hour, trading_type, inc_vars)
            
            # 2. Try MongoDB (Legacy/Secondary)
            if hasattr(self.mongo_manager, 'upsert_document') and self.mongo_manager.is_connected:
                filter_query = {'date': date, 'hour': hour, 'type': trading_type}
                update_data = {
                    '$set': {
                        'timestamp': datetime.utcnow(),
                        'conversion_rate': metrics_data.get('conversion_rate', 0.0)
                    },
                    '$inc': {
                        'signals_generated': metrics_data.get('signals_generated', 0),
                        'trades_executed': metrics_data.get('trades_executed', 0),
                        'volume_rejections': metrics_data.get('volume_rejections', 0),
                        'adx_rejections': metrics_data.get('adx_rejections', 0),
                        'volatility_rejections': metrics_data.get('volatility_rejections', 0),
                        'other_rejections': metrics_data.get('other_rejections', 0),
                        'total_rejections': metrics_data.get('total_rejections', 0)
                    }
                }
                self.mongo_manager.upsert_document('metrics_summary', filter_query, update_data)
                return True
            else:
                # 3. Fallback to JSON file
                return self._save_hourly_metrics_json(date, metrics_data)

        except Exception as e:
            print(f"⚠️ Metrics summary update failed: {e}")
            return False

    def _save_market_analysis_json(self, date: str, analysis_data: Dict[str, Any]) -> bool:
        """Save market analysis data to JSON file (MongoDB fallback)"""
        try:
            filename = f"market_analyses_{date.replace('-', '')}.json"
            filepath = self.mongo_manager.data_dir / filename

            # Load existing data or create new
            existing_data = {}
            if filepath.exists():
                try:
                    with open(filepath, 'r') as f:
                        existing_data = json.load(f)
                except:
                    existing_data = {}

            # Add new analysis data
            hour_key = analysis_data.get('hour', '00:00')
            existing_data[hour_key] = analysis_data

            # Save back to file
            with open(filepath, 'w') as f:
                json.dump(existing_data, f, indent=2, default=str)

            return True

        except Exception as e:
            print(f"⚠️ JSON market analysis save failed: {e}")
            return False

    def _save_strategy_signals_json(self, date: str, strategy_data: Dict[str, Any]) -> bool:
        """Save strategy signals data to JSON file (MongoDB fallback)"""
        try:
            filename = f"strategy_signals_{date.replace('-', '')}.json"
            filepath = self.mongo_manager.data_dir / filename

            # Load existing data or create new
            existing_data = {}
            if filepath.exists():
                try:
                    with open(filepath, 'r') as f:
                        existing_data = json.load(f)
                except:
                    existing_data = {}

            # Add new strategy data
            hour_key = strategy_data.get('hour', '00:00')
            existing_data[hour_key] = strategy_data

            # Save back to file
            with open(filepath, 'w') as f:
                json.dump(existing_data, f, indent=2, default=str)

            return True

        except Exception as e:
            print(f"⚠️ JSON strategy signals save failed: {e}")
            return False

    def _save_hourly_metrics_json(self, date: str, metrics_data: Dict[str, Any]) -> bool:
        """Save hourly metrics data to JSON file (MongoDB fallback)"""
        try:
            filename = f"hourly_metrics_{date.replace('-', '')}.json"
            filepath = self.mongo_manager.data_dir / filename

            # Load existing data or create new
            existing_data = {}
            if filepath.exists():
                try:
                    with open(filepath, 'r') as f:
                        existing_data = json.load(f)
                except:
                    existing_data = {}

            # Add new metrics data
            hour_key = metrics_data.get('hour', '00:00')
            existing_data[hour_key] = metrics_data

            # Save back to file
            with open(filepath, 'w') as f:
                json.dump(existing_data, f, indent=2, default=str)

            return True

        except Exception as e:
            print(f"⚠️ JSON hourly metrics save failed: {e}")
            return False

    def debug(self, message: str, **kwargs):
        """Log debug message to dedicated debug_logs.json file"""
        # Always call parent debug method (for console/file logging based on config)
        super().debug(message, **kwargs)

        # Additionally log to debug_logs.json if LOG_OUTPUT includes db
        if self.log_to_db:
            try:
                document = {
                    'message': message,
                    'metadata': kwargs or {}
                }
                self.mongo_manager.insert_document('debug_logs', document)
            except Exception as e:
                # Don't let debug logging failures break the application
                print(f"⚠️ Debug logging to DB failed: {e}")

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

    def cleanup_old_logs(self):
        """Clean up expired log entries based on retention policy"""
        if self.mongo_manager.is_connected:
            self.mongo_manager.cleanup_expired_documents()

    def get_mongodb_status(self) -> Dict[str, Any]:
        """Get MongoDB connection status"""
        return {
            'connected': self.mongo_manager.is_connected,
            'database': self.config.MONGODB_DATABASE if hasattr(self.config, 'MONGODB_DATABASE') else None,
            'async_logging': self.async_logging_enabled
        }

    def close(self):
        """Close MongoDB connections"""
        if hasattr(self, 'mongo_manager'):
            self.mongo_manager.close()
