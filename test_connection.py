#!/usr/bin/env python3
"""
APEX HUNTER V14 - Enhanced Multi-Exchange Connection Test
Tests connectivity to any configured exchange with flexible selection

Usage:
    python test_connection.py                    # Tests all configured exchanges
    python test_connection.py --exchange binance # Tests specific exchange
    python test_connection.py --telegram         # Tests Telegram bots only
"""

import sys
import os
import argparse
from datetime import datetime
from config import Config
from bot_logging import Logger
from exchange import CCXTExchangeClient
from notifications import TelegramNotificationManager


def print_header(title):
    """Print formatted header"""
    print()
    print("=" * 80)
    print(title.center(80))
    print("=" * 80)
    print()


def print_section(title):
    """Print section title"""
    print()
    print("─" * 80)
    print(f"  {title}")
    print("─" * 80)


def print_success(message):
    """Print success message"""
    print(f"   ✅ {message}")


def print_error(message):
    """Print error message"""
    print(f"   ❌ {message}")


def print_info(message):
    """Print info message"""
    print(f"   ℹ️  {message}")


def test_single_exchange(exchange_id, config, logger, exchange_type="spot"):
    """Test a single exchange connection"""
    results = {
        'exchange': exchange_id,
        'type': exchange_type,
        'connected': False,
        'balance': None,
        'markets': 0,
        'error': None
    }
    
    try:
        print(f"\n🔗 Connecting to {exchange_id.upper()} ({exchange_type})...")
        
        client = CCXTExchangeClient(config, logger, exchange_id)
        
        if hasattr(client.exchange, 'options'):
            client.exchange.options['defaultType'] = exchange_type
        
        # Test Balance
        print("\n📊 Test 1: Fetching Account Balance...")
        balance = client.get_balance()
        
        if balance:
            print_success("Balance fetched successfully!")
            results['connected'] = True
            results['balance'] = balance
            
            usdt = balance.get('USDT', {})
            if usdt:
                print(f"\n   💰 USDT Balance:")
                print(f"      Total: {usdt.get('total', 0):,.2f} USDT")
                print(f"      Free: {usdt.get('free', 0):,.2f} USDT")
            else:
                print_info("USDT balance: 0 or not available")
        else:
            print_error("Failed to fetch balance")
            results['error'] = "Balance fetch failed"
            return results
        
        # Test Markets
        print("\n📊 Test 2: Fetching Markets...")
        markets = client.get_markets()
        
        if markets:
            results['markets'] = len(markets)
            print_success(f"Found {len(markets):,} trading pairs")
        
        return results
        
    except Exception as e:
        print_error(f"Connection failed: {str(e)}")
        results['error'] = str(e)
        return results


def test_telegram_bots(config, logger):
    """Test all Telegram bots"""
    print_section("TELEGRAM BOT TESTS")
    
    try:
        telegram = TelegramNotificationManager(config, logger)
        
        results = {'futures': False, 'spot': False, 'arbitrage': False}
        
        if telegram.futures_bot and telegram.futures_bot.is_connected:
            print_success("Futures bot connected")
            if telegram.send_futures_message("✅ Apex Hunter V14 - Futures bot test"):
                results['futures'] = True
        
        if telegram.spot_bot and telegram.spot_bot.is_connected:
            print_success("Spot bot connected")
            if telegram.send_spot_message("✅ Apex Hunter V14 - Spot bot test"):
                results['spot'] = True
        
        if telegram.arbitrage_bot and telegram.arbitrage_bot.is_connected:
            print_success("Arbitrage bot connected")
            if telegram.send_arbitrage_message("✅ Apex Hunter V14 - Arbitrage bot test"):
                results['arbitrage'] = True
        
        return results
        
    except Exception as e:
        print_error(f"Telegram test failed: {str(e)}")
        return {'futures': False, 'spot': False, 'arbitrage': False}


def main():
    """Main test function"""
    parser = argparse.ArgumentParser(description='Test exchange and Telegram connections')
    parser.add_argument('--exchange', type=str, help='Test specific exchange (e.g., binance, kucoin)')
    parser.add_argument('--type', type=str, choices=['spot', 'futures'], default='spot')
    parser.add_argument('--telegram', action='store_true', help='Test Telegram bots only')
    
    args = parser.parse_args()
    
    print_header("APEX HUNTER V14 - CONNECTION TEST")
    
    # Load configuration
    print("⚙️  Loading configuration...")
    try:
        config = Config()
        logger = Logger(config)
        print_success("Configuration loaded")
    except Exception as e:
        print_error(f"Configuration error: {str(e)}")
        sys.exit(1)
    
    # Test specific exchange
    if args.exchange:
        print_section(f"TESTING {args.exchange.upper()} ({args.type})")
        results = test_single_exchange(args.exchange, config, logger, args.type)
        
        if results['connected']:
            print_success(f"\n{args.exchange.upper()} connection successful!")
        else:
            print_error(f"\n{args.exchange.upper()} connection failed!")
    
    # Test Telegram only
    elif args.telegram:
        telegram_results = test_telegram_bots(config, logger)
    
    # Test all
    else:
        # Test Futures
        print_section(f"FUTURES EXCHANGE ({config.FUTURES_EXCHANGE.upper()})")
        futures_result = test_single_exchange(config.FUTURES_EXCHANGE, config, logger, 'futures')
        
        # Test Spot
        if config.SPOT_EXCHANGE != config.FUTURES_EXCHANGE:
            print_section(f"SPOT EXCHANGE ({config.SPOT_EXCHANGE.upper()})")
            spot_result = test_single_exchange(config.SPOT_EXCHANGE, config, logger, 'spot')
        
        # Test Telegram
        telegram_results = test_telegram_bots(config, logger)
        
        # Summary
        print_header("TEST SUMMARY")
        print(f"✅ Tests complete")
        print(f"\n🎯 Quick Commands:")
        print(f"   python test_connection.py --exchange binance")
        print(f"   python test_connection.py --exchange kucoin --type futures")
        print(f"   python test_connection.py --telegram")
    
    print()


if __name__ == "__main__":
    main()
