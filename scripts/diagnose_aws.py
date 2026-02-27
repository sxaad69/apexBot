
import sys
import os
from pathlib import Path
import sqlite3

# Add project root to path
sys.path.append(os.getcwd())

try:
    from config.config import Config
    from database.sqlite_manager import SQLiteManager
    print("✅ Project modules imported successfully")
except Exception as e:
    print(f"❌ Module import failed: {e}")
    sys.exit(1)

def run_diagnostics():
    print("\n--- 🔍 APEX HUNTER AWS DIAGNOSTICS ---")
    
    # 1. Check Environment Variables
    print("\n1. Environment Check:")
    sqlite_env = os.getenv('SQLITE_ENABLED')
    print(f"   OS SQLITE_ENABLED: {sqlite_env}")
    
    # 2. Check Config Resolution
    print("\n2. Configuration Resolution:")
    try:
        config = Config()
        print(f"   config.SQLITE_ENABLED: {getattr(config, 'SQLITE_ENABLED', 'MISSING')}")
        print(f"   config.DATA_DIRECTORY: {getattr(config, 'DATA_DIRECTORY', 'MISSING')}")
        
        data_path = Path(getattr(config, 'DATA_DIRECTORY', './data'))
        print(f"   Resolved Data Path: {data_path.absolute()}")
        print(f"   Data Path Exists: {data_path.exists()}")
        
    except Exception as e:
        print(f"   ❌ Config initialization failed: {e}")

    # 3. Test SQLite Creation Manually
    print("\n3. Manual SQLite Creation Test:")
    test_db = data_path / 'diag_test.db'
    try:
        conn = sqlite3.connect(test_db)
        conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        print(f"   ✅ Successfully created {test_db}")
        if test_db.exists():
            os.remove(test_db)
            print("   ✅ Successfully removed test file")
    except Exception as e:
        print(f"   ❌ Failed to create SQLite file: {e}")

    print("\n4. SQLiteManager Initialization Test:")
    try:
        db = SQLiteManager(config)
        print(f"   ✅ SQLiteManager initialized successfully")
        print(f"   Main DB: {db.main_db.absolute()}")
        print(f"   Log DB: {db.log_db.absolute()}")
        
        if db.main_db.exists():
            print("   ✅ Main DB file FOUND")
        else:
            print("   ❌ Main DB file NOT FOUND (Unexpected!)")
            
    except Exception as e:
        print(f"   ❌ SQLiteManager failed: {e}")

    print("\n--- End of Diagnostics ---")

if __name__ == "__main__":
    run_diagnostics()
