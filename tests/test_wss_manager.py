import pytest
import json
import asyncio
from exchange.wss_manager import BinanceFuturesWSSManager

def test_wss_manager_parsing():
    """Test that the WSS manager correctly parses the Binance array payload."""
    manager = BinanceFuturesWSSManager()
    
    # Simulated Binance !markPrice@arr@1s payload
    payload = [
        {"s": "BTCUSDT", "p": "65000.50"},
        {"s": "LABUSDT", "p": "4.60"},
        {"s": "ETHUSDT", "p": "3200.00"}
    ]
    
    # Inject the payload manually
    manager._handle_message(json.dumps(payload))
    
    # Assertions
    assert manager.live_prices["BTC/USDT"] == 65000.50
    assert manager.live_prices["LAB/USDT"] == 4.60
    assert manager.live_prices["ETH/USDT"] == 3200.00
    assert len(manager.live_prices) == 3

def test_wss_manager_symbol_conversion():
    """Test that non-USDT symbols are also converted correctly."""
    manager = BinanceFuturesWSSManager()
    payload = [{"s": "BTCCUSD", "p": "65000.00"}]
    manager._handle_message(json.dumps(payload))
    assert manager.live_prices["BTC/CUSD"] == 65000.00
