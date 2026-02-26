"""
Log Cleanup Script
Deletes log files older than 7 days to keep the server clean.
"""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

# Configuration
RETENTION_DAYS = 7
DIRECTORIES = ['./logs', './data']

def cleanup_old_files():
    """Delete files older than RETENTION_DAYS in the specified directories"""
    print(f"🧹 Starting cleanup (Retention: {RETENTION_DAYS} days)...")
    
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
                        
    print(f"\n✨ Cleanup complete!")
    print(f"  ✅ Files deleted: {files_deleted}")
    print(f"  ✅ Space freed: {space_freed / (1024*1024):.2f} MB")

if __name__ == "__main__":
    cleanup_old_files()
