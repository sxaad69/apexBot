import os
import sys
import time
import json
import sqlite3
import pandas as pd
import numpy as np
import concurrent.futures
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

# Add parent directory to path to import bot modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.config import Config
    from exchange.ccxt_client import CCXTExchangeClient
    from bot_logging.logger import Logger
    from strategies.strategy_a1 import StrategyA1
    from strategies.strategy_a2 import StrategyA2
    from strategies.strategy_a3 import StrategyA3
    from strategies.strategy_a4 import StrategyA4
    from strategies.strategy_a5 import StrategyA5
    from strategies.strategy_a6 import StrategyA6
    from risk.risk_manager import RiskManager
except ImportError as e:
    print(f"❌ Error: Could not import bot modules: {e}")
    sys.exit(1)

# --- COLORS ---
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

# --- CACHE ---
CACHE_DIR = "data/cache"
os.makedirs(CACHE_DIR, exist_ok=True)

def get_discovery_cache_path(date_str: str) -> str:
    return os.path.join(CACHE_DIR, f"discovery_{date_str}.json")

# --- FORENSIC WRAPPERS ---

class ForensicRiskManager(RiskManager):
    """Intercepts rejection to identify specific layer IDs"""
    def evaluate_detailed(self, trade_params: Dict, account_state: Dict) -> Dict:
        approved_params = trade_params.copy()
        for i, layer in enumerate(self.layers):
            res = layer.evaluate(approved_params, account_state)
            if res is None:
                return {
                    "approved": False, 
                    "layer_id": i + 1, 
                    "layer_name": layer.__class__.__name__
                }
            approved_params = res
        return {"approved": True, "params": approved_params}

# --- TOOLS ---

def resample_ohlcv(df_in: pd.DataFrame, interval: str = '5min') -> pd.DataFrame:
    """Converts 1m data to bot-parity 5m candles"""
    df = df_in.copy()
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    resampled = df.resample(interval, on='timestamp').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }).dropna().reset_index()
    # Convert back to unix ms for strategy compatibility
    resampled['timestamp'] = resampled['timestamp'].astype(np.int64) // 10**6
    return resampled

def simulate_lifecycle(df_entry_onwards: pd.DataFrame, side: str, entry_price: float, sl: float, tp: float):
    """Simulates moving through candles until SL or TP hit"""
    for _, row in df_entry_onwards.iterrows():
        l, h = row['low'], row['high']
        
        if side == 'buy':
            if l <= sl: return "SL", sl, ((sl - entry_price) / entry_price * 100)
            if h >= tp: return "TP", tp, ((tp - entry_price) / entry_price * 100)
        else: # sell
            if h >= sl: return "SL", sl, ((entry_price - sl) / entry_price * 100)
            if l <= tp: return "TP", tp, ((entry_price - tp) / entry_price * 100)
            
    # If day ends without hit
    if len(df_entry_onwards) == 0:
        return "EXPIRED", entry_price, 0.0
    final_price = df_entry_onwards.iloc[-1]['close']
    roi = ((final_price - entry_price) / entry_price * 100) if side == 'buy' else ((entry_price - final_price) / entry_price * 100)
    return "EXPIRED", final_price, roi

# --- DISCOVERY ---

def fetch_single_symbol_perf(exchange, symbol, since):
    try:
        ohlcv = exchange.exchange.fetch_ohlcv(symbol, '1d', since=since, limit=1)
        if not ohlcv: return None
        c = ohlcv[0]
        o, h, l, cl, vol = c[1], c[2], c[3], c[4], c[5]
        return {
            'symbol': symbol,
            'gainer_score': (h - o) / o * 100,
            'loser_score': (o - l) / o * 100,
            'volume_usdt': vol * cl
        }
    except: return None

