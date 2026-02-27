"""
SQLite Database Manager
Handles core trading data persistence with transactional integrity.
Implements the trade lifecycle pattern (Entry -> Update -> Exit).
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any

class SQLiteManager:
    def __init__(self, config):
        self.config = config
        self.data_dir = Path(getattr(config, 'DATA_DIRECTORY', './data'))
        self.main_db = self.data_dir / 'apex_hunter.db'
        self.log_db = self.data_dir / 'activity_log.db'
        self._init_db()

    def _get_connection(self, db_path: Path):
        """Get a thread-safe connection to a specific database"""
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initialize both database schemas"""
        # --- MAIN DB (High Value) ---
        conn = self._get_connection(self.main_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                trade_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL,
                strategy TEXT,
                side TEXT,
                leverage INTEGER,
                entry_price REAL,
                entry_time TEXT,
                exit_price REAL,
                exit_time TEXT,
                pnl_amount REAL,
                pnl_percent REAL,
                status TEXT DEFAULT 'OPEN',
                stop_loss REAL,
                take_profit REAL,
                metadata TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                hour TEXT,
                type TEXT,
                signals_generated INTEGER DEFAULT 0,
                trades_executed INTEGER DEFAULT 0,
                total_rejections INTEGER DEFAULT 0,
                UNIQUE(date, hour, type)
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS active_positions (
                symbol TEXT PRIMARY KEY,
                entry_price REAL,
                current_price REAL,
                side TEXT,
                size REAL,
                leverage INTEGER,
                strategy TEXT,
                pnl_percent REAL,
                last_updated TEXT
            )
        ''')
        conn.commit()
        conn.close()

        # --- LOG DB (High Volume) ---
        conn = self._get_connection(self.log_db)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                type TEXT,
                symbol TEXT,
                message TEXT,
                metadata TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                price REAL,
                indicators TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS strategy_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                strategy TEXT,
                action TEXT,
                confidence REAL,
                data TEXT
            )
        ''')
        # Indexes for fast querying
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_ts ON market_analysis(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_ts ON strategy_signals(timestamp)')
        
        conn.commit()
        conn.close()

    def open_trade(self, trade_data: Dict[str, Any]) -> bool:
        """Create a new 'OPEN' trade entry in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO trades (
                    trade_id, symbol, market_type, strategy, side, leverage, 
                    entry_price, entry_time, stop_loss, take_profit, status, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
            ''', (
                trade_data.get('trade_id'),
                trade_data.get('symbol'),
                trade_data.get('market_type', 'futures'),
                trade_data.get('strategy'),
                trade_data.get('side'),
                trade_data.get('leverage', 1),
                trade_data.get('entry_price'),
                trade_data.get('timestamp', datetime.utcnow().isoformat()),
                trade_data.get('stop_loss'),
                trade_data.get('take_profit'),
                json.dumps(trade_data.get('metadata', {}))
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ SQLite open_trade error: {e}")
            return False

    def close_trade(self, trade_id: str, exit_data: Dict[str, Any]) -> bool:
        """Update exist trade in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trades 
                SET exit_price = ?, exit_time = ?, pnl_amount = ?, pnl_percent = ?, status = 'CLOSED', metadata = ?
                WHERE trade_id = ?
            ''', (
                exit_data.get('exit_price'),
                exit_data.get('timestamp', datetime.utcnow().isoformat()),
                exit_data.get('pnl_amount'),
                exit_data.get('pnl_percent'),
                json.dumps(exit_data.get('metadata', {})),
                trade_id
            ))
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
        except Exception as e:
            print(f"❌ SQLite close_trade error: {e}")
            return False

    def save_analysis(self, symbol: str, price: float, indicators: Dict):
        """Log high-frequency market analysis to log DB"""
        try:
            conn = self._get_connection(self.log_db)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO market_analysis (symbol, price, indicators) VALUES (?, ?, ?)',
                         (symbol, price, json.dumps(indicators, default=str)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ SQLite save_analysis error: {e}")

    def save_signal(self, symbol: str, strategy: str, action: str, confidence: float, data: Dict):
        """Log high-frequency strategy signals to log DB"""
        try:
            conn = self._get_connection(self.log_db)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO strategy_signals (symbol, strategy, action, confidence, data) 
                VALUES (?, ?, ?, ?, ?)
            ''', (symbol, strategy, action, confidence, json.dumps(data, default=str)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ SQLite save_signal error: {e}")

    def save_active_positions_snapshot(self, positions_data: List[Dict[str, Any]]):
        """Save a snapshot of all active positions to main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            # Clear old snapshot
            cursor.execute('DELETE FROM active_positions')
            # Insert new snapshot
            for pos in positions_data:
                cursor.execute('''
                    INSERT INTO active_positions (
                        symbol, entry_price, current_price, side, size, 
                        unrealized_pnl, pnl_percent, timestamp, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    pos.get('symbol'),
                    pos.get('entry_price'),
                    pos.get('current_price'),
                    pos.get('side'),
                    pos.get('size'),
                    pos.get('unrealized_pnl'),
                    pos.get('pnl_percent'),
                    pos.get('timestamp', datetime.utcnow().isoformat()),
                    json.dumps(pos.get('metadata', {}), default=str)
                ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ SQLite save_active_positions_snapshot error: {e}")
            return False

    def log_activity(self, log_type: str, symbol: str, message: str, metadata: Dict = None):
        """Log general activity to log DB"""
        try:
            conn = self._get_connection(self.log_db)
            cursor = conn.cursor()
            cursor.execute('INSERT INTO activity_log (type, symbol, message, metadata) VALUES (?, ?, ?, ?)',
                         (log_type, symbol, message, json.dumps(metadata or {}, default=str)))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"❌ SQLite log_activity error: {e}")

    def upsert_metrics(self, date: str, hour: str, m_type: str, inc_data: Dict[str, int]):
        """Increment hourly metrics in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('INSERT OR IGNORE INTO metrics (date, hour, type) VALUES (?, ?, ?)', (date, hour, m_type))
            for key, val in inc_data.items():
                if key in ['signals_generated', 'trades_executed', 'total_rejections']:
                    cursor.execute(f'UPDATE metrics SET {key} = {key} + ? WHERE date = ? AND hour = ? AND type = ?',
                                 (val, date, hour, m_type))
            conn.commit()
            conn.close()
        except: pass

    def purge_old_activity(self, days: int = 7):
        """Purge old logs to prevent disk bloat"""
        try:
            cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
            conn = self._get_connection(self.log_db)
            cursor = conn.cursor()
            for table in ['activity_log', 'market_analysis', 'strategy_signals']:
                cursor.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
            count = cursor.rowcount
            conn.commit()
            cursor.execute("VACUUM")
            conn.close()
            return count
        except Exception as e:
            print(f"❌ SQLite purge error: {e}")
            return 0
    def save_active_positions_snapshot(self, positions: Dict[str, Any], current_prices: Dict[str, float] = None):
        """Save/Update active positions in SQLite"""
        conn = self._get_connection(self.main_db)
        cursor = conn.cursor()
        try:
            # Clear existing mapped positions or use REPLACE INTO
            # We'll use a transaction for reliability
            cursor.execute("BEGIN TRANSACTION")
            
            # 1. Clear dead positions (those not in the current snapshot)
            if positions:
                placeholders = ','.join(['?'] * len(positions))
                cursor.execute(f"DELETE FROM active_positions WHERE symbol NOT IN ({placeholders})", list(positions.keys()))
            else:
                cursor.execute("DELETE FROM active_positions")

            # 2. Update/Insert current positions
            for symbol, pos in positions.items():
                curr_price = current_prices.get(symbol, pos.get('entry_price', 0)) if current_prices else pos.get('entry_price', 0)
                
                # Calculate P&L if possible
                pnl_pct = 0
                if pos.get('entry_price', 0) > 0:
                    side_mult = 1 if pos.get('side', 'buy').lower() == 'buy' else -1
                    pnl_pct = ((curr_price / pos['entry_price']) - 1) * 100 * side_mult * pos.get('leverage', 1)

                cursor.execute('''
                    REPLACE INTO active_positions 
                    (symbol, entry_price, current_price, side, size, leverage, strategy, pnl_percent, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    symbol, 
                    pos.get('entry_price'), 
                    curr_price,
                    pos.get('side', 'buy'),
                    pos.get('size', 0),
                    pos.get('leverage', 1),
                    pos.get('strategy', 'unknown'),
                    pnl_pct,
                    datetime.utcnow().isoformat()
                ))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error saving active positions to SQLite: {e}")
        finally:
            conn.close()
