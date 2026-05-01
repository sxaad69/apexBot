import sqlite3
import ccxt
import pandas as pd
from datetime import datetime

def check_unrealized():
    db_path = '/home/ubuntu/apexBot/data/apex_hunter.db'
    try:
        conn = sqlite3.connect(db_path)
        trades = pd.read_sql_query("SELECT trade_id, symbol, side, entry_price, size, leverage FROM trades WHERE status = 'OPEN'", conn)
        conn.close()
    except Exception as e:
        print(f"Error reading database: {e}")
        return
    
    if trades.empty:
        print('No open trades found.')
        return

    print(f"[{datetime.now()}] Fetching live prices for {len(trades)} open positions...")
    exchange = ccxt.binance({'options': {'defaultType': 'future'}})
    
    # Fetch all tickers to handle mapping easily
    tickers = exchange.fetch_tickers()
    
    results = []
    total_unrealized = 0
    
    for _, trade in trades.iterrows():
        symbol = trade['symbol'] # e.g. "BTC/USDT:USDT"
        
        # Try different formats for the ticker key
        ticker = None
        for key in [symbol, symbol.split(':')[0], symbol.replace(':USDT', '')]:
            if key in tickers:
                ticker = tickers[key]
                break
        
        if not ticker:
            print(f"Warning: Could not find price for {symbol}")
            continue

        current_price = ticker['last']
        entry_price = trade['entry_price']
        size = trade['size']
        leverage = trade['leverage']
        side = trade['side']
        
        if side == 'buy':
            pnl_pct = ((current_price - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - current_price) / entry_price) * 100
            
        pnl_amount = (size * leverage) * (pnl_pct / 100)
        total_unrealized += pnl_amount
        
        results.append({
            'symbol': symbol.split(':')[0],
            'side': side,
            'pnl_%': round(pnl_pct, 2),
            'pnl_$': round(pnl_amount, 2)
        })
        
    df = pd.DataFrame(results)
    losses = df[df['pnl_$'] < 0].sort_values('pnl_$')
    profits = df[df['pnl_$'] >= 0].sort_values('pnl_$', ascending=False)
    
    print('\n🔴 CURRENT LOSSES (UNREALIZED):')
    if losses.empty:
        print('No trades currently in the red!')
    else:
        print(losses.to_string(index=False))
    
    print('\n🟢 CURRENT PROFITS (UNREALIZED):')
    if profits.empty:
        print('No trades currently in profit!')
    else:
        print(profits.to_string(index=False))
        
    print('\n' + '='*40)
    print(f'TOTAL NET UNREALIZED P&L: ${total_unrealized:.2f}')
    print('='*40)

if __name__ == '__main__':
    check_unrealized()
