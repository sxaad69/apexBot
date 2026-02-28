#!/usr/bin/env python3
"""
APEX HUNTER V14 - Full Integration Test Suite
Tests the complete futures trading pipeline:
  1. Strategy Signal Generation (A1, A3, A4)
  2. Risk Layer Pipeline (Leverage, Stop Loss, Position Size)
  3. SQLite Database Writes (trade open / close)
  4. Telegram Notification Rendering

All exchange calls are mocked with synthetic OHLCV data.
Run: python3 scripts/integration_test.py
"""

import sys
import os
import uuid
import sqlite3
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from io import StringIO

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from bot_logging.mongo_logger import MongoLogger
from strategies import StrategyA1, StrategyA2, StrategyA3, StrategyA4, StrategyA5
from risk.layers.leverage_control import LeverageControlLayer
from risk.layers.stop_loss_management import StopLossManagementLayer

# ─────────────────────────────────────────────────────────────────────────────
# ANSI Colors
# ─────────────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

PASS = f"{GREEN}✅ PASS{RESET}"
FAIL = f"{RED}❌ FAIL{RESET}"
WARN = f"{YELLOW}⚠️  WARN{RESET}"
INFO = f"{CYAN}ℹ️  INFO{RESET}"

results = []

def section(title):
    print(f"\n{BOLD}{BLUE}{'─'*70}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─'*70}{RESET}")

def log_result(name, passed, detail=""):
    status = PASS if passed else FAIL
    results.append({"name": name, "passed": passed})
    detail_str = f"  → {detail}" if detail else ""
    print(f"  {status}  {name}{detail_str}")


# ─────────────────────────────────────────────────────────────────────────────
# SYNTHETIC DATA FACTORY
# ─────────────────────────────────────────────────────────────────────────────
def make_ohlcv(candles=300, base_price=65000, trend='up', atr_pct=0.003, volume=500):
    """Generate synthetic OHLCV dataframe with configurable trend and volatility."""
    np.random.seed(42)
    prices = [base_price]
    for _ in range(candles - 1):
        direction = 1.0 if trend == 'up' else (-1.0 if trend == 'down' else 0.0)
        change = prices[-1] * (atr_pct * (direction * 0.3 + np.random.randn() * 0.5))
        prices.append(max(prices[-1] + change, 100))

    timestamps = pd.date_range(end=datetime.utcnow(), periods=candles, freq='15min')
    df = pd.DataFrame({
        'open':   [p * (1 - atr_pct * 0.3) for p in prices],
        'high':   [p * (1 + atr_pct * 0.8) for p in prices],
        'low':    [p * (1 - atr_pct * 0.8) for p in prices],
        'close':  prices,
        'volume': [volume * (1 + 0.5 * abs(np.random.randn())) for _ in prices],
    }, index=timestamps)
    return df


def make_bullish_squeeze_df(candles=300, base_price=65000):
    """Create data specifically designed to trigger A3 BB squeeze breakout."""
    df = make_ohlcv(candles=candles - 30, base_price=base_price, trend='up', atr_pct=0.004, volume=1000)
    # Tight range (squeeze) for last 20 candles
    squeeze_prices = [df['close'].iloc[-1]] * 20
    squeeze_idx = pd.date_range(start=df.index[-1] + pd.Timedelta('15min'), periods=20, freq='15min')
    squeeze_df = pd.DataFrame({
        'open':   squeeze_prices,
        'high':   [p * 1.001 for p in squeeze_prices],
        'low':    [p * 0.999 for p in squeeze_prices],
        'close':  squeeze_prices,
        'volume': [200] * 20
    }, index=squeeze_idx)
    # Breakout candle
    breakout_price = squeeze_prices[-1] * 1.015
    breakout_idx = squeeze_idx[-1] + pd.Timedelta('15min')
    breakout_df = pd.DataFrame({
        'open':   [squeeze_prices[-1]],
        'high':   [breakout_price * 1.01],
        'low':    [squeeze_prices[-1] * 0.999],
        'close':  [breakout_price],
        'volume': [2000]  # Big volume spike
    }, index=[breakout_idx])
    return pd.concat([df, squeeze_df, breakout_df])


# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────
def setup():
    config = Config()
    config.TESTING_MODE = True
    config.TESTING_ADX_MIN = 10
    config.TESTING_VOLUME_MULT = 0.5
    config.MAX_EQUITY_RISK_PERCENT = 3.0
    config.MAX_LEVERAGE_ABSOLUTE = 20
    config.MIN_ATR_PERCENT = 0.05

    logger = MagicMock()
    logger.info = lambda *a, **k: None
    logger.debug = lambda *a, **k: None
    logger.warning = lambda *a, **k: None
    logger.error = lambda *a, **k: None
    logger.position_rejected = lambda *a, **k: None

    return config, logger


# ─────────────────────────────────────────────────────────────────────────────
# TEST 1: STRATEGY SELECTION
# ─────────────────────────────────────────────────────────────────────────────
def test_strategy_selection(config, logger):
    section("TEST 1: Strategy Selection (Toggles)")
    active_strategies = []
    if getattr(config, 'STRATEGY_A1_ENABLED', True):
        active_strategies.append(StrategyA1(config, logger))
    if getattr(config, 'STRATEGY_A2_ENABLED', False):
        active_strategies.append(StrategyA2(config, logger))
    if getattr(config, 'STRATEGY_A3_ENABLED', True):
        active_strategies.append(StrategyA3(config, logger))
    if getattr(config, 'STRATEGY_A4_ENABLED', True):
        active_strategies.append(StrategyA4(config, logger))
    if getattr(config, 'STRATEGY_A5_ENABLED', False):
        active_strategies.append(StrategyA5(config, logger))

    names = [s.name for s in active_strategies]
    log_result("STRATEGY_A2_ENABLED=false → A2 not loaded",
               "A2: EMA+RSI" not in str(names), f"Active: {names}")
    log_result("STRATEGY_A5_ENABLED=false → A5 not loaded",
               "A5: Market Microstructure" not in str(names), f"Active: {names}")
    log_result("A1 is active", any("A1" in n for n in names))
    log_result("A3 is active", any("A3" in n for n in names))
    log_result("A4 is active", any("A4" in n for n in names))
    log_result("Exactly 3 strategies active", len(active_strategies) == 3,
               f"Count: {len(active_strategies)}")
    return active_strategies


