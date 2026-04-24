import sys
import os
sys.path.append(os.getcwd())
from config import Config
from bot_logging.mongo_logger import MongoLogger
from exchange import CCXTExchangeClient

config = Config()
logger = MongoLogger(config)
exchange = CCXTExchangeClient(config, logger, config.FUTURES_EXCHANGE)

positions = exchange.exchange.fetch_positions()
total_notional = 0
total_pnl = 0
count = 0

for p in positions:
    amt = abs(float(p.get('contracts', 0) or p.get('info', {}).get('positionAmt', 0)))
    price = float(p.get('entryPrice', 0) or p.get('info', {}).get('entryPrice', 0))
    pnl = float(p.get('unrealizedPnl', 0) or p.get('info', {}).get('unrealizedProfit', 0))
    if amt > 0:
        total_notional += (amt * price)
        total_pnl += pnl
        count += 1

fee_rate = float(getattr(config, 'FUTURES_FEE_PERCENT', 0.04)) / 100
closing_fees = total_notional * fee_rate
net_profit = total_pnl - closing_fees

print(f"--- EXIT SCENARIO ---")
print(f"Open Positions: {count}")
print(f"Total Notional Value: ${total_notional:.2f}")
print(f"Unrealized P&L: ${total_pnl:.2f}")
print(f"Estimated Fees (at {fee_rate*100:.2f}%): ${closing_fees:.2f}")
print(f"----------------------")
print(f"FINAL NET GAIN: ${net_profit:.2f}")
