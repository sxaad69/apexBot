import os
import ccxt
from dotenv import load_dotenv

def audit():
    load_dotenv()
    exchange = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'options': {'defaultType': 'future'}
    })
    
    balance = exchange.fetch_balance()
    print('--- LIVE ACCOUNT VALUE ---')
    wallet_usdt = float(balance['total']['USDT'])
    print(f'Wallet Balance: ${wallet_usdt:.2f}')
    
    total_unrealized = 0
    open_positions = []
    for p in balance['info']['positions']:
        if float(p['positionAmt']) != 0:
            pnl = float(p['unrealizedProfit'])
            total_unrealized += pnl
            open_positions.append((p['symbol'], pnl))
    
    for symbol, pnl in open_positions:
        print(f'{symbol} | Unrealized: ${pnl:+.2f}')
        
    print(f'Total Unrealized Bleed: ${total_unrealized:+.2f}')
    print(f'Effective Account Equity: ${wallet_usdt + total_unrealized:.2f}')

if __name__ == "__main__":
    audit()
