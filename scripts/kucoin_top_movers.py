
import ccxt
import pandas as pd
from datetime import datetime

def main():
    print("Initializing KuCoin Futures (Native) connection...")
    # Use the specialized kucoinfutures class
    exchange = ccxt.kucoinfutures({
        'enableRateLimit': True,
    })

    try:
        print("Fetching all KuCoin Futures tickers...")
        tickers = exchange.fetch_tickers()
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return

    # Filter for active USDT perpetuals/futures
    market_data = []
    for symbol, ticker in tickers.items():
        # KuCoin Futures symbols usually end with USDTM or are in symbol:USDT format
        # In ccxt.kucoinfutures, symbols are typically 'BTC/USDT:USDT' or similar
        if (symbol.endswith(':USDT') or 'USDT' in symbol) and ticker.get('percentage') is not None:
            market_data.append({
                'symbol': symbol,
                'change_24h': ticker['percentage'],
                'volume_24h': ticker.get('quoteVolume', 0),
                'last_price': ticker.get('last', 0)
            })

    if not market_data:
        print("No market data found for USDT futures pairs.")
        # Debug: show first few symbols to see format
        sample_symbols = list(tickers.keys())[:5]
        print(f"Sample symbols available: {sample_symbols}")
        return

    # Sort by percentage change
    market_data.sort(key=lambda x: x['change_24h'], reverse=True)

    print(f"\n--- TRUE KUCOIN FUTURES MARKET REPORT ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}) ---")
    
    print("\n🚀 TOP 10 GAINERS (24H)")
    print(f"{'Symbol':<20} | {'Price':<10} | {'Change %':<10} | {'Volume (24h)':<15}")
    print("-" * 65)
    for r in market_data[:10]:
        print(f"{r['symbol']:<20} | ${r['last_price']:<10.4g} | {r['change_24h']:>+9.2f}% | ${r['volume_24h']:,.0f}")

    print("\n🔻 TOP 10 LOSERS (24H)")
    print(f"{'Symbol':<20} | {'Price':<10} | {'Change %':<10} | {'Volume (24h)':<15}")
    print("-" * 65)
    for r in reversed(market_data[-10:]):
        print(f"{r['symbol']:<20} | ${r['last_price']:<10.4g} | {r['change_24h']:>+9.2f}% | ${r['volume_24h']:,.0f}")

    # Top by Volume
    market_data.sort(key=lambda x: x['volume_24h'], reverse=True)
    print("\n💎 TOP 10 BY VOLUME (24H)")
    print(f"{'Symbol':<20} | {'Price':<10} | {'Change %':<10} | {'Volume (24h)':<15}")
    print("-" * 65)
    for r in market_data[:10]:
        print(f"{r['symbol']:<20} | ${r['last_price']:<10.4g} | {r['change_24h']:>+9.2f}% | ${r['volume_24h']:,.0f}")

if __name__ == "__main__":
    main()
