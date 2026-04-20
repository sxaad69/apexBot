import os
import sys
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import ApexHunterBot

bot = ApexHunterBot()
signal = {
    'action': 'enter',
    'side': 'buy',
    'entry_price': bot.engine.exchange.get_ticker('BTC/USDT')['last'],
    'stop_loss': bot.engine.exchange.get_ticker('BTC/USDT')['last'] * 0.98,
    'take_profit': bot.engine.exchange.get_ticker('BTC/USDT')['last'] * 1.05,
    'confidence': 99.9,
    'stop_loss_roe': 10.0
}
print(f"Forcing entry at {signal['entry_price']}")
bot.engine.execute_paper_trade(signal, 'A6_Forced', 'BTC/USDT')

print("Starting bot to monitor adoption and trailing stops...")
bot.run(interval=5)