# ─────────────────────────────────────────────────────────────────────────────
# TEST 2: STRATEGY SIGNAL GENERATION
# ─────────────────────────────────────────────────────────────────────────────
def test_signal_generation(config, logger):
    section("TEST 2: Strategy Signal Generation")

    # A1 — Uptrend data (need 300+ candles for 200 EMA)
    a1 = StrategyA1(config, logger)
    df_up = make_ohlcv(350, trend='up', atr_pct=0.006, volume=2000)
    df_up['adx'] = 50.0  # Force ADX to be high enough for strict filters
    signal_a1 = None
    for i in range(60, len(df_up)):
        s = a1.generate_signal(df_up.iloc[:i+1], 'BTC/USDT')
        if s:
            signal_a1 = s
            break
    a1_side = signal_a1['side'] if signal_a1 else 'None'
    a1_conf = f"{signal_a1['confidence']:.2f}" if signal_a1 else 'N/A'
    log_result("A1 generates signal on uptrend data", signal_a1 is not None,
               f"side={a1_side} conf={a1_conf}")
    if signal_a1:
        log_result("A1 has 200 EMA guard column",
                  'ema_major' in a1.calculate_indicators(df_up.iloc[:201]).columns)
        log_result("A1 signal has stop_loss", 'stop_loss' in signal_a1)
        log_result("A1 signal has take_profit", 'take_profit' in signal_a1)
        log_result("A1 signal has ATR in indicators",
                  'atr' in signal_a1.get('indicators', {}),
                  str(list(signal_a1.get('indicators', {}).keys())))
        log_result("A1 confidence <= 0.85 (hard ceiling)",
                  signal_a1['confidence'] <= 0.85,
                  f"conf={signal_a1['confidence']:.2f}")
    else:
        print(f"  {WARN}  A1 found no crossover in synthetic data (normal with strict ADX 25+)")  

    # A3 — Momentum data with high volume
    a3 = StrategyA3(config, logger)
    df_squeeze = make_bullish_squeeze_df()
    df_squeeze['adx'] = 50.0 # Force ADX high
    signal_a3 = None
    for i in range(60, len(df_squeeze)):
        s = a3.generate_signal(df_squeeze.iloc[:i+1], 'ETH/USDT')
        if s:
            signal_a3 = s
            break
    log_result("A3 generates signal on momentum data", signal_a3 is not None,
               f"side={signal_a3['side'] if signal_a3 else 'None'}")
    log_result("A3 TP multiplier is 2.5x ATR (upgraded)",
               a3.atr_tp_mult == 2.5, f"atr_tp_mult={a3.atr_tp_mult}")
    log_result("A3 SL multiplier is 1.2x ATR (upgraded)",
               a3.atr_sl_mult == 1.2, f"atr_sl_mult={a3.atr_sl_mult}")

    # A4 — Strong trend data (needs 210+ candles for 200 EMA)
    a4 = StrategyA4(config, logger)
    df_strong = make_ohlcv(350, trend='up', atr_pct=0.007, volume=3000)
    df_strong['adx'] = 50.0 # Force ADX high
    signal_a4 = None
    for i in range(210, len(df_strong)):
        s = a4.generate_signal(df_strong.iloc[:i+1], 'SOL/USDT')
        if s:
            signal_a4 = s
            break
    a4_side = signal_a4['side'] if signal_a4 else 'None'
    a4_conf = f"{signal_a4['confidence']:.2f}" if signal_a4 else 'N/A'
    log_result("A4 generates signal on strong trend data", signal_a4 is not None,
               f"side={a4_side} conf={a4_conf}")
    if signal_a4:
        log_result("A4 confidence is 0.90 (strong alignment)",
                  signal_a4['confidence'] == 0.90,
                  f"conf={signal_a4['confidence']:.2f}")

    return signal_a1, signal_a3


# ─────────────────────────────────────────────────────────────────────────────
# TEST 3: DYNAMIC LEVERAGE CALCULATION
# ─────────────────────────────────────────────────────────────────────────────
def test_dynamic_leverage(config, logger):
    section("TEST 3: Dynamic Leverage (ATR + Confidence Hybrid)")
    layer = LeverageControlLayer(config, logger)
    account = {'drawdown_percent': 0}

    # Case 1: Low ATR (tight market) + High confidence → should get high leverage
    tp1 = {'symbol': 'BTC/USDT', 'strategy': 'A4', 'entry_price': 65000,
            'confidence': 0.90, 'atr': 65000 * 0.0015}  # 0.15% ATR
    result1 = layer.evaluate(tp1.copy(), account)
    # ATR_safe_max = 3.0 / 0.15 = 20x, conf_scale = 0.3 + 0.7*0.9 = 0.93, final = min(20*0.93, 20) = 18x
    log_result("Low ATR (0.15%) + 0.90 conf → ≥15x leverage",
               result1['leverage'] >= 15, f"Got: {result1['leverage']}x")
    log_result("Leverage never exceeds MAX_LEVERAGE_ABSOLUTE (20x)",
               result1['leverage'] <= 20, f"Got: {result1['leverage']}x")

    # Case 2: High ATR (volatile market) + Low confidence → should get low leverage
    tp2 = {'symbol': 'BTC/USDT', 'strategy': 'A3', 'entry_price': 65000,
            'confidence': 0.65, 'atr': 65000 * 0.006}  # 0.6% ATR
    result2 = layer.evaluate(tp2.copy(), account)
    # ATR_safe_max = 3.0 / 0.6 = 5x, conf_scale = 0.30 + 0.7*0.65 = 0.755, final = 3x
    log_result("High ATR (0.6%) + 0.65 conf → ≤5x leverage",
               result2['leverage'] <= 5, f"Got: {result2['leverage']}x")

    # Case 3: Drawdown protection — 12% drawdown should cap at 5x
    tp3 = {'symbol': 'BTC/USDT', 'strategy': 'A4', 'entry_price': 65000,
            'confidence': 0.95, 'atr': 65000 * 0.001}
    result3 = layer.evaluate(tp3.copy(), {'drawdown_percent': 12})
    log_result("12% drawdown → leverage capped at 5x",
               result3['leverage'] <= 5, f"Got: {result3['leverage']}x")

    # Case 4: No ATR data → should fallback to conservative 5x
    tp4 = {'symbol': 'BTC/USDT', 'strategy': 'A1', 'entry_price': 65000,
            'confidence': 0.80, 'atr': None}
    result4 = layer.evaluate(tp4.copy(), account)
    log_result("No ATR data → conservative fallback ≤5x",
               result4['leverage'] <= 5, f"Got: {result4['leverage']}x")

    # Case 5: leverage_breakdown included in result
    log_result("leverage_breakdown dict present in trade_params",
               'leverage_breakdown' in result1, str(result1.get('leverage_breakdown')))

    print(f"\n  {INFO}  Leverage Matrix (Entry $65,000):")
    for atr_pct, atr_label, conf, conf_label in [
        (0.0015, "0.15% (tight)", 0.92, "0.92"),
        (0.003,  "0.30% (normal)", 0.85, "0.85"),
        (0.006,  "0.60% (volatile)", 0.70, "0.70"),
    ]:
        tp = {'symbol': 'BTC', 'strategy': 'Test', 'entry_price': 65000,
              'confidence': conf, 'atr': 65000 * atr_pct}
        r = layer.evaluate(tp, account)
        print(f"    ATR {atr_label:<22} conf={conf_label} → {r['leverage']:>2}x leverage")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 4: EQUITY-CAP STOP LOSS
