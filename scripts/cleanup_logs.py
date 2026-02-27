"""
Log Cleanup Script
Deletes log files older than 7 days to keep the server clean.
"""

import os
import time
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
RETENTION_DAYS = 7
DIRECTORIES = ['./logs', './data']
SQLITE_DBS = ['./data/activity_log.db']

def cleanup_sqlite():
    """Purge old records from SQLite activity logs"""
    print(f"🧹 Starting SQLite maintenance (Retention: {RETENTION_DAYS} days)...")
    cutoff = (datetime.utcnow() - timedelta(days=RETENTION_DAYS)).isoformat()
    
    for db_path in SQLITE_DBS:
        path = Path(db_path)
        if not path.exists(): continue
        
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            
            # Identify tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = [row[0] for row in cursor.fetchall()]
            
            for table in tables:
                if table in ['activity_log', 'market_analysis', 'strategy_signals']:
                    cursor.execute(f"DELETE FROM {table} WHERE timestamp < ?", (cutoff,))
                    print(f"  🗑️ Purged {cursor.rowcount} rows from {table}")
            
            conn.commit()
            cursor.execute("VACUUM")
            conn.close()
            print(f"  ✅ Optimized: {db_path}")
        except Exception as e:
            print(f"  ❌ Error maintaining {db_path}: {e}")

def cleanup_old_files():
    """Delete files older than RETENTION_DAYS in the specified directories"""
    print(f"🧹 Starting file cleanup (Retention: {RETENTION_DAYS} days)...")
    
    cutoff_time = time.time() - (RETENTION_DAYS * 86400)
    files_deleted = 0
    space_freed = 0
    
    for dir_path in DIRECTORIES:
        path = Path(dir_path)
        if not path.exists():
            print(f"📁 Directory not found: {dir_path}")
            continue
            
        print(f"📂 Scanning: {dir_path}")
        
        for file in path.iterdir():
            if file.is_file():
                # Don't delete database files!
                if file.suffix in ['.db', '.db-journal', '.db-wal']:
                    continue
                    
                # Check modification time
                file_mtime = file.stat().st_mtime
                if file_mtime < cutoff_time:
                    try:
                        file_size = file.stat().st_size
                        file.unlink()
                        files_deleted += 1
                        space_freed += file_size
                        print(f"  🗑️ Deleted: {file.name}")
                    except Exception as e:
                        print(f"  ❌ Error deleting {file.name}: {e}")
                        
    print(f"\n✨ File cleanup complete!")
    print(f"  ✅ Files deleted: {files_deleted}")
    print(f"  ✅ Space freed: {space_freed / (1024*1024):.2f} MB")

if __name__ == "__main__":
    cleanup_sqlite()
    print("-" * 40)
    cleanup_old_files()
