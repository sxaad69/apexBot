"""
JSON File Storage Manager
Handles all data operations using JSON files instead of MongoDB
Provides same interface as MongoDB for seamless migration
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import uuid
from config import Config


class JSONManager:
    """
    JSON file-based storage manager
    Drop-in replacement for MongoDB with identical interface
    """

    def __init__(self, config: Config):
        self.config = config
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)

        # Collection names (same as MongoDB)
        self.collections = {
            'futures_trades': 'futures_trades.json',
            'spot_signals': 'spot_signals.json',
            'arbitrage_opportunities': 'arbitrage_opportunities.json',
            'trailing_stops': 'trailing_stops.json',
            'risk_rejections': 'risk_rejections.json',
            'system_logs': 'system_logs.json',
            'debug_logs': 'debug_logs.json'
        }

        # Always connected (local files)
        self.is_connected = True
        print("✅ JSON storage initialized")

    def _get_file_path(self, collection: str) -> Path:
        """Get full file path for a collection"""
        filename = self.collections.get(collection, f"{collection}.json")
        return self.data_dir / filename

    def _load_collection(self, collection: str) -> List[Dict]:
        """Load all documents from a collection file"""
        file_path = self._get_file_path(collection)

        if not file_path.exists():
            return []

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Ensure it's a list
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save_collection(self, collection: str, documents: List[Dict]):
        """Save all documents to a collection file"""
        file_path = self._get_file_path(collection)

        # Create backup before overwriting (optional safety feature)
        if file_path.exists() and getattr(self.config, 'BACKUP_BEFORE_SAVE', False):
            backup_path = file_path.with_suffix('.backup.json')
            file_path.rename(backup_path)

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(documents, f, indent=2, default=str, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Error saving {collection}: {e}")
            # Restore backup if it exists
            if backup_path and backup_path.exists():
                backup_path.rename(file_path)

    def _generate_id(self) -> str:
        """Generate unique ID for documents"""
        return str(uuid.uuid4())

    def _add_timestamps(self, document: Dict) -> Dict:
        """Add timestamps to document"""
        now = datetime.utcnow()

        # Add timestamp if not present
        if 'timestamp' not in document:
            document['timestamp'] = now

        # Add expiration for retention (same as MongoDB TTL)
        if hasattr(self.config, 'MONGODB_RETENTION_DAYS') and self.config.MONGODB_RETENTION_DAYS > 0:
            document['expires_at'] = now + timedelta(days=self.config.MONGODB_RETENTION_DAYS)

        return document

    # ===== PUBLIC INTERFACE (Same as MongoDB) =====

    def insert_document(self, collection: str, document: Dict[str, Any]) -> bool:
        """Insert single document (same interface as MongoDB)"""
        if not self.is_connected:
            return False

        try:
            # Load existing documents
            documents = self._load_collection(collection)

            # Add metadata
            document['_id'] = self._generate_id()
            document = self._add_timestamps(document)

            # Append new document
            documents.append(document)

            # Save back to file
            self._save_collection(collection, documents)

            return True

        except Exception as e:
            print(f"❌ JSON insert error: {e}")
            return False

    def insert_many(self, collection: str, documents: List[Dict[str, Any]]) -> bool:
        """Insert multiple documents"""
        if not self.is_connected:
            return False

        try:
            # Load existing documents
            existing_docs = self._load_collection(collection)

            # Add metadata to new documents
            for doc in documents:
                doc['_id'] = self._generate_id()
                doc = self._add_timestamps(doc)

            # Combine and save
            all_docs = existing_docs + documents
            self._save_collection(collection, all_docs)

            return True

        except Exception as e:
            print(f"❌ JSON bulk insert error: {e}")
            return False

    def find_documents(self, collection: str, query: Dict = None, limit: int = 100) -> List[Dict]:
        """Find documents with optional query (simplified query support)"""
        if not self.is_connected:
            return []

        try:
            documents = self._load_collection(collection)

            if query:
                # Simple query filtering (can be enhanced)
                filtered_docs = []
                for doc in documents:
                    match = True
                    for key, value in query.items():
                        if isinstance(value, dict):
                            # Handle MongoDB-style queries like {"$gte": datetime}
                            for op, op_value in value.items():
                                if op == "$gte" and doc.get(key) < op_value:
                                    match = False
                                    break
                                elif op == "$lte" and doc.get(key) > op_value:
                                    match = False
                                    break
                        elif doc.get(key) != value:
                            match = False
                            break

                    if match:
                        filtered_docs.append(doc)

                documents = filtered_docs

            return documents[:limit] if limit > 0 else documents

        except Exception as e:
            print(f"❌ JSON query error: {e}")
            return []

    def aggregate_data(self, collection: str, pipeline: List[Dict]) -> List[Dict]:
        """Simple aggregation pipeline support"""
        # For now, return raw data (can be enhanced with pandas/numpy for complex aggregations)
        return self.find_documents(collection)

    # ===== DASHBOARD QUERY HELPERS =====

    def get_strategy_performance(self, days: int = 30) -> List[Dict]:
        """Get strategy performance summary using pandas for aggregation"""
        try:
            import pandas as pd

            # Load futures trades
            trades = self.find_documents('futures_trades')

            if not trades:
                return []

            df = pd.DataFrame(trades)

            # Filter by date
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df[df['timestamp'] >= cutoff_date]

            if df.empty:
                return []

            # Group by strategy and aggregate
            result = df.groupby('strategy').agg({
                'pnl_amount': ['sum', 'mean', 'count'],
                'symbol': 'count'
            }).round(2)

            # Flatten column names
            result.columns = ['total_pnl', 'avg_pnl', 'total_trades', 'symbol_count']
            result = result.reset_index()

            # Add win rate
            winning_trades = df[df['pnl_amount'] > 0].groupby('strategy').size()
            result['win_rate'] = (winning_trades / result['total_trades'] * 100).round(1)

            # Sort by total P&L
            result = result.sort_values('total_pnl', ascending=False)

            return result.to_dict('records')

        except ImportError:
            print("⚠️ Pandas not available, returning basic stats")
            return []
        except Exception as e:
            print(f"❌ Strategy performance error: {e}")
            return []

    def get_daily_pnl(self, days: int = 30) -> List[Dict]:
        """Get daily P&L summary"""
        try:
            import pandas as pd

            trades = self.find_documents('futures_trades')
            if not trades:
                return []

            df = pd.DataFrame(trades)
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df['date'] = df['timestamp'].dt.date

            # Filter by date
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            df = df[df['timestamp'] >= cutoff_date]

            if df.empty:
                return []

            # Group by date
            daily = df.groupby('date').agg({
                'pnl_amount': 'sum',
                'timestamp': 'count'
            }).round(2)

            daily.columns = ['total_pnl', 'trade_count']
            daily = daily.reset_index().sort_values('date', ascending=False)

            return daily.to_dict('records')

        except Exception as e:
            print(f"❌ Daily P&L error: {e}")
            return []

    def get_risk_rejection_stats(self, days: int = 7) -> List[Dict]:
        """Get risk layer rejection statistics"""
        try:
            import pandas as pd

            rejections = self.find_documents('risk_rejections')
            if not rejections:
                return []

            df = pd.DataFrame(rejections)
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            # Filter by date
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            df = df[df['timestamp'] >= cutoff_date]

            if df.empty:
                return []

            # Group by layer
            stats = df.groupby('layer_name').agg({
                'timestamp': 'count',
                'timestamp': 'max'
            })

            stats.columns = ['rejection_count', 'latest_rejection']
            stats = stats.reset_index().sort_values('rejection_count', ascending=False)

            return stats.to_dict('records')

        except Exception as e:
            print(f"❌ Risk stats error: {e}")
            return []

    # ===== CLEANUP METHODS =====

    def cleanup_expired_documents(self):
        """Remove expired documents based on retention policy"""
        if not hasattr(self.config, 'MONGODB_RETENTION_DAYS') or self.config.MONGODB_RETENTION_DAYS <= 0:
            return

        cutoff_date = datetime.utcnow()

        for collection in self.collections.keys():
            try:
                documents = self._load_collection(collection)

                # Filter out expired documents
                active_docs = [
                    doc for doc in documents
                    if 'expires_at' not in doc or
                       datetime.fromisoformat(doc['expires_at']) > cutoff_date
                ]

                # Save filtered documents
                if len(active_docs) != len(documents):
                    removed_count = len(documents) - len(active_docs)
                    self._save_collection(collection, active_docs)
                    print(f"🧹 Cleaned up {removed_count} expired documents from {collection}")

            except Exception as e:
                print(f"❌ Cleanup error for {collection}: {e}")

    def clean_collection(self, collection: str):
        """Clean all data from a specific collection"""
        file_path = self._get_file_path(collection)
        if file_path.exists():
            file_path.unlink()
            print(f"🗑️ Cleaned {collection}")

    def clean_all_collections(self):
        """Clean all JSON database files"""
        for collection in self.collections.keys():
            self.clean_collection(collection)
        print("🗑️ All database collections cleaned")

    def close(self):
        """Close (no-op for JSON files)"""
        self.is_connected = False
        print("🔌 JSON storage closed")

    def __del__(self):
        """Destructor"""
        self.close()