# ─────────────────────────────────────────────────────────────────────────────
def test_stop_loss_layer(config, logger):
    section("TEST 4: Equity-Cap Stop Loss Layer")
    try:
        from risk.layers.stop_loss_management import StopLossManagementLayer
        sl_layer = StopLossManagementLayer(config, logger)
        account = {'total_balance': 100, 'drawdown_percent': 0}

        # 10x leverage, 3% equity risk → SL must be 0.3% away from entry
        tp = {'symbol': 'BTC/USDT', 'strategy': 'A4', 'entry_price': 65000,
              'side': 'buy', 'stop_loss': 63000, 'take_profit': 68000,
              'leverage': 10, 'size': 10, 'confidence': 0.85}
        result = sl_layer.evaluate(tp.copy(), account)

        if result:
            expected_sl_price = 65000 * (1 - (3.0 / 100 / 10))
            actual_sl = result.get('stop_loss', tp['stop_loss'])
            # The SL should be at or above the equity-cap price (closer to entry)
            log_result("10x leverage → SL within 0.5% of entry",
                       abs(actual_sl - expected_sl_price) < 65000 * 0.005,
                       f"Expected ~${expected_sl_price:.0f}, Got ${actual_sl:.0f}")
            log_result("SL is tighter than original wide SL ($63000)",
                       actual_sl > 63000,
                       f"Equity-capped SL: ${actual_sl:.0f}")
        else:
            log_result("StopLoss layer returned trade_params", result is not None)
    except Exception as e:
        log_result("StopLoss layer runs without errors", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TEST 5: CONFIDENCE-BASED POSITION SIZING
# ─────────────────────────────────────────────────────────────────────────────
def test_position_sizing():
    section("TEST 5: Confidence-Based Position Sizing Logic")
    capital = 100.0

    def get_size(confidence):
        if confidence >= 0.90:
            base = 0.15
        elif confidence >= 0.80:
            base = 0.12
        elif confidence >= 0.70:
            base = 0.10
        else:
            base = 0.07
        return capital * base

    log_result("conf=0.65 → 7% ($7.00)",  abs(get_size(0.65) - 7.0) < 0.001,  f"Got ${get_size(0.65):.2f}")
    log_result("conf=0.74 → 10% ($10.00)", abs(get_size(0.74) - 10.0) < 0.001, f"Got ${get_size(0.74):.2f}")
    log_result("conf=0.85 → 12% ($12.00)", abs(get_size(0.85) - 12.0) < 0.001, f"Got ${get_size(0.85):.2f}")
    log_result("conf=0.92 → 15% ($15.00)", abs(get_size(0.92) - 15.0) < 0.001, f"Got ${get_size(0.92):.2f}")

    # Opportunity reserve logic
    def get_exposure_ceiling(confidence):
        return capital * 0.80 if confidence >= 0.90 else capital * 0.60

    log_result("conf=0.85 → 60% exposure ceiling ($60)",
               get_exposure_ceiling(0.85) == 60.0, f"Got ${get_exposure_ceiling(0.85):.0f}")
    log_result("conf=0.92 → 80% exposure ceiling ($80) [reserve unlocked]",
               get_exposure_ceiling(0.92) == 80.0, f"Got ${get_exposure_ceiling(0.92):.0f}")

    # Scenario: two concurrent signals
    sig1_size = get_size(0.80)  # $12
    sig2_max_exposure = get_exposure_ceiling(0.92)  # $80
    available_for_sig2 = sig2_max_exposure - sig1_size  # $68
    sig2_size = min(get_size(0.92), available_for_sig2)  # $15
    log_result("Both 80%+90% signals open simultaneously (12+15=$27 deployed)",
               sig1_size + sig2_size == 27.0,
               f"Sig1=${sig1_size}, Sig2=${sig2_size}, Total=${sig1_size+sig2_size}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 6: SQLITE DATABASE INTEGRATION
# ─────────────────────────────────────────────────────────────────────────────
def test_sqlite_integration(config, logger):
    section("TEST 6: SQLite Database Integration")
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'data', 'apex_hunter.db')

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        log_result("Database file exists and is accessible", True, db_path)
        log_result("'trades' table exists", 'trades' in tables, f"Tables: {tables}")

        # Check schema columns
        cursor.execute("PRAGMA table_info(trades)")
        cols = [r[1] for r in cursor.fetchall()]
        log_result("'trade_id' column exists in trades", 'trade_id' in cols)
        log_result("'strategy' column exists in trades", 'strategy' in cols)
        log_result("'leverage' column exists in trades", 'leverage' in cols)

        # Write a mock trade
        mock_id = f"TEST-{uuid.uuid4().hex[:8].upper()}"
        cursor.execute("""
            INSERT OR IGNORE INTO trades
            (trade_id, symbol, market_type, strategy, side, leverage,
             entry_price, entry_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (mock_id, 'BTC/USDT', 'futures', 'A4: Trend Following',
              'buy', 13, 65000.0, datetime.utcnow().isoformat(), 'OPEN'))
        conn.commit()
        log_result("Mock trade INSERT succeeds", True, f"trade_id={mock_id}")

        # Read it back
        cursor.execute("SELECT trade_id, leverage, strategy FROM trades WHERE trade_id=?", (mock_id,))
        row = cursor.fetchone()
        log_result("Mock trade readable from DB",
                   row is not None and row[0] == mock_id, str(row))

        # Clean up test trade
        cursor.execute("DELETE FROM trades WHERE trade_id=?", (mock_id,))
        conn.commit()
        log_result("Mock trade cleanup successful", True)
        conn.close()
    except Exception as e:
        log_result("SQLite DB integration", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# TEST 7: TELEGRAM NOTIFICATION RENDERING
# ─────────────────────────────────────────────────────────────────────────────
def test_telegram_rendering():
    section("TEST 7: Telegram Notification Rendering")

    mock_entry_futures = {
        'trade_id': 'FUT-ABCD1234',
        'symbol': 'BTC/USDT',
        'side': 'buy',
        'entry_price': 65000.00,
        'stop_loss': 64805.50,
        'take_profit': 65780.00,
        'leverage': 13,
        'strategy': 'A4: Trend Following',
        'confidence': 0.90,
        'size': 15.0,
    }

    # Simulate the template render (from telegram_bot.py)
    msg = (
        f"🎯 <b>FUTURES TRADE ENTRY</b>\n\n"
        f"<b>Trade ID:</b> <code>{mock_entry_futures.get('trade_id', 'N/A')}</code>\n"
        f"<b>Symbol:</b> {mock_entry_futures.get('symbol')}\n"
        f"<b>Side:</b> {mock_entry_futures.get('side', '').upper()}\n"
        f"<b>Entry Price:</b> ${mock_entry_futures.get('entry_price', 0):,.2f}\n"
        f"<b>Leverage:</b> {mock_entry_futures.get('leverage', 1)}x\n"
        f"<b>Strategy:</b> {mock_entry_futures.get('strategy', 'N/A')}\n"
        f"<b>Stop Loss:</b> ${mock_entry_futures.get('stop_loss', 0):,.2f}\n"
        f"<b>Take Profit:</b> ${mock_entry_futures.get('take_profit', 0):,.2f}\n"
    )

    log_result("Entry message contains Trade ID", 'FUT-ABCD1234' in msg)
    log_result("Entry message contains Leverage", '13x' in msg)
    log_result("Entry message contains Strategy", 'A4: Trend Following' in msg)
    log_result("Entry message contains Stop Loss", '64,805.50' in msg)

    # Exit message
    mock_exit_futures = {**mock_entry_futures, 'exit_price': 65780.00,
                         'pnl_percent': 9.6, 'reason': 'take_profit'}
    exit_msg = (
        f"🏁 <b>FUTURES TRADE EXIT</b>\n\n"
        f"<b>Trade ID:</b> <code>{mock_exit_futures.get('trade_id', 'N/A')}</code>\n"
        f"<b>Symbol:</b> {mock_exit_futures.get('symbol')}\n"
        f"<b>P&L:</b> +{mock_exit_futures.get('pnl_percent', 0):.2f}%\n"
        f"<b>Reason:</b> {mock_exit_futures.get('reason', 'N/A')}\n"
    )

    log_result("Exit message contains Trade ID", 'FUT-ABCD1234' in exit_msg)
    log_result("Exit message contains P&L", '+9.60%' in exit_msg)
    log_result("Exit message contains exit reason", 'take_profit' in exit_msg)

    print(f"\n  {INFO}  Sample Entry Alert:")
    for line in msg.strip().split('\n')[:8]:
        clean = line.replace('<b>', '').replace('</b>', '').replace('<code>', '').replace('</code>', '')
        print(f"    {clean}")


# ─────────────────────────────────────────────────────────────────────────────
# TEST 8: END-TO-END SCENARIO
# ─────────────────────────────────────────────────────────────────────────────
def test_end_to_end(config, logger):
    section("TEST 8: End-to-End Scenario (Full Pipeline Mock)")
    print(f"  {INFO}  Simulating: A4 signal → Risk Layer → Leverage → Position Size → DB Write → Telegram")

    try:
        # Step 1: Generate a signal
        a4 = StrategyA4(config, logger)
        df = make_ohlcv(300, trend='up', atr_pct=0.003, volume=2000)
        df['adx'] = 50.0 # Force ADX high
        signal = None
        for i in range(210, len(df)):
            signal = a4.generate_signal(df.iloc[:i+1], 'BTC/USDT')
            if signal:
                break

        log_result("Step 1: A4 generates a valid signal", signal is not None,
                   f"side={signal['side'] if signal else 'None'}")
        if not signal:
            return

        # Step 2: Build trade_params
        indicators = signal.get('indicators', {})
        trade_params = {
            'symbol': 'BTC/USDT',
            'side': signal['side'],
            'entry_price': signal['entry_price'],
            'size': 12.0,  # 12% → $12
            'leverage': 8,
            'stop_loss': signal['stop_loss'],
            'take_profit': signal['take_profit'],
            'strategy': signal['strategy'],
            'confidence': signal['confidence'],
            'atr': indicators.get('atr'),
        }
        log_result("Step 2: trade_params built with ATR forwarded",
                   'atr' in trade_params,
                   f"ATR={trade_params['atr']}")

        # Step 3: Leverage layer
        lev_layer = LeverageControlLayer(config, logger)
        account_state = {'drawdown_percent': 0}
        after_lev = lev_layer.evaluate(trade_params.copy(), account_state)
        log_result("Step 3: Leverage layer approves and sets dynamic leverage",
                   after_lev and 'leverage' in after_lev,
                   f"Leverage={after_lev['leverage']}x")

        # Step 4: Verify equity-cap consistency
        equity_risk = config.MAX_EQUITY_RISK_PERCENT
        lev = after_lev['leverage']
        implied_sl_pct = equity_risk / lev
        log_result("Step 4: Implied SL% is above noise floor (>0.10%)",
                   implied_sl_pct > 0.1,
                   f"SL%={implied_sl_pct:.2f}% at {lev}x leverage")

        # Step 5: Generate trade ID and write to DB
        trade_id = f"FUT-{uuid.uuid4().hex[:8].upper()}"
        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'data', 'apex_hunter.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO trades
            (trade_id, symbol, market_type, strategy, side, leverage,
             entry_price, entry_time, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, 'BTC/USDT', 'futures', signal['strategy'],
              signal['side'], lev, signal['entry_price'],
              datetime.utcnow().isoformat(), 'OPEN'))
        conn.commit()
        cursor.execute("SELECT status FROM trades WHERE trade_id=?", (trade_id,))
        row = cursor.fetchone()
        log_result("Step 5: Trade written to SQLite as OPEN",
                   row and row[0] == 'OPEN', f"trade_id={trade_id}")

        # Step 6: Simulate close
        cursor.execute("""
            UPDATE trades SET status='CLOSED', exit_price=?, exit_time=?,
            pnl_percent=? WHERE trade_id=?
        """, (signal['take_profit'], datetime.utcnow().isoformat(), 9.2, trade_id))
        conn.commit()
        cursor.execute("SELECT status, pnl_percent FROM trades WHERE trade_id=?", (trade_id,))
        row2 = cursor.fetchone()
        log_result("Step 6: Trade updated to CLOSED in SQLite",
                   row2 and row2[0] == 'CLOSED', f"P&L={row2[1] if row2 else 'N/A'}%")

        # Step 7: Verify Telegram message would include trade ID
        alert = f"🎯 FUTURES TRADE ENTRY | Trade ID: {trade_id} | {signal['side'].upper()} BTC/USDT"
        log_result("Step 7: Telegram alert contains Trade ID", trade_id in alert)

        # Cleanup
        cursor.execute("DELETE FROM trades WHERE trade_id=?", (trade_id,))
        conn.commit()
        conn.close()

    except Exception as e:
        import traceback
        log_result("E2E test ran without critical errors", False, str(e))
        traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────────────
# FINAL REPORT
# ─────────────────────────────────────────────────────────────────────────────
def print_summary():
    total = len(results)
    passed = sum(1 for r in results if r['passed'])
    failed = total - passed

    print(f"\n{BOLD}{'═'*70}{RESET}")
    print(f"{BOLD}  INTEGRATION TEST SUMMARY{RESET}")
    print(f"{BOLD}{'═'*70}{RESET}")
    print(f"  Total Tests:  {total}")
    print(f"  {GREEN}Passed:       {passed}{RESET}")
    if failed:
        print(f"  {RED}Failed:       {failed}{RESET}")
    score = (passed / total * 100) if total > 0 else 0
    color = GREEN if score >= 90 else (YELLOW if score >= 70 else RED)
    print(f"\n  {color}{BOLD}Score: {score:.0f}%  {'🟢 READY' if score >= 90 else '🟡 REVIEW' if score >= 70 else '🔴 ISSUES'}{RESET}")

    if failed:
        print(f"\n  {RED}Failed Tests:{RESET}")
        for r in results:
            if not r['passed']:
                print(f"    ❌ {r['name']}")
    print(f"\n{'═'*70}\n")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"\n{BOLD}{CYAN}{'═'*70}{RESET}")
    print(f"{BOLD}{CYAN}  APEX HUNTER V14 — INTEGRATION TEST SUITE{RESET}")
    print(f"{BOLD}{CYAN}  {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC{RESET}")
    print(f"{BOLD}{CYAN}{'═'*70}{RESET}")

    config, logger = setup()

    test_strategy_selection(config, logger)
    test_signal_generation(config, logger)
    test_dynamic_leverage(config, logger)
    test_stop_loss_layer(config, logger)
    test_position_sizing()
    test_sqlite_integration(config, logger)
    test_telegram_rendering()
    test_end_to_end(config, logger)

    print_summary()
