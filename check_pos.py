import sys
import os
sys.path.append(os.path.abspath('.'))
from config.config import Config
from exchange.ccxt_client import CCXTExchangeClient
from bot_logging.mongo_logger import MongoLogger
config = Config()
logger = MongoLogger(config)
exchange = CCXTExchangeClient(config, logger)
pos = exchange.get_positions()
print('Binance Active Positions:', sum(1 for p in pos if abs(float(p.get('contracts', 0))) > 0))
