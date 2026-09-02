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
        """Get a thread-safe connection to a specific database.
        
        check_same_thread=False: allows the Sentinel thread to write safely.
        busy_timeout=5000: retry for up to 5s on lock contention (WAL handles concurrency).
        """
        conn = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except:
            pass
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
                size REAL,
                entry_price REAL,
                entry_time TEXT,
                exit_price REAL,
                exit_time TEXT,
                pnl_amount REAL,
                pnl_percent REAL,
                status TEXT DEFAULT 'OPEN',
                stop_loss REAL,
                take_profit REAL,
                highest_price REAL,
                lowest_price REAL,
                trailing_stop_price REAL,
                trailing_stop_active INTEGER DEFAULT 0,
                capital_at_entry REAL,
                capital_at_exit REAL,
                reason TEXT,
                metadata TEXT,
                exchange_order_id TEXT,
                sl_order_id TEXT,
                tp_order_id TEXT,
                confidence REAL,
                stop_loss_roe REAL
            )
        ''')
        
        # --- MIGRATIONS ---
        try:
            # Check migration for new financial columns
            cursor.execute("PRAGMA table_info(trades)")
            columns = [col[1] for col in cursor.fetchall()]
            
            if 'reason' not in columns:
                print("🔧 Migrating database: Adding 'reason' column back...")
                cursor.execute("ALTER TABLE trades ADD COLUMN reason TEXT")

            if 'size' not in columns:
                print("🔧 Migrating database: Adding 'size' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN size REAL")


            if 'capital_at_entry' not in columns:
                print("🔧 Migrating database: Adding 'capital_at_entry' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN capital_at_entry REAL")
                
            if 'capital_at_exit' not in columns:
                print("🔧 Migrating database: Adding 'capital_at_exit' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN capital_at_exit REAL")
            
            if 'lowest_price' not in columns:
                print("🔧 Migrating database: Adding 'lowest_price' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN lowest_price REAL")
            
            if 'trailing_stop_active' not in columns:
                print("🔧 Migrating database: Adding 'trailing_stop_active' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN trailing_stop_active INTEGER DEFAULT 0")
                
            if 'exchange_order_id' not in columns:
                print("🔧 Migrating database: Adding 'exchange_order_id' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN exchange_order_id TEXT")
            
            if 'sl_order_id' not in columns:
                print("🔧 Migrating database: Adding 'sl_order_id' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN sl_order_id TEXT")
                
            if 'tp_order_id' not in columns:
                print("🔧 Migrating database: Adding 'tp_order_id' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN tp_order_id TEXT")
            
            if 'confidence' not in columns:
                print("🔧 Migrating database: Adding 'confidence' column...")
                cursor.execute("ALTER TABLE trades ADD COLUMN confidence REAL")
            
            conn.commit()
        except Exception as e:
            print(f"⚠️  Database trades migration warning: {e}")

        # --- SETTINGS TABLE (Persistent State) ---
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    last_updated TEXT
                )
            ''')
            conn.commit()
        except Exception as e:
            print(f"⚠️  Database settings table creation error: {e}")

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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS circuit_breaker_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                triggered_at TEXT NOT NULL,
                net_roe_pct REAL,
                positions_closed INTEGER,
                cooldown_minutes INTEGER,
                cooldown_until TEXT
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio_ratchets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                activation_roe REAL,
                peak_roe REAL,
                exit_roe REAL,
                total_pnl REAL,
                positions_closed INTEGER,
                metadata TEXT
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
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS rejections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                symbol TEXT,
                strategy TEXT,
                side TEXT,
                entry_price REAL,
                reason TEXT,
                layer TEXT,
                confidence REAL,
                metadata TEXT
            )
        ''')
        # Indexes for fast querying
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_activity_ts ON activity_log(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_analysis_ts ON market_analysis(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_signals_ts ON strategy_signals(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_rejections_ts ON rejections(timestamp)')

        # --- ENRICHMENT SCHEMA ---
        # trade_outcomes: MFE/MAE per closed trade, written once at exit. Derived from
        # highest_price/lowest_price tracked at WSS (0.5s) resolution. Joins back to
        # strategy_evaluations via (entry_timestamp, symbol, strategy).
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trade_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entry_timestamp TEXT,
                symbol TEXT,
                strategy TEXT,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                exit_timestamp TEXT,
                exit_reason TEXT,
                pnl_pct REAL,
                max_favorable_excursion REAL,
                max_adverse_excursion REAL,
                time_to_max_favorable_min REAL
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_outcome_join ON trade_outcomes(entry_timestamp, symbol, strategy)')

        # paper_signals: every signal generated by a paper-mode strategy.
        # Resolved later by comparing mark price against SL/TP.
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS paper_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
                strategy TEXT,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                stop_loss REAL,
                take_profit REAL,
                confidence REAL,
                indicators TEXT,
                outcome TEXT,
                exit_price REAL,
                pnl_pct REAL,
                resolved_at TEXT
            )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_ts ON paper_signals(timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_paper_outcome ON paper_signals(outcome)')

        conn.commit()
        conn.close()

    def log_rejection(self, data: Dict[str, Any]) -> bool:
        """Log a rejected trade signal to the rejections table in log_db"""
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO rejections (
                        symbol, strategy, side, entry_price, reason, layer, confidence, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data.get('symbol'),
                    data.get('strategy'),
                    data.get('side'),
                    data.get('entry_price'),
                    data.get('reason'),
                    data.get('layer'),
                    data.get('confidence', 0.0),
                    json.dumps(data.get('metadata', {}), default=str)
                ))
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite log_rejection error: {e}")
            return False

    def log_trade_outcome(self, data: Dict[str, Any]) -> bool:
        """Log MFE/MAE outcome for a closed trade (trade_outcomes table)."""
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO trade_outcomes (
                        entry_timestamp, symbol, strategy, side, entry_price,
                        exit_price, exit_timestamp, exit_reason, pnl_pct,
                        max_favorable_excursion, max_adverse_excursion,
                        time_to_max_favorable_min
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ''', (
                    data.get('entry_timestamp'),
                    data.get('symbol'),
                    data.get('strategy'),
                    data.get('side'),
                    data.get('entry_price'),
                    data.get('exit_price'),
                    data.get('exit_timestamp'),
                    data.get('exit_reason'),
                    data.get('pnl_pct'),
                    data.get('max_favorable_excursion'),
                    data.get('max_adverse_excursion'),
                    data.get('time_to_max_favorable_min'),
                ))
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite log_trade_outcome error: {e}")
            return False

    def log_paper_signal(self, data: Dict[str, Any]) -> bool:
        """Log a paper-mode strategy signal (no real trade placed)."""
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO paper_signals (
                        strategy, symbol, side, entry_price, stop_loss,
                        take_profit, confidence, indicators
                    ) VALUES (?,?,?,?,?,?,?,?)
                ''', (
                    data.get('strategy'),
                    data.get('symbol'),
                    data.get('side'),
                    data.get('entry_price'),
                    data.get('stop_loss'),
                    data.get('take_profit'),
                    data.get('confidence'),
                    json.dumps(data.get('indicators'), default=str) if data.get('indicators') else None,
                ))
                conn.commit()
                return True
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite log_paper_signal error: {e}")
            return False

    def resolve_paper_signals(self, mark_prices: Dict[str, float]) -> int:
        """Resolve open paper signals by comparing current mark price against SL/TP.

        mark_prices: {symbol: current_mark_price}
        Returns count of newly resolved signals.
        """
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                open_signals = cursor.execute(
                    "SELECT id, symbol, side, entry_price, stop_loss, take_profit FROM paper_signals WHERE outcome IS NULL"
                ).fetchall()
                resolved = 0
                for row in open_signals:
                    sig_id, symbol, side, entry_price, stop_loss, take_profit = row
                    mark = mark_prices.get(symbol)
                    if mark is None or entry_price is None:
                        continue
                    outcome = None
                    exit_price = None
                    pnl_pct = None
                    if side and side.lower() == 'buy':
                        if stop_loss and mark <= stop_loss:
                            outcome = 'loss'
                            exit_price = stop_loss
                        elif take_profit and mark >= take_profit:
                            outcome = 'win'
                            exit_price = take_profit
                    else:
                        if stop_loss and mark >= stop_loss:
                            outcome = 'loss'
                            exit_price = stop_loss
                        elif take_profit and mark <= take_profit:
                            outcome = 'win'
                            exit_price = take_profit
                    if outcome and exit_price:
                        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
                        if side and side.lower() == 'sell':
                            pnl_pct = -pnl_pct
                        cursor.execute(
                            "UPDATE paper_signals SET outcome=?, exit_price=?, pnl_pct=?, resolved_at=datetime('now') WHERE id=?",
                            (outcome, exit_price, pnl_pct, sig_id)
                        )
                        resolved += 1
                conn.commit()
                return resolved
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite resolve_paper_signals error: {e}")
            return 0

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting from the persistent settings table"""
        try:
            conn = self._get_connection(self.main_db)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                if row:
                    return row['value']
                return default
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite get_setting error: {e}")
            return default

    def set_setting(self, key: str, value: Any) -> bool:
        """Save a setting to the persistent settings table"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('''
                REPLACE INTO settings (key, value, last_updated)
                VALUES (?, ?, ?)
            ''', (key, str(value), datetime.utcnow().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ SQLite set_setting error: {e}")
            return False

    def get_trades(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch trades from the database, optionally filtered by status"""
        try:
            conn = self._get_connection(self.main_db)
            try:
                cursor = conn.cursor()
                query = "SELECT * FROM trades"
                params = []
                if status:
                    query += " WHERE status = ?"
                    params.append(status)
                cursor.execute(query, params)
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite get_trades error: {e}")
            return []

    def record_trade(self, trade_data: Dict[str, Any]) -> bool:
        """Create a new 'OPEN' trade entry in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            
            # Key alignment between TradeManager and SQLiteManager
            entry_time = trade_data.get('entry_time') or trade_data.get('timestamp') or datetime.utcnow().isoformat()
            metadata = trade_data.get('metadata', '{}')
            if isinstance(metadata, dict):
                metadata = json.dumps(metadata)

            cursor.execute('''
                INSERT INTO trades (
                    trade_id, symbol, market_type, strategy, side, leverage, size,
                    entry_price, entry_time, stop_loss, take_profit, 
                    highest_price, lowest_price,
                    capital_at_entry, status, metadata, exchange_order_id, confidence, stop_loss_roe
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?)
            ''', (
                trade_data.get('trade_id'),
                trade_data.get('symbol'),
                trade_data.get('market_type', 'futures'),
                trade_data.get('strategy'),
                trade_data.get('side'),
                trade_data.get('leverage', 1),
                trade_data.get('size', 0.0),
                trade_data.get('entry_price'),
                entry_time,
                trade_data.get('stop_loss'),
                trade_data.get('take_profit'),
                trade_data.get('highest_price') or trade_data.get('entry_price'),
                trade_data.get('lowest_price') or trade_data.get('entry_price'),
                trade_data.get('capital_at_entry', 0.0),
                metadata,
                trade_data.get('exchange_order_id'),
                trade_data.get('confidence', 0.0),
                trade_data.get('stop_loss_roe', 5.0)
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ SQLite record_trade error: {e}")
            return False

    def update_trade_order_ids(self, trade_id: str, sl_order_id=None, tp_order_id=None) -> bool:
        """Update the exchange order IDs (SL/TP) for an open trade.

        Order IDs may be passed as None to clear them (e.g. after an exit or
        when an order is cancelled). Used by the exchange-side SL/TP feature
        (Phase 15) to persist order ids for restart recovery.
        """
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE trades SET sl_order_id = ?, tp_order_id = ? WHERE trade_id = ?",
                (sl_order_id, tp_order_id, trade_id)
            )
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
        except Exception as e:
            print(f"❌ SQLite update_trade_order_ids error: {e}")
            return False

    def update_trade_metadata(self, trade_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing trade with live parameters like SL/TP and metadata."""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            
            # Dynamically build the UPDATE query based on the updates dict mapping
            set_clauses = []
            values = []
            
            for key, val in updates.items():
                if key == 'metadata':
                    set_clauses.append("metadata = ?")
                    values.append(json.dumps(val))
                elif key in ['take_profit', 'stop_loss', 'highest_price', 'lowest_price', 'trailing_stop_price', 'exchange_order_id', 'size']:
                    set_clauses.append(f"{key} = ?")
                    values.append(val)
                    
            if not set_clauses:
                return False
                
            query = f"UPDATE trades SET {', '.join(set_clauses)} WHERE trade_id = ?"
            values.append(trade_id)
            
            cursor.execute(query, tuple(values))
            success = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return success
        except Exception as e:
            print(f"❌ SQLite update_trade_metadata error: {e}")
            return False

    def close_trade(self, trade_id: str, exit_data: Dict[str, Any]) -> bool:
        """Update exist trade in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE trades 
                SET exit_price = ?, exit_time = ?, pnl_amount = ?, pnl_percent = ?, 
                    reason = ?, capital_at_exit = ?, status = 'CLOSED', metadata = ?
                WHERE trade_id = ?
            ''', (
                exit_data.get('exit_price'),
                exit_data.get('timestamp', datetime.utcnow().isoformat()),
                exit_data.get('pnl_amount'),
                exit_data.get('pnl_percent'),
                exit_data.get('reason', 'manual'),
                exit_data.get('capital_at_exit'),
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
            try:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO market_analysis (symbol, price, indicators) VALUES (?, ?, ?)',
                             (symbol, price, json.dumps(indicators, default=str)))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite save_analysis error: {e}")

    def save_analysis_bulk(self, records: List[Dict[str, Any]]):
        """Log sweep-level market analysis summaries to log DB in bulk.

        NOTE: records are analysis_data dicts produced by _collect_market_analysis_data().
        They use 'trading_type' (not 'symbol'), 'current_price' (not 'price'),
        and the entire dict is the indicator payload — there is no separate 'indicators' key.
        """
        if not records:
            return
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                data_tuples = []
                for rec in records:
                    # Resolve symbol: prefer explicit 'symbol', fall back to trading_type
                    symbol = rec.get('symbol') or rec.get('trading_type', 'futures')
                    # Resolve price: prefer explicit 'price', fall back to current_price
                    price = rec.get('price') or rec.get('current_price', 0.0) or 0.0
                    # Store the full dict as the indicators blob (exclude non-serialisable keys)
                    indicators = {k: v for k, v in rec.items() if k != 'timestamp'}
                    data_tuples.append((
                        symbol,
                        price,
                        json.dumps(indicators, default=str)
                    ))
                cursor.executemany('INSERT INTO market_analysis (symbol, price, indicators) VALUES (?, ?, ?)', data_tuples)
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite save_analysis_bulk error: {e}")



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
            try:
                cursor = conn.cursor()
                cursor.execute('INSERT INTO activity_log (type, symbol, message, metadata) VALUES (?, ?, ?, ?)',
                             (log_type, symbol, message, json.dumps(metadata or {}, default=str)))
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"❌ SQLite log_activity error: {e}")

    def upsert_metrics(self, date: str, hour: str, m_type: str, inc_data: Dict[str, int]):
        """Increment hourly metrics in main DB"""
        try:
            conn = self._get_connection(self.main_db)
            try:
                cursor = conn.cursor()
                cursor.execute('INSERT OR IGNORE INTO metrics (date, hour, type) VALUES (?, ?, ?)', (date, hour, m_type))
                for key, val in inc_data.items():
                    if key in ['signals_generated', 'trades_executed', 'total_rejections']:
                        cursor.execute(f'UPDATE metrics SET {key} = {key} + ? WHERE date = ? AND hour = ? AND type = ?',
                                     (val, date, hour, m_type))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def purge_old_activity(self, days: int = 7):
        """Purge old logs to prevent disk bloat.
        
        market_analysis is high-volume sweep data — kept for only 1 day.
        Other tables (activity_log, strategy_signals) honour the full 'days' window.
        """
        try:
            conn = self._get_connection(self.log_db)
            try:
                cursor = conn.cursor()
                # Tight window for high-volume sweep data
                analysis_cutoff = (datetime.utcnow() - timedelta(days=1)).isoformat()
                cursor.execute("DELETE FROM market_analysis WHERE timestamp < ?", (analysis_cutoff,))
                analysis_deleted = cursor.rowcount

                # Normal window for other tables
                cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
                for table in ['activity_log', 'strategy_signals']:
                    cursor.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))

                conn.commit()
                cursor.execute("VACUUM")
                return analysis_deleted
            finally:
                conn.close()
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
    def record_portfolio_ratchet(self, data: Dict[str, Any]) -> bool:
        """Log a portfolio ratchet/liquidation event"""
        try:
            conn = self._get_connection(self.main_db)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO portfolio_ratchets (
                    timestamp, activation_roe, peak_roe, exit_roe, total_pnl, positions_closed, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                datetime.utcnow().isoformat(),
                data.get('activation_roe'),
                data.get('peak_roe'),
                data.get('exit_roe'),
                data.get('total_pnl'),
                data.get('positions_closed'),
                json.dumps(data.get('metadata', {}))
            ))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"❌ SQLite record_portfolio_ratchet error: {e}")
            return False
