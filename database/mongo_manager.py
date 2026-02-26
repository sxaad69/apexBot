"""
MongoDB Database Manager
Handles all MongoDB operations for APEX HUNTER V14
"""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError
import motor.motor_asyncio
from config import Config


class MongoManager:
    """
    MongoDB connection and operations manager
    Supports both sync and async operations
    """

    def __init__(self, config: Config):
        self.config = config
        self.client: Optional[MongoClient] = None
        self.async_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
        self.database = None
        self.async_database = None
        self.is_connected = False

        # Consolidated collection names
        self.collections = {
            'futures_trades': 'futures_trades', # High value, permanent
            'spot_signals': 'spot_signals',   # High value, permanent
            'activity_log': 'activity_log',   # Consolidated (api, risk, alerts), 7-day TTL
            'metrics_summary': 'metrics_summary', # Hourly/Daily totals, permanent
            'system_logs': 'system_logs',     # System state, 30-day TTL
            'debug_logs': 'debug_logs'        # Verbose, 1-day TTL
        }

        # Initialize connection
        self._connect()

    def _connect(self) -> bool:
        """Establish MongoDB connection"""
        try:
            # MongoDB Atlas connection string
            connection_string = self._build_connection_string()

            # Sync client (Increased timeouts for slow local DNS resolution)
            self.client = MongoClient(
                connection_string,
                serverSelectionTimeoutMS=30000,
                connectTimeoutMS=30000,
                socketTimeoutMS=30000,
                maxPoolSize=10,
                minPoolSize=2
            )

            # Test connection
            self.client.admin.command('ping')
            self.database = self.client[self.config.MONGODB_DATABASE]

            # Async client
            self.async_client = motor.motor_asyncio.AsyncIOMotorClient(connection_string)
            self.async_database = self.async_client[self.config.MONGODB_DATABASE]

            self.is_connected = True
            print(f"✅ MongoDB connected to database: {self.config.MONGODB_DATABASE}")

            # Create indexes
            self._create_indexes()

            return True

        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            print(f"❌ MongoDB connection failed: {e}")
            self.is_connected = False
            return False
        except Exception as e:
            print(f"❌ MongoDB error: {e}")
            self.is_connected = False
            return False

    def _build_connection_string(self) -> str:
        """Build MongoDB connection string"""
        host = self.config.MONGODB_HOST
        port = self.config.MONGODB_PORT
        database = self.config.MONGODB_DATABASE

        # For MongoDB Atlas, use full connection string
        if 'mongodb+srv://' in host or 'mongodb://' in host:
            return host

        # For local MongoDB
        if self.config.MONGODB_USERNAME and self.config.MONGODB_PASSWORD:
            return f"mongodb://{self.config.MONGODB_USERNAME}:{self.config.MONGODB_PASSWORD}@{host}:{port}/{database}"
        else:
            return f"mongodb://{host}:{port}/{database}"

    def _create_indexes(self):
        """Create database indexes for optimal query performance"""
        try:
            # Futures trades indexes
            self.database[self.collections['futures_trades']].create_index([
                ('timestamp', DESCENDING),
                ('strategy', ASCENDING),
                ('symbol', ASCENDING)
            ])
            self.database[self.collections['futures_trades']].create_index([
                ('strategy', ASCENDING),
                ('pnl_amount', DESCENDING)
            ])

            # Spot signals indexes
            self.database[self.collections['spot_signals']].create_index([
                ('timestamp', DESCENDING),
                ('strategy', ASCENDING),
                ('symbol', ASCENDING)
            ])

            # Arbitrage opportunities indexes
            self.database[self.collections['arbitrage_opportunities']].create_index([
                ('timestamp', DESCENDING),
                ('type', ASCENDING),
                ('profit_percent', DESCENDING)
            ])

            # Trailing stops indexes
            self.database[self.collections['trailing_stops']].create_index([
                ('timestamp', DESCENDING),
                ('strategy', ASCENDING),
                ('symbol', ASCENDING)
            ])

            # Risk rejections indexes
            self.database[self.collections['risk_rejections']].create_index([
                ('timestamp', DESCENDING),
                ('layer_name', ASCENDING),
                ('symbol', ASCENDING)
            ])

            # System logs indexes
            self.database[self.collections['system_logs']].create_index([
                ('timestamp', DESCENDING),
                ('level', ASCENDING)
            ])
            self.database[self.collections['system_logs']].create_index("expires_at", expireAfterSeconds=0)

            # Activity log indexes (Consolidated)
            self.database[self.collections['activity_log']].create_index([
                ('timestamp', DESCENDING),
                ('type', ASCENDING),
                ('symbol', ASCENDING)
            ])
            self.database[self.collections['activity_log']].create_index("expires_at", expireAfterSeconds=0)

            # Debug logs (High volume, short life)
            self.database[self.collections['debug_logs']].create_index("timestamp", expireAfterSeconds=86400) # 1 day

            # Metrics Summary (Upsert collection)
            self.database[self.collections['metrics_summary']].create_index([
                ('date', ASCENDING),
                ('hour', ASCENDING),
                ('type', ASCENDING)
            ], unique=True)

            print("✅ Database indexes created successfully")

        except Exception as e:
            print(f"⚠️ Index creation failed: {e}")

    def upsert_document(self, collection: str, filter_query: Dict, update_data: Dict) -> bool:
        """Upsert document (increment or set)"""
        if not self.is_connected:
            return False
        try:
            # Ensure timestamp is updated
            if '$set' not in update_data:
                update_data['$set'] = {}
            update_data['$set']['last_updated'] = datetime.utcnow()
            
            result = self.database[collection].update_one(
                filter_query,
                update_data,
                upsert=True
            )
            return result.acknowledged
        except Exception as e:
            print(f"❌ MongoDB upsert error: {e}")
            return False

    # ===== SYNC OPERATIONS =====

    def insert_document(self, collection: str, document: Dict[str, Any]) -> bool:
        """Insert single document"""
        if not self.is_connected:
            return False

        try:
            # Add timestamp if not present
            if 'timestamp' not in document:
                document['timestamp'] = datetime.utcnow()

            # Add TTL if retention is configured
            if hasattr(self.config, 'MONGODB_RETENTION_DAYS') and self.config.MONGODB_RETENTION_DAYS > 0:
                document['expires_at'] = datetime.utcnow() + timedelta(days=self.config.MONGODB_RETENTION_DAYS)

            result = self.database[collection].insert_one(document)
            return result.acknowledged

        except Exception as e:
            print(f"❌ MongoDB insert error: {e}")
            return False

    def insert_many(self, collection: str, documents: List[Dict[str, Any]]) -> bool:
        """Insert multiple documents"""
        if not self.is_connected:
            return False

        try:
            # Add timestamps and TTL
            for doc in documents:
                if 'timestamp' not in doc:
                    doc['timestamp'] = datetime.utcnow()
                if hasattr(self.config, 'MONGODB_RETENTION_DAYS') and self.config.MONGODB_RETENTION_DAYS > 0:
                    doc['expires_at'] = datetime.utcnow() + timedelta(days=self.config.MONGODB_RETENTION_DAYS)

            result = self.database[collection].insert_many(documents)
            return result.acknowledged

        except Exception as e:
            print(f"❌ MongoDB bulk insert error: {e}")
            return False

    def find_documents(self, collection: str, query: Dict = None, limit: int = 100) -> List[Dict]:
        """Find documents with optional query"""
        if not self.is_connected:
            return []

        try:
            cursor = self.database[collection].find(query or {}).limit(limit)
            return list(cursor)

        except Exception as e:
            print(f"❌ MongoDB query error: {e}")
            return []

    def aggregate_data(self, collection: str, pipeline: List[Dict]) -> List[Dict]:
        """Run aggregation pipeline"""
        if not self.is_connected:
            return []

        try:
            result = list(self.database[collection].aggregate(pipeline))
            return result

        except Exception as e:
            print(f"❌ MongoDB aggregation error: {e}")
            return []

    # ===== DASHBOARD QUERY HELPERS =====

    def get_strategy_performance(self, days: int = 30) -> List[Dict]:
        """Get strategy performance summary"""
        pipeline = [
            {
                '$match': {
                    'timestamp': {
                        '$gte': datetime.utcnow() - timedelta(days=days)
                    }
                }
            },
            {
                '$group': {
                    '_id': '$strategy',
                    'total_trades': {'$sum': 1},
                    'winning_trades': {
                        '$sum': {'$cond': [{'$gt': ['$pnl_amount', 0]}, 1, 0]}
                    },
                    'total_pnl': {'$sum': '$pnl_amount'},
                    'avg_pnl': {'$avg': '$pnl_amount'},
                    'max_pnl': {'$max': '$pnl_amount'},
                    'min_pnl': {'$min': '$pnl_amount'}
                }
            },
            {
                '$project': {
                    'strategy': '$_id',
                    'total_trades': 1,
                    'win_rate': {
                        '$multiply': [
                            {'$divide': ['$winning_trades', '$total_trades']},
                            100
                        ]
                    },
                    'total_pnl': {'$round': ['$total_pnl', 2]},
                    'avg_pnl': {'$round': ['$avg_pnl', 2]},
                    'max_pnl': {'$round': ['$max_pnl', 2]},
                    'min_pnl': {'$round': ['$min_pnl', 2]}
                }
            },
            {'$sort': {'total_pnl': -1}}
        ]

        return self.aggregate_data(self.collections['futures_trades'], pipeline)

    def get_daily_pnl(self, days: int = 30) -> List[Dict]:
        """Get daily P&L summary"""
        pipeline = [
            {
                '$match': {
                    'timestamp': {
                        '$gte': datetime.utcnow() - timedelta(days=days)
                    }
                }
            },
            {
                '$group': {
                    '_id': {
                        '$dateToString': {
                            'format': '%Y-%m-%d',
                            'date': '$timestamp'
                        }
                    },
                    'total_pnl': {'$sum': '$pnl_amount'},
                    'trade_count': {'$sum': 1}
                }
            },
            {
                '$project': {
                    'date': '$_id',
                    'total_pnl': {'$round': ['$total_pnl', 2]},
                    'trade_count': 1
                }
            },
            {'$sort': {'date': -1}}
        ]

        return self.aggregate_data(self.collections['futures_trades'], pipeline)

    def get_risk_rejection_stats(self, days: int = 7) -> List[Dict]:
        """Get risk layer rejection statistics"""
        pipeline = [
            {
                '$match': {
                    'timestamp': {
                        '$gte': datetime.utcnow() - timedelta(days=days)
                    }
                }
            },
            {
                '$group': {
                    '_id': '$layer_name',
                    'rejection_count': {'$sum': 1},
                    'latest_rejection': {'$max': '$timestamp'}
                }
            },
            {
                '$project': {
                    'layer_name': '$_id',
                    'rejection_count': 1,
                    'latest_rejection': 1
                }
            },
            {'$sort': {'rejection_count': -1}}
        ]

        return self.aggregate_data(self.collections['risk_rejections'], pipeline)

    # ===== ASYNC OPERATIONS =====

    async def insert_document_async(self, collection: str, document: Dict[str, Any]) -> bool:
        """Async insert single document"""
        if not self.is_connected:
            return False

        try:
            # Add timestamp if not present
            if 'timestamp' not in document:
                document['timestamp'] = datetime.utcnow()

            # Add TTL if retention is configured
            if hasattr(self.config, 'MONGODB_RETENTION_DAYS') and self.config.MONGODB_RETENTION_DAYS > 0:
                document['expires_at'] = datetime.utcnow() + timedelta(days=self.config.MONGODB_RETENTION_DAYS)

            result = await self.async_database[collection].insert_one(document)
            return result.acknowledged

        except Exception as e:
            print(f"❌ MongoDB async insert error: {e}")
            return False

    async def insert_many_async(self, collection: str, documents: List[Dict[str, Any]]) -> bool:
        """Async insert multiple documents"""
        if not self.is_connected:
            return False

        try:
            # Add timestamps and TTL
            for doc in documents:
                if 'timestamp' not in doc:
                    doc['timestamp'] = datetime.utcnow()
                if hasattr(self.config, 'MONGODB_RETENTION_DAYS') and self.config.MONGODB_RETENTION_DAYS > 0:
                    doc['expires_at'] = datetime.utcnow() + timedelta(days=self.config.MONGODB_RETENTION_DAYS)

            result = await self.async_database[collection].insert_many(documents)
            return result.acknowledged

        except Exception as e:
            print(f"❌ MongoDB async bulk insert error: {e}")
            return False

    # ===== CLEANUP METHODS =====

    def cleanup_expired_documents(self):
        """Remove expired documents based on retention policy"""
        if not self.is_connected:
            return

        try:
            cutoff_date = datetime.utcnow()

            for collection in self.collections.values():
                result = self.database[collection].delete_many({
                    'expires_at': {'$lt': cutoff_date}
                })

                if result.deleted_count > 0:
                    print(f"🧹 Cleaned up {result.deleted_count} expired documents from {collection}")

        except Exception as e:
            print(f"❌ Cleanup error: {e}")

    def close(self):
        """Close database connections"""
        if self.client:
            self.client.close()
        if self.async_client:
            self.async_client.close()
        self.is_connected = False
        print("🔌 MongoDB connections closed")

    def __del__(self):
        """Destructor - ensure connections are closed"""
        self.close()
