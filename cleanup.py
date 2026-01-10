#!/usr/bin/env python3
"""
APEX HUNTER V14 - Cleanup Script
Clean logs and database files with confirmation prompts
"""

import argparse
import sys
from pathlib import Path
from config import Config


def clean_log_files(config, dry_run=False):
    """Clean all log files in the logs directory"""
    logs_dir = Path(getattr(config, 'LOG_FILE_PATH', './logs'))

    if not logs_dir.exists():
        print("⚠️  Logs directory not found")
        return 0

    # Find all log files
    log_files = list(logs_dir.glob("*.log"))

    if not log_files:
        print("ℹ️  No log files found")
        return 0

    print(f"🗑️  Found {len(log_files)} log files to clean:")

    for log_file in log_files:
        file_size = log_file.stat().st_size / (1024 * 1024)  # Size in MB
        print(f"   • {log_file.name} ({file_size:.2f} MB)")

    if dry_run:
        print("\n🔍 Dry run - no files deleted")
        return len(log_files)

    # Confirm deletion
    confirm = input(f"\n⚠️  Delete {len(log_files)} log files? (yes/no): ").lower().strip()

    if confirm not in ['yes', 'y']:
        print("❌ Cleanup cancelled")
        return 0

    # Delete files
    deleted_count = 0
    for log_file in log_files:
        try:
            log_file.unlink()
            print(f"   ✅ Deleted: {log_file.name}")
            deleted_count += 1
        except Exception as e:
            print(f"   ❌ Error deleting {log_file.name}: {e}")

    print(f"\n✅ Log cleanup completed: {deleted_count} files deleted")
    return deleted_count


def clean_database_files(config, dry_run=False):
    """Clean all JSON database files"""
    data_dir = Path("data")

    if not data_dir.exists():
        print("⚠️  Data directory not found")
        return 0

    # JSON files to clean
    json_files = [
        "futures_trades.json",
        "spot_signals.json",
        "arbitrage_opportunities.json",
        "trailing_stops.json",
        "risk_rejections.json",
        "system_logs.json"
    ]

    files_to_clean = []
    total_size = 0

    for json_file in json_files:
        file_path = data_dir / json_file
        if file_path.exists():
            file_size = file_path.stat().st_size
            total_size += file_size
            files_to_clean.append((file_path, file_size))

    if not files_to_clean:
        print("ℹ️  No database files found")
        return 0

    print(f"🗑️  Found {len(files_to_clean)} database files to clean:")
    print(f"   Total size: {total_size / (1024 * 1024):.2f} MB")
    for file_path, file_size in files_to_clean:
        size_mb = file_size / (1024 * 1024)
        print(f"   • {file_path.name} ({size_mb:.2f} MB)")

    if dry_run:
        print("\n🔍 Dry run - no files deleted")
        return len(files_to_clean)

    # Confirm deletion
    confirm = input(f"\n⚠️  Delete {len(files_to_clean)} database files? (yes/no): ").lower().strip()

    if confirm not in ['yes', 'y']:
        print("❌ Cleanup cancelled")
        return 0

    # Delete files
    deleted_count = 0
    for file_path, _ in files_to_clean:
        try:
            file_path.unlink()
            print(f"   ✅ Deleted: {file_path.name}")
            deleted_count += 1
        except Exception as e:
            print(f"   ❌ Error deleting {file_path.name}: {e}")

    print(f"\n✅ Database cleanup completed: {deleted_count} files deleted")
    return deleted_count


def main():
    parser = argparse.ArgumentParser(
        description='APEX HUNTER V14 Cleanup Script',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cleanup.py --logs --db              # Clean everything
  python3 cleanup.py --logs                   # Clean only logs
  python3 cleanup.py --db                     # Clean only database
  python3 cleanup.py --logs --dry-run         # Preview log cleanup
  CLEAN_LOGS=yes python3 main.py              # Clean logs on bot startup
  CLEAN_DB=yes python3 main.py                # Clean database on bot startup
        """
    )

    parser.add_argument('--logs', action='store_true',
                       help='Clean all log files in logs/ directory')
    parser.add_argument('--db', '--database', action='store_true',
                       help='Clean all JSON database files in data/ directory')
    parser.add_argument('--dry-run', action='store_true',
                       help='Preview what would be deleted without actually deleting')
    parser.add_argument('--force', action='store_true',
                       help='Skip confirmation prompts (use with caution)')

    args = parser.parse_args()

    # Load configuration
    try:
        config = Config()
    except Exception as e:
        print(f"❌ Error loading configuration: {e}")
        sys.exit(1)

    # Check if any operation was specified
    if not args.logs and not args.db:
        print("❌ No cleanup operation specified. Use --logs, --db, or both.")
        parser.print_help()
        sys.exit(1)

    print("🧹 APEX HUNTER V14 Cleanup Script")
    print("=" * 40)

    total_cleaned = 0

    # Clean logs
    if args.logs:
        print("\n📝 LOG FILES CLEANUP")
        print("-" * 20)
        cleaned = clean_log_files(config, args.dry_run)
        if not args.dry_run:
            total_cleaned += cleaned

    # Clean database
    if args.db:
        print("\n💾 DATABASE FILES CLEANUP")
        print("-" * 25)
        cleaned = clean_database_files(config, args.dry_run)
        if not args.dry_run:
            total_cleaned += cleaned

    print("\n" + "=" * 40)

    if args.dry_run:
        print("🔍 DRY RUN COMPLETED - No files were deleted")
    else:
        print(f"✅ CLEANUP COMPLETED - {total_cleaned} files deleted")

    print("=" * 40)


if __name__ == "__main__":
    main()
