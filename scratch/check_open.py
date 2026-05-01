import sqlite3, ccxt
conn = sqlite3.connect('/home/ubuntu/apexBot/data/apex_hunter.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT symbol, side, entry_price, size, leverage FROM trades WHERE status="OPEN"')
trades = c.fetchall()

if not trades:
    print('No open trades currently.')
else:
    exchange = ccxt.binance({'enableRateLimit': True})
    exchange.set_sandbox_mode(True)
    tickers = exchange.fetch_tickers([t['symbol'].split(':')[0] for t in trades])
    
    total_pnl = 0.0
    print(f'{"SYMBOL":<15} | {"SIDE":<5} | {"ROE %":<8} | {"DOLLAR PNL":<10}')
    print('-'*50)
    for t in trades:
        ccxt_sym = t['symbol'].split(':')[0]
        if ccxt_sym not in tickers: continue
        current_price = tickers[ccxt_sym]['last']
        
        if t['side'] == 'buy':
            price_move = (current_price - t['entry_price']) / t['entry_price']
        else:
            price_move = (t['entry_price'] - current_price) / t['entry_price']
            
        roe = price_move * t['leverage'] * 100
        dollar_pnl = 30.0 * (roe / 100)
        total_pnl += dollar_pnl
        
        indicator = '🟩' if dollar_pnl > 0 else '🟥'
        print(f'{t["symbol"]:15} | {t["side"]:5} | {roe:8.2f}% | ${dollar_pnl:8.2f} {indicator}')
        
    print('-'*50)
    indicator = '🟩' if total_pnl > 0 else '🟥'
    print(f'TOTAL UNREALIZED: ${total_pnl:.2f} {indicator}')
