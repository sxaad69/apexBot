#!/usr/bin/env python3
"""
APEX HUNTER V14 - Stress Test Agent
Simulates various market conditions to verify risk management and wallet fidelity.
"""

import os
import sys
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import Config
from main import PaperTradingEngine
from core.spot_trading_engine import SpotTradingEngine

class MockLogger:
    def info(self, msg): print(f"🟢 INFO: {msg}")
    def warning(self, msg): print(f"🟡 WARN: {msg}")
    def error(self, msg): print(f"🔴 ERROR: {msg}")
    def debug(self, msg): pass
    def system(self, msg): print(f"🖥️ SYSTEM: {msg}")
    def trade_entry(self, **kwargs): print(f"📥 ENTRY: {kwargs}")
    def trade_exit(self, **kwargs): print(f"📤 EXIT: {kwargs}")
    def risk_layer_triggered(self, *args, **kwargs):
        print(f"⚠️ RISK LAYER TRIGGERED: {args} {kwargs}")
    def risk_rejection(self, *args, **kwargs):
        print(f"🚫 RISK REJECTION: {args} {kwargs}")
    def save_active_positions(self, *args, **kwargs): pass
    def save_market_analysis(self, *args, **kwargs): pass
    def save_strategy_signals(self, *args, **kwargs): pass
    def save_hourly_metrics(self, *args, **kwargs): pass
    def record_risk_rejection(self, *args, **kwargs): pass
    def position_rejected(self, **kwargs): 
        print(f"🚫 REJECTED: {kwargs.get('symbol')} | Reason: {kwargs.get('reason')} | Layer: {kwargs.get('layer')}")
    def log_trade(self, *args, **kwargs): pass
    def log_trailing_stop(self, *args, **kwargs): pass
    def trade_rejection(self, *args, **kwargs): pass
    def log_circuit_breaker(self, *args, **kwargs): print(f"⚡ CIRCUIT BREAKER: {args} {kwargs}")

class StressTestAgent:
    def __init__(self, mode="sideways"):
        self.mode = mode
        self.config = Config()
        self.config.TESTING_MODE = True
        self.config.FORCE_TRADES = False
        self.logger = MockLogger()
        self.telegram = None
        
        # Lower thresholds for testing
        self.config.TRAILING_STOP_ACTIVATION = 0.5
        self.config.TRAILING_STOP_DISTANCE = 0.2
        
        # Initialize engines
        self.futures_engine = PaperTradingEngine(self.config, self.logger, self.telegram)
        self.spot_engine = SpotTradingEngine(self.config, self.logger, self.telegram, self.futures_engine.risk_manager)
        
        # MOCK EXCHANGE TO PREVENT REAL API CALLS
        class MockCCXT:
            def fetch_order_book(self, *args, **kwargs):
                # Return 30% imbalance to trigger A5
                return {
                    'bids': [[50000, 13]], 
                    'asks': [[50001, 10]]
                }
            def fetch_ohlcv(self, *args, **kwargs): return []
            def fetch_tickers(self, *args, **kwargs): return {}
            
        class MockExchangeClient:
            def __init__(self):
                self.exchange = MockCCXT()

        self.mock_client = MockExchangeClient()
        
        # Initialize engines with shared mock client
        self.futures_engine = PaperTradingEngine(self.config, self.logger, self.telegram)
        self.futures_engine.exchange = self.mock_client
        
        self.spot_engine = SpotTradingEngine(self.config, self.logger, self.telegram, self.futures_engine.risk_manager, exchange_client=self.mock_client)
        
        # Pre-generate full dataset
        self.full_df = self.generate_synthetic_data(length=500)
        
        print(f"\n🚀 STRESS TEST STARTED: MODE={mode.upper()}\n")

    def generate_synthetic_data(self, length=500):
        """Generate synthetic OHLCV data based on mode"""
        base_price = 50000.0
        data = []
        now = datetime.now()
        
        price = base_price
        for i in range(length):
            if self.mode == "sideways":
                change = (np.random.random() - 0.5) * 0.0002 # Very small moves
                price *= (1 + change)
                adx = 12
            elif self.mode == "trending":
                change = 0.0005 + (np.random.random() * 0.001) # Sustained growth
                price *= (1 + change)
                adx = 35
            elif self.mode == "flash_crash":
                if 400 < i < 410:
                    price *= 0.98 # Sudden drop
                else:
                    price *= (1 + (np.random.random() - 0.5) * 0.001)
                adx = 40
            
            data.append([
                now - timedelta(minutes=15 * (length - i)),
                price * 0.999,
                price * 1.002,
                price * 0.998,
                price,
                1000 + (np.random.random() * 500)
            ])
            
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df.set_index('timestamp', inplace=True)
        
        # Add basic indicators
        df['adx'] = adx
        df['ema_fast'] = df['close'].ewm(span=9).mean()
        df['ema_slow'] = df['close'].ewm(span=21).mean()
        df['ema_master'] = df['close'].ewm(span=200).mean()
        
        return df

    def run_simulation(self, cycles=50):
        symbol = "TEST/USDT"
        
        print(f"--- RUNNING {cycles} CYCLES ---")
        
        # Start from bar 400 so we have enough history for indicators
        for i in range(400, 400 + cycles):
            current_df = self.full_df.iloc[:i+1]
            
            # Mock engine's fetch_market_data to return current slice
            self.futures_engine.fetch_market_data = lambda s, **k: current_df
            self.spot_engine.fetch_market_data = lambda s, **k: current_df
            
            self.futures_engine.run_cycle(symbol)
            self.spot_engine.run_cycle(symbol)
        
        # Verify results
        print("\n--- FINAL AUDIT ---")
        self.audit_engine("FUTURES", self.futures_engine)
        self.audit_engine("SPOT", self.spot_engine)

    def audit_engine(self, name, engine):
        if name == "FUTURES":
            # For futures, capital is per strategy
            for strat_name, balance in engine.capital.items():
                print(f"[{name}] {strat_name} Balance: ${balance:.2f} (Start: $100.00)")
        else:
            print(f"[{name}] Total Balance: ${engine.virtual_balance:.2f} (Start: $100.00)")
        
        trades = engine.trades
        print(f"[{name}] Total Trades: {len(trades)}")
        for t in trades:
            pnl = t.get('pnl') or t.get('pnl_usdt', 0)
            print(f"  - Trade: {t.get('side')} {t.get('symbol')} | P&L: ${pnl:+.2f} | Reason: {t.get('reason')}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = "sideways"
        
    agent = StressTestAgent(mode=mode)
    agent.run_simulation()
