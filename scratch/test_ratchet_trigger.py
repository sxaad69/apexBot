import sys
import os
import asyncio
import unittest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, timedelta

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from risk.layers.portfolio_profit_ratchet import PortfolioProfitRatchet
from risk.risk_manager import RiskManager

class TestRatchetIntegration(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = MagicMock()
        self.config.PROFIT_RATCHET_ENABLED = True
        self.config.PROFIT_RATCHET_ACTIVATION = 6.0
        self.config.PROFIT_RATCHET_TRAILING = 1.0
        self.config.PROFIT_RATCHET_FLOOR = 1.0
        self.config.PROFIT_RATCHET_COOLDOWN = 5
        self.config.PROFIT_RATCHET_SLIPPAGE_BUFFER = 0.2
        self.config.FUTURES_FEE_PERCENT = 0.04
        
        self.db = MagicMock()
        self.exchange = MagicMock()
        self.exchange.exchange_id = 'binance'
        self.logger = MagicMock()
        self.telegram = MagicMock()
        
        self.ratchet = PortfolioProfitRatchet(
            self.config, self.db, self.exchange, self.logger, self.telegram
        )
        
        self.risk_manager = RiskManager(
            self.config, self.logger, self.db, profit_ratchet=self.ratchet
        )

    async def test_ratchet_activation_and_lock(self):
        """Test that ratchet activates and RiskManager respects the lock"""
        
        # We need to initialize nx_pro directly for testing with mocks
        self.ratchet.nx_pro = MagicMock()
        
        # Mock watch_positions to yield dummy position
        self.ratchet.nx_pro.watch_positions = AsyncMock(side_effect=[
            [{'symbol': 'BTC/USDT', 'notional': 1000, 'unrealizedPnl': 80}], # First iteration
            asyncio.CancelledError() # Stop the loop
        ])
        
        # Mock fetch_balance to return a 8.0% Net ROE (Gross is higher, say 8.5)
        # Margin: 1000, Profit: 85 (85/1000 = 8.5% gross, ~8.0% net)
        self.ratchet.nx_pro.fetch_balance = AsyncMock(return_value={
            'info': {
                'totalUnrealizedProfit': '85.0',
                'totalInitialMargin': '1000.0'
            }
        })
        
        # Bypass telegram to avoid API calls
        self.ratchet.telegram = MagicMock()
        self.ratchet._liquidate_all = AsyncMock()
        
        try:
            await self.ratchet.monitor_loop()
        except asyncio.CancelledError:
            pass

        # Verification 1: Ratchet should have activated and set its locked state
        self.assertTrue(self.ratchet.ratchet_active, "Ratchet failed to activate at 8.0% ROE")

        # Verification 2: RiskManager should be locked when ratchet active
        # We simulate the ratchet dropping to liquidation floor
        self.ratchet.is_liquidating = True
        self.assertTrue(self.risk_manager._is_ratchet_locked(), "RiskManager ignored live ratchet lock")

        print("✅ Ratchet Lock Verification: SUCCESS")

    def test_database_cooldown_lock(self):
        """Test that RiskManager respects the persistent database cooldown"""
        # Mock DB returning a future cooldown
        future_time = (datetime.utcnow() + timedelta(minutes=10)).isoformat()
        self.db.get_setting.return_value = future_time
        
        self.assertTrue(self.risk_manager._is_ratchet_locked())
        print("✅ DB Cooldown Verification: SUCCESS")

if __name__ == "__main__":
    unittest.main()
