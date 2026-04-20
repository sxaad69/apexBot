import os
import sys
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path to import config
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.config import Config
from utils.logger import setup_logger
from exchange.ccxt_client import CCXTExchangeClient

config = Config()
logger = setup_logger(config)
try:
    exchange = CCXTExchangeClient(config, logger)
    positions = exchange.get_positions()
    print("Found positions:", len(positions))
    if positions:
        print("Keys available in position object:")
        print(list(positions[0].keys()))
        print("Sample position:")
        print(positions[0])
except Exception as e:
    print("Error:", e)
