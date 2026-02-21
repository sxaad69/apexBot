import os
import sys

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from database.mongo_manager import MongoManager
from config.config import Config

def debug_spot_data():
    load_dotenv()
    config = Config.__new__(Config)
    config._load_configuration()
    mongo = MongoManager(config)
    
    if not mongo.is_connected:
        print("❌ DB Connection Fail")
        return

    print("--- RAW SPOT_SIGNALS SAMPLE ---")
    raw_spot = mongo.find_documents('spot_signals', limit=5)
    for doc in raw_spot:
        print(f"ID: {doc['_id']} | Symbol: {doc.get('symbol')} | Executed: {doc.get('executed')} | MarketType: {doc.get('market_type')}")

    print("\n--- ALL COLLECTIONS COUNT ---")
    for col in ['futures_trades', 'spot_signals', 'risk_rejections']:
        count = mongo.db[col].count_documents({})
        print(f"{col}: {count} docs")

if __name__ == "__main__":
    debug_spot_data()
