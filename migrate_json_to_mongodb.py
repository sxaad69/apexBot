#!/usr/bin/env python3
"""
APEX HUNTER V14 - JSON to MongoDB Migration Script
Migrate all JSON data files to MongoDB Atlas
"""

import json
import sys
from pathlib import Path
from datetime import datetime
from config import Config
from database.mongo_manager import MongoManager


def load_json_file(file_path):
    """Load and return JSON data from file"""
    if not file_path.exists():
        return []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"❌ Error loading {file_path.name}: {e}")
        return []


def migrate_collection(json_file, collection_name, mongo_manager, dry_run=False):
    """Migrate a single JSON file to MongoDB collection"""
    json_path = Path("data") / json_file

    print(f"\n📄 Processing {json_file} → {collection_name}")

    # Load JSON data
    data = load_json_file(json_path)

    if not data:
        print(f"   ℹ️  No data found in {json_file}")
        return 0

    print(f"   📊 Found {len(data)} records")

    if dry_run:
        print("   🔍 Dry run - would migrate records but not actually doing it")
        return len(data)

    # Insert data to MongoDB
    success = mongo_manager.insert_many(collection_name, data)

    if success:
        print(f"   ✅ Successfully migrated {len(data)} records")
        return len(data)
    else:
        print("   ❌ Migration failed"
        return 0


def main():
    parser = argparse.ArgumentParser(
        description='APEX HUNTER V14 JSON to MongoDB Migration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 migrate_json_to_mongodb.py              # Migrate all data
  python3 migrate_json_to_mongodb.py --dry-run    # Preview migration
  python3 migrate_json_to_mongodb.py --force      # Skip confirmations

Migration Map:
  futures_trades.json         → futures_trades collection
  spot_signals.json           → spot_signals collection
  arbitrage_opportunities.json → arbitrage_opportunities collection
  trailing_stops.json         → trailing_stops collection
  risk_rejections.json        → risk_rejections collection
  system_logs.json            → system_logs collection
        """
    )

    parser.add_argument('--dry-run', action='store_true',
                       help='Preview migration without actually migrating data')
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompts')
    parser.add_argument('--collection', type=str,
                       help='Migrate only specific collection (filename without .json)')

    args = parser.parse_args()

    print("🚀 APEX HUNTER V14 JSON → MongoDB Migration")
    print("=" * 50)

    # Load configuration
    try:
        config = Config()
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        sys.exit(1)

    # Check MongoDB configuration
    if not getattr(config, 'MONGODB_ENABLED', True):
        print("❌ MongoDB is disabled in configuration")
        print("   Set MONGODB_ENABLED=true in your .env file")
        sys.exit(1)

    # Initialize MongoDB connection
    print("🔌 Connecting to MongoDB...")
    mongo_manager = MongoManager(config)

    if not mongo_manager.is_connected:
        print("❌ Cannot connect to MongoDB")
        print("   Check your MongoDB configuration in .env")
        sys.exit(1)

    print(f"✅ Connected to database: {config.MONGODB_DATABASE}")

    # Define migration mapping
    migration_map = {
        'futures_trades.json': 'futures_trades',
        'spot_signals.json': 'spot_signals',
        'arbitrage_opportunities.json': 'arbitrage_opportunities',
        'trailing_stops.json': 'trailing_stops',
        'risk_rejections.json': 'risk_rejections',
        'system_logs.json': 'system_logs'
    }

    # Filter to specific collection if requested
    if args.collection:
        json_file = f"{args.collection}.json"
        if json_file not in migration_map:
            print(f"❌ Unknown collection: {args.collection}")
            print("Available collections:", ', '.join(k.replace('.json', '') for k in migration_map.keys()))
            sys.exit(1)

        migration_map = {json_file: migration_map[json_file]}

    # Check data directory
    data_dir = Path("data")
    if not data_dir.exists():
        print("❌ Data directory not found")
        sys.exit(1)

    # Preview migration
    print("
📋 Migration Preview:"    print("-" * 30)

    total_records = 0
    collections_to_migrate = []

    for json_file, collection in migration_map.items():
        json_path = data_dir / json_file
        if json_path.exists():
            data = load_json_file(json_path)
            record_count = len(data)
            total_records += record_count

            file_size = json_path.stat().st_size / (1024 * 1024)  # MB
            print("6.2f"            collections_to_migrate.append((json_file, collection, record_count))
        else:
            print(f"⚠️  {json_file} not found (skipping)")

    if not collections_to_migrate:
        print("ℹ️  No data files found to migrate")
        sys.exit(0)

    print("
📊 Total: {total_records} records across {len(collections_to_migrate)} collections"
    if args.dry_run:
        print("\n🔍 DRY RUN MODE - No data will be migrated")

    # Confirm migration (unless force or dry-run)
    if not args.force and not args.dry_run:
        print("
⚠️  WARNING: This will migrate data to MongoDB"        print("   Make sure your MongoDB connection is properly configured"
        confirm = input("\nContinue with migration? (yes/no): ").lower().strip()

        if confirm not in ['yes', 'y']:
            print("❌ Migration cancelled")
            sys.exit(0)

    # Perform migration
    print("\n🚀 Starting Migration...")
    print("-" * 30)

    total_migrated = 0
    successful_collections = 0

    for json_file, collection, expected_count in collections_to_migrate:
        try:
            migrated = migrate_collection(json_file, collection, mongo_manager, args.dry_run)
            total_migrated += migrated

            if migrated > 0:
                successful_collections += 1

        except Exception as e:
            print(f"❌ Error migrating {json_file}: {e}")

    # Summary
    print("\n" + "=" * 50)

    if args.dry_run:
        print("🔍 DRY RUN COMPLETED")
        print(f"   Would migrate: {total_records} records")
    else:
        print("✅ MIGRATION COMPLETED")
        print(f"   Migrated: {total_migrated} records")
        print(f"   Collections: {successful_collections}/{len(collections_to_migrate)} successful")

    print("=" * 50)

    # Close connection
    mongo_manager.close()


if __name__ == "__main__":
    main()
