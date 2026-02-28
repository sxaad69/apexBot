import time
import asyncio
from config import Config
from bot_logging import Logger
from strategies.strategy_a6 import StrategyA6

def main():
    print("Initializing Config and Logger...")
    config = Config()
    logger = Logger(config)
    
    print("Starting Strategy A6 (this will launch the WSS thread)...")
    a6 = StrategyA6(config, logger)
    
    print("Waiting 10 seconds for WebSocket to connect and gather orderbooks...")
    for i in range(10):
        time.sleep(1)
        print(f"Tick {i+1}...")
        
    print("\n--- Current WebSocket Memory State ---")
    for symbol, book in a6.latest_orderbooks.items():
        bids_len = len(book.get('bids', []))
        asks_len = len(book.get('asks', []))
        imbalance = a6.fetch_imbalance(symbol)
        print(f"{symbol}: {bids_len} bids, {asks_len} asks attached. Imbalance: {imbalance*100:.2f}%")
        
    print("\nCompleted WSS test.")

if __name__ == '__main__':
    main()
