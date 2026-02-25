import os
import json
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path to import bot modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.config import Config
    from database.mongo_manager import MongoManager
except ImportError as e:
    print(f"❌ Error: Could not import bot modules: {e}")
    sys.exit(1)

def json_serial(obj):
    """JSON serializer for objects not serializable by default json code"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)

def main():
    print("=" * 60)
    print("      🚀 APEX HUNTER V14 - MONGODB ATLAS EXPORTER 🚀")
    print("=" * 60)

    # 1. Setup Configuration
    from dotenv import load_dotenv
    load_dotenv()
    
    config = Config.__new__(Config)
    config._load_configuration()
    
    # 2. Connect to MongoDB
    print(f"📡 Connecting to Atlas: {config.MONGODB_DATABASE}...")
    mongo = MongoManager(config)

    if not mongo.is_connected:
        print("❌ Could not connect to MongoDB Atlas.")
        print("💡 Ensure your .env has correct credentials and you are on a compatible network.")
        return

    # 3. Setup Export Directory
    export_dir = Path("db")
    if export_dir.exists():
        print(f"🧹 Clearing existing export directory: {export_dir.absolute()}...")
        for file in export_dir.glob("*.json"):
            try:
                file.unlink()
            except Exception as e:
                print(f"⚠️ Could not delete {file.name}: {e}")
    else:
        export_dir.mkdir(exist_ok=True)
    
    print(f"📂 Exporting to root folder: {export_dir.absolute()}")

    # 4. Get all collections
    collections = mongo.database.list_collection_names()
    print(f"📊 Found {len(collections)} collections to export.")

    # 5. Export each collection
    for coll_name in collections:
        print(f"📦 Exporting {coll_name}...", end="", flush=True)
        
        try:
            # Fetch all documents
            documents = list(mongo.database[coll_name].find({}))
            
            # Save to JSON file
            export_path = export_dir / f"{coll_name}.json"
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(documents, f, indent=2, default=json_serial, ensure_ascii=False)
            
            print(f" ✅ ({len(documents)} docs)")
        except Exception as e:
            print(f" ❌ Error: {e}")

    print("-" * 60)
    print("✅ EXPORT COMPLETE!")
    print(f"📍 Files are located in the '{export_dir}' folder.")
    print("📝 You can now share these files for deep analysis.")
    print("=" * 60)

if __name__ == "__main__":
    main()
