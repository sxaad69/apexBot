"""
MongoDB Logger
Extends the base logger to include MongoDB logging capabilities
"""

import asyncio
from typing import Dict, Any, Optional
from datetime import datetime
from .logger import Logger
from database.json_manager import JSONManager


class MongoLogger(Logger):
    """
    Enhanced logger with MongoDB support
    Logs to both files and MongoDB database
    """

    def __init__(self, config):
        super().__init__(config)

        # Determine storage engine based on config
        use_mongo_db = False
        
        # Check for MongoDB Atlas connection string or credentials
        if hasattr(config, 'MONGODB_HOST') and config.MONGODB_HOST:
            if 'mongodb' in config.MONGODB_HOST or (hasattr(config, 'MONGODB_USERNAME') and config.MONGODB_USERNAME):
                use_mongo_db = True
                
        if use_mongo_db:
            try:
                from database.mongo_manager import MongoManager
                print("🍃 Attempting to connect to MongoDB Atlas...")
                self.mongo_manager = MongoManager(config)
                
                if not self.mongo_manager.is_connected:
                    print("⚠️ MongoDB connection failed, falling back to JSON storage")
                    self.mongo_manager = JSONManager(config)
            except ImportError:
                print("⚠️ MongoDB drivers (pymongo/motor) not found, using JSON storage")
                self.mongo_manager = JSONManager(config)
            except Exception as e:
                print(f"⚠️ MongoDB initialization error: {e}")
                print("⚠️ Falling back to JSON storage")
                self.mongo_manager = JSONManager(config)
        else:
            # Default to JSON storage
            self.mongo_manager = JSONManager(config)

        # Async logging queue
        self.async_queue = asyncio.Queue()
        self.async_logging_enabled = False

        # Start async logging if MongoDB is connected
        if self.mongo_manager.is_connected:
            self.async_logging_enabled = True
            # Note: In a real implementation, you'd start an async task here
            # For simplicity, we'll use sync logging in this demo

        # Check LOG_OUTPUT configuration for debug logging
        self.log_to_db = False
        if hasattr(config, 'LOG_OUTPUT'):
            log_output = config.LOG_OUTPUT.lower()
            if log_output in ['both', 'db']:
                self.log_to_db = True

    def _log_to_mongodb(self, category: str, level: str, message: str, **kwargs):
        """Log to MongoDB database"""
        if not self.mongo_manager.is_connected:
            return

        try:
            # Prepare document
            document = {
                'timestamp': datetime.utcnow(),
                'category': category,
                'level': level,
                'message': message,
                'metadata': kwargs or {}
            }

            # Determine collection based on category
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

            # Insert document
            self.mongo_manager.insert_document(collection, document)

        except Exception as e:
            # Don't let MongoDB logging failures break the application
            # Just log to console as fallback
            print(f"⚠️ MongoDB logging failed: {e}")

    # ===== Override base logging methods to include MongoDB =====

    def api_call(self, method: str, url: str, status: Optional[int] = None, duration: Optional[float] = None, **kwargs):
        """Log API call details to both file and MongoDB"""
        # Call parent method for file logging
        super().api_call(method, url, status, duration, **kwargs)

        # Log to MongoDB
        self._log_to_mongodb('api_calls', 'INFO', f"API Call: {method} {url}",
                           status=status, duration_ms=f"{duration*1000:.2f}" if duration else None, **kwargs)

    def position_rejected(self, symbol: str, reason: str, layer: str, **kwargs):
        """Log rejected position with reason to both file and MongoDB"""
        # Call parent method for file logging
        super().position_rejected(symbol, reason, layer, **kwargs)

        # Log to MongoDB with full rejection details
        document = {
            'symbol': symbol,
            'reason': reason,
            'layer_name': layer,
            'layer_number': self._get_layer_number(layer),
            'trade_params': kwargs.get('trade_params', {}),
            'account_state': kwargs.get('account_state', {}),
            'strategy': kwargs.get('strategy', 'Unknown'),
            'metadata': {k: v for k, v in kwargs.items()
                        if k not in ['trade_params', 'account_state', 'strategy']}
        }

        self.mongo_manager.insert_document('risk_rejections', document)

    def token_usage(self, endpoint: str, tokens: int, total: int):
        """Log API token usage to both file and MongoDB"""
        # Call parent method for file logging
        super().token_usage(endpoint, tokens, total)

        # Log to MongoDB
        self._log_to_mongodb('token_metrics', 'DEBUG', f"Token Usage: {endpoint}",
                           tokens=tokens, total=total)

    def risk_layer_triggered(self, layer: str, reason: str, action: str, **kwargs):
        """Log risk layer activation to both file and MongoDB"""
        # Call parent method for file logging
        super().risk_layer_triggered(layer, reason, action, **kwargs)

        # Log to MongoDB
        self._log_to_mongodb('risk_management', 'WARNING', f"Risk Layer Triggered: {layer}",
                           reason=reason, action=action, **kwargs)

    def trade_entry(self, symbol: str, side: str, size: float, price: float, leverage: int, **kwargs):
        """Log trade entry to both file and MongoDB"""
        # Call parent method for file logging
        super().trade_entry(symbol, side, size, price, leverage, **kwargs)

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
            'metadata': kwargs
        }

        # This will be updated with exit details later
        # For now, we log the entry
        self.mongo_manager.insert_document('futures_trades', document)

    def trade_exit(self, symbol: str, pnl: float, pnl_percent: float, duration: str, **kwargs):
        """Log trade exit to both file and MongoDB"""
        # Call parent method for file logging
        super().trade_exit(symbol, pnl, pnl_percent, duration, **kwargs)

        # Log complete trade exit to MongoDB
        document = {
            'symbol': symbol,
            'exit_price': kwargs.get('exit_price'),
            'pnl_amount': pnl,
            'pnl_percent': pnl_percent,
            'duration': duration,
            'reason': kwargs.get('reason', 'manual'),
            'strategy': kwargs.get('strategy', 'Unknown'),
            'entry_price': kwargs.get('entry_price'),
            'side': kwargs.get('side'),
            'leverage': kwargs.get('leverage'),
            'stop_loss': kwargs.get('stop_loss'),
            'take_profit': kwargs.get('take_profit'),
            'metadata': kwargs
        }

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
        document = {
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
            'metadata': kwargs
        }

        self.mongo_manager.insert_document('trailing_stops', document)

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
        """Save active positions with live prices for dashboard visibility"""
        try:
            if positions is None:
                positions = {}
                
            # Insert current positions
            docs = []
            for key, pos in positions.items():
                doc = pos.copy()
                doc['position_key'] = key
                
                # Add current price if available for unrealized P&L calculation
                symbol = pos.get('symbol')
                if current_prices and symbol in current_prices:
                    doc['current_price'] = current_prices[symbol]
                
                if 'timestamp' not in doc:
                    doc['timestamp'] = datetime.utcnow()
                docs.append(doc)
            
            # Use JSON manager's internal save method to overwrite
            self.mongo_manager._save_collection('active_positions', docs)
            return True
            
        except Exception as e:
            print(f"⚠️ Failed to save active positions: {e}")
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
        """Save market analysis data to MongoDB or JSON fallback"""
        try:
            document = {
                'date': date,
                'hour': hour,
                'trading_type': analysis_data.get('trading_type', 'futures'),
                'total_analyses': analysis_data.get('total_analyses', 0),
                'futures_analyses': analysis_data.get('futures_analyses', 0),
                'spot_analyses': analysis_data.get('spot_analyses', 0),
                'arbitrage_analyses': analysis_data.get('arbitrage_analyses', 0),
                'pairs_analyzed': analysis_data.get('pairs_analyzed', []),
                'strategies_active': analysis_data.get('strategies_active', []),
                'timestamp': datetime.utcnow()
            }

            # Try MongoDB first
            if self.mongo_manager.is_connected:
                self.mongo_manager.insert_document('market_analyses', document)
                return True
            else:
                # Fallback to JSON
                return self._save_market_analysis_json(date, analysis_data)

        except Exception as e:
            print(f"⚠️ Market analysis logging failed: {e}")
            return False

    def save_strategy_signals(self, date: str, hour: str, strategy_data: Dict[str, Any]) -> bool:
        """Save strategy signals data to MongoDB or JSON fallback"""
        try:
            document = {
                'date': date,
                'hour': hour,
                'trading_type': strategy_data.get('trading_type', 'futures'),
                **{k: v for k, v in strategy_data.items() if k not in ['date', 'hour', 'trading_type']},
                'timestamp': datetime.utcnow()
            }

            # Try MongoDB first
            if self.mongo_manager.is_connected:
                self.mongo_manager.insert_document('strategy_signals', document)
                return True
            else:
                # Fallback to JSON
                return self._save_strategy_signals_json(date, strategy_data)

        except Exception as e:
            print(f"⚠️ Strategy signals logging failed: {e}")
            return False

    def save_hourly_metrics(self, date: str, hour: str, metrics_data: Dict[str, Any]) -> bool:
        """Save hourly trading metrics to MongoDB or JSON fallback"""
        try:
            document = {
                'date': date,
                'hour': hour,
                'trading_type': metrics_data.get('trading_type', 'futures'),
                'signals_generated': metrics_data.get('signals_generated', 0),
                'trades_executed': metrics_data.get('trades_executed', 0),
                'volume_rejections': metrics_data.get('volume_rejections', 0),
                'adx_rejections': metrics_data.get('adx_rejections', 0),
                'other_rejections': metrics_data.get('other_rejections', 0),
                'total_rejections': metrics_data.get('total_rejections', 0),
                'conversion_rate': metrics_data.get('conversion_rate', 0.0),
                'timestamp': datetime.utcnow()
            }

            # Try MongoDB first
            if self.mongo_manager.is_connected:
                self.mongo_manager.insert_document('hourly_metrics', document)
                return True
            else:
                # Fallback to JSON
                return self._save_hourly_metrics_json(date, metrics_data)

        except Exception as e:
            print(f"⚠️ Hourly metrics logging failed: {e}")
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