def discover_elite_movers(exchange, date_str: str, top_n: int = 5):
    cache_path = get_discovery_cache_path(date_str)
    if os.path.exists(cache_path):
        with open(cache_path, 'r') as f:
            print(f"📦 {GREEN}Loading Movers from Cache...{RESET}")
            data = json.load(f)
            return data['gainers'], data['losers']

    print(f"🔍 {CYAN}Discovery Mode:{RESET} Parallel Scan of Futures Markets ({date_str})...")
    start_dt = datetime.strptime(date_str, "%Y-%m-%d")
    since = int(start_dt.timestamp() * 1000)
    
    markets = exchange.get_markets()
    symbols = [s for s in markets.keys() if markets[s]['type'] == 'swap' or markets[s].get('future')]
    
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(fetch_single_symbol_perf, exchange, s, since) for s in symbols]
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            res = f.result()
            if res: results.append(res)
            if (i+1) % 50 == 0: print(f"   Scanned {i+1}/{len(symbols)} symbols...", end='\r')

    # Tag sides for auditing
    gainers = sorted(results, key=lambda x: x['gainer_score'], reverse=True)[:top_n]
    for g in gainers: g['audit_side'] = 'LONG'
    
    losers = sorted(results, key=lambda x: x['loser_score'], reverse=True)[:top_n]
    for l in losers: l['audit_side'] = 'SHORT'
    
    with open(cache_path, 'w') as f:
        json.dump({'gainers': gainers, 'losers': losers}, f)
        
    return gainers, losers

# --- SIMULATION ---

def run_precision_replay(config, logger, exchange, movers, date_str: str, sim_capital: float = 10000.0):
    print(f"\n🧬 {CYAN}Active Replay (Precision Mode):{RESET} Bot vs Market Reality")
    print(f"💰 {BOLD}Simulation Capital:{RESET} {sim_capital} USDT")
    
    risk = ForensicRiskManager(config, logger)
    strats = [
        StrategyA1(config, logger),
        StrategyA2(config, logger),
        StrategyA3(config, logger),
        StrategyA4(config, logger),
        StrategyA5(config, logger), # Synthetic Mode
        StrategyA6(config, logger)  # Synthetic Mode
    ]
    
    # Account State (Critical Fix: matches position_sizing.py naming)
    account_state = {
        'available_balance': sim_capital, 
        'total_balance': sim_capital,
        'drawdown_percent': 0, 
        'open_positions_count': 0, 
        'current_positions': []
    }
    
    since = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000)
    all_narratives = []

    for item in movers:
        symbol = item['symbol']
        # Identify if we are auditing the Gainer potential (Long) or Loser potential (Short)
        # item['audit_side'] is added in discover_elite_movers
        side_type = item.get('audit_side', 'LONG')
        potential = item['gainer_score'] if side_type == 'LONG' else item['loser_score']
        
        print(f"\n📼 {BOLD}{symbol}{RESET} ({side_type} Potential: {potential:.2f}%)")
        
        # 1. Data & Resampling
        cache_path = os.path.join(CACHE_DIR, f"{symbol.replace('/', '_')}_{date_str}_1m.csv")
        if os.path.exists(cache_path):
            df_1m = pd.read_csv(cache_path)
            print(f"   📀 Loaded 1m tape.")
        else:
            print(f"   📥 Fetching 1m tape...")
            ohlcv = []
            curr = since
            for _ in range(2):
                batch = exchange.exchange.fetch_ohlcv(symbol, '1m', since=curr, limit=1000)
                if not batch: break
                ohlcv.extend(batch)
                curr = batch[-1][0] + 60000
                time.sleep(0.5)
            df_1m = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1m.to_csv(cache_path, index=False)
            
        df_5m = resample_ohlcv(df_1m)
        print(f"   🕒 Resampled to 5m ({len(df_5m)} bars)")

        # 2. Replay
        found_hit = False
        for i in range(50, len(df_5m)):
            window = df_5m.iloc[:i+1]
            price = df_5m.iloc[i]['close']
            ts_str = datetime.fromtimestamp(df_5m.iloc[i]['timestamp']/1000).strftime('%H:%M')
            
            for s in strats:
                # Synthetic Mocking for A5/A6
                if s.name == "A6: Orderbook WSS":
                    imb = 0.50 if side_type == 'LONG' else -0.50
                    s.latest_orderbooks[symbol] = {
                        'bids': [[price * 1.001, 10000000]] if side_type == 'LONG' else [[price * 0.999, 1000]],
                        'asks': [[price * 0.999, 10000000]] if side_type == 'SHORT' else [[price * 1.001, 1000]]
                    }
                elif s.name == "A5: Market Microstructure":
                    # Mock order book analyze and whale detect
                    imb = 0.50 if side_type == 'LONG' else -0.50
                    def mock_analyze(sym, mt): return imb
                    def mock_detect(sym, mt): return {'count': 5, 'buy_pressure': 5 if side_type=='LONG' else 0, 'sell_pressure': 5 if side_type=='SHORT' else 0, 'net_pressure': 5 if side_type=='LONG' else -5, 'total_value': 100000}
                    s.analyze_order_book = mock_analyze
                    s.detect_whales = mock_detect
                
                signal = s.generate_signal(window, symbol=symbol)
                if signal and ((side_type == 'LONG' and signal['side'] == 'buy') or (side_type == 'SHORT' and signal['side'] == 'sell')):
                    v = risk.evaluate_detailed(signal, account_state)
                    
                    # 3. Lifecycle Simulation
                    signal_ts = df_5m.iloc[i]['timestamp']
                    df_1m_onwards = df_1m[df_1m['timestamp'] > signal_ts]
                    exit_reason, exit_p, roi = simulate_lifecycle(df_1m_onwards, signal['side'], signal['entry_price'], signal['stop_loss'], signal['take_profit'])
                    
                    if v['approved']:
                        verdict_str = f"{GREEN}APPROVED{RESET}"
                        layer_info = "Passed All Risk Layers"
                    else:
                        verdict_str = f"{RED}VETOED (Layer {v['layer_id']}: {v['layer_name']}){RESET}"
                        layer_info = f"Risk layer {BOLD}{v['layer_id']} ({v['layer_name']}){RESET}"
                    
                    # Exact user-requested format
                    act = "BUY" if signal['side']=='buy' else "SELL"
                    narrative = f"Strategy {BOLD}{s.name}{RESET} indicates to {BOLD}{act}{RESET} {BOLD}{symbol}{RESET} at {ts_str}. "
                    narrative += f"{layer_info} {verdict_str}. "
                    narrative += f"ROI would have been {BOLD}{roi:+.2f}%{RESET} with position open at {BOLD}{signal['entry_price']:.4f}{RESET} and exit hit at {BOLD}{exit_reason} ({exit_p:.4f}){RESET}."
                    
                    print(f"   📢 {narrative}")
                    all_narratives.append({'symbol': symbol, 'text': narrative, 'side': side_type})
                    found_hit = True
                    break
            if found_hit: break
            
        if not found_hit: print(f"   💨 {YELLOW}Missed{RESET}: Strategies remained silent during the move.")

    return all_narratives

def main():
    print("=" * 80)
    print(f"    🚀 {CYAN}ULTIMATE FORENSIC AUDITOR - TOTAL SYSTEM PACKAGE{RESET}")
    print("=" * 80)

    # Format: python scripts/winner_replay_simulator.py [DATE or 'week'] [CAPITAL]
    arg1 = sys.argv[1] if len(sys.argv) > 1 else 'yesterday'
    capital = float(sys.argv[2]) if len(sys.argv) > 2 else 100.0
    
    if arg1 == 'week':
        lookback_days = 7
        start_date = (datetime.utcnow() - timedelta(days=7))
    elif arg1 == 'yesterday' or not arg1:
        lookback_days = 1
        start_date = (datetime.utcnow() - timedelta(days=1))
    else:
        # Specific date
        lookback_days = 1
        start_date = datetime.strptime(arg1, "%Y-%m-%d")

    config = Config()
    logger = Logger(config)
    exchange = CCXTExchangeClient(config, logger)
    
    all_weekly_narratives = []
    start_time = time.time()

    for i in range(lookback_days):
        target_dt = start_date + timedelta(days=i)
        target_date_str = target_dt.strftime("%Y-%m-%d")
        
        print(f"\n📅 {BOLD}AUDITING DATE: {target_date_str}{RESET}")
        print("-" * 40)
        
        try:
            gainers, losers = discover_elite_movers(exchange, target_date_str)
            day_narratives = run_precision_replay(config, logger, exchange, gainers + losers, target_date_str, capital)
            all_weekly_narratives.extend([(target_date_str, n) for n in day_narratives])
        except Exception as e:
            print(f"⚠️ Error auditing {target_date_str}: {e}")

    end_time = time.time()
    duration = (end_time - start_time) / 60
    
    print("\n" + "=" * 80)
    print(f"🕵️  {BOLD}THE GRAND FORENSIC REPORT: {lookback_days} DAY(S) LOOKBACK{RESET}")
    print("=" * 80)
    
    if not all_weekly_narratives:
        print("   No valid signals found in this period.")
    else:
        current_date = ""
        for date_str, n in all_weekly_narratives:
            if date_str != current_date:
                print(f"\n🗓️  {BOLD}{date_str}{RESET}:")
                current_date = date_str
            print(f"  • [{n['symbol']}] {n['text']}")
    
    print(f"\n📊 {BOLD}Total Audit Duration: {duration:.1f} minutes.{RESET}")
    print("=" * 80)

if __name__ == "__main__":
    main()
