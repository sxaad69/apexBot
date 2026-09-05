"""
Configuration Management
Loads and validates environment variables and configuration settings
"""

import os
from typing import List, Optional
from dotenv import load_dotenv


class Config:
    """
    Centralized configuration management for Apex Hunter V14
    Loads settings from environment variables with validation
    """
    
    def __init__(self, env_file: Optional[str] = None):
        """
        Initialize configuration from environment variables
        
        Args:
            env_file: Path to .env file (optional)
        """
        # Load environment variables
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()
        
        self._load_configuration()
        self._validate_configuration()
    
    def _load_configuration(self):
        """Load all configuration values from environment variables"""
        
        # ===== Exchange Configuration (Multi-Exchange CCXT) =====
        self.EXCHANGE = os.getenv('EXCHANGE', 'binance').lower()
        self.EXCHANGE_ENVIRONMENT = os.getenv('EXCHANGE_ENVIRONMENT', 'testnet')
        
        # Futures and Spot exchanges
        self.FUTURES_EXCHANGE = os.getenv('FUTURES_EXCHANGE', 'kucoin').lower()
        self.SPOT_EXCHANGE = os.getenv('SPOT_EXCHANGE', 'binance').lower()
        
        # Arbitrage mode
        self.ARBITRAGE_MODE = 'select'.lower()
        
        # Legacy KuCoin-specific (backward compatibility)
        # Exchange environment is now set globally via EXCHANGE_ENVIRONMENT
        # Legacy KuCoin settings maintained for backwards compatibility
        self.KUCOIN_ENVIRONMENT = self.EXCHANGE_ENVIRONMENT
        if self.KUCOIN_ENVIRONMENT == 'testnet':
            self.KUCOIN_BASE_URL = 'https://api-sandbox-futures.kucoin.com'
        else:
            self.KUCOIN_BASE_URL = 'https://api-futures.kucoin.com'
        
        # Exchange API Credentials (Multiple Exchanges)
        self.BINANCE_API_KEY = os.getenv('BINANCE_API_KEY', '')
        self.BINANCE_API_SECRET = os.getenv('BINANCE_API_SECRET', '')
        
        self.KUCOIN_API_KEY = os.getenv('KUCOIN_API_KEY', '')
        self.KUCOIN_API_SECRET = os.getenv('KUCOIN_API_SECRET', '')
        self.KUCOIN_API_PASSPHRASE = os.getenv('KUCOIN_API_PASSPHRASE', '')
        
        self.BYBIT_API_KEY = os.getenv('BYBIT_API_KEY', '')
        self.BYBIT_API_SECRET = os.getenv('BYBIT_API_SECRET', '')
        
        self.OKX_API_KEY = os.getenv('OKX_API_KEY', '')
        self.OKX_API_SECRET = os.getenv('OKX_API_SECRET', '')
        self.OKX_API_PASSPHRASE = os.getenv('OKX_API_PASSPHRASE', '')
        
        self.GATE_API_KEY = os.getenv('GATE_API_KEY', '')
        self.GATE_API_SECRET = os.getenv('GATE_API_SECRET', '')
        
        # ===== Master Trading Controls (Triple Safety Lock) =====
        self.FUTURES_TRADING_ENABLED = 'true'.lower()
        self.SPOT_TRADING_ENABLED = 'false'.lower()
        self.ARBITRAGE_TRADING_ENABLED = 'false'.lower()
        
        # ===== Futures Trading Configuration (Primary) =====
        self.FUTURES_TRADING_ENABLED = self._str_to_bool('true')
        self.FUTURES_VIRTUAL_CAPITAL = 136.78  # Real Wallet Baseline
        self.FUTURES_POSITION_SIZE_PERCENT = float('4.0')
        self.FUTURES_MAX_LEVERAGE = int('10')
        self.FUTURES_TAKE_PROFIT_PERCENT = float('10')
        self.FUTURES_MAX_DAILY_LOSS_PERCENT = float('5')
        self.FUTURES_MAX_DRAWDOWN_PERCENT = float('70')
        self.FUTURES_MAX_OPEN_POSITIONS = int(os.getenv('FUTURES_MAX_OPEN_POSITIONS', '15'))
        # Core-fix leverage ceiling (Phase 1): 5x base, 6x only for high-confidence
        # signals (conf >= TIER_HOT_CONF). Forensics: conf rescues 3-5x but dies at 10x
        # (12.5% win); 10x raw stops get hit by wick noise (0 wins in 17 SLs).
        # FUTURES_MAX_LEVERAGE (line 83) stays as the ABSOLUTE emergency ceiling;
        # these two are the working caps used by the risk layers and entry sizing.
        self.LEV_CAP_BASE = int(os.getenv('LEV_CAP_BASE', '5'))
        self.LEV_CAP_HIGH_CONF = int(os.getenv('LEV_CAP_HIGH_CONF', '6'))

        # --- Exposure & Reserve Management ---
        # FUTURES_MAX_EXPOSURE_NORMAL: Max % of capital for standard signals (Confidence < Threshold)
        self.FUTURES_MAX_EXPOSURE_NORMAL = 0.85 
        
        # FUTURES_MAX_EXPOSURE_ELITE: Max % of capital for high-conviction signals (Confidence >= Threshold)
        self.FUTURES_MAX_EXPOSURE_ELITE = 0.95
        
        # FUTURES_OPPORTUNITY_THRESHOLD: Confidence needed to tap into the Elite reserve.
        self.FUTURES_OPPORTUNITY_THRESHOLD = 0.90

        # Market Discovery Sync
        self.FUTURES_PAIRS = 'auto'
        self.FUTURES_AUTO_TOP_N = int(os.getenv('FUTURES_AUTO_TOP_N', '1000'))  # env-overridable universe size
        self.FUTURES_AUTO_MIN_VOLUME = float('500000')
        # Comma-separated list of base symbols to exclude from discovery/A6 watch/sweeps.
        # Matching is case-insensitive on the base symbol (e.g. 'BTC' excludes BTC/USDT).
        self.FUTURES_EXCLUDE_SYMBOLS = [s.strip().upper() for s in os.getenv('FUTURES_EXCLUDE_SYMBOLS', '').split(',') if s.strip()]
        
        # ===== Full-Universe Scanning (Phase 2) =====
        self.OHLCV_BATCH_MAX = int('100')  # Max stale-candle refreshes per sweep
        self.A6_MAX_WATCH_SYMBOLS = int(os.getenv('A6_MAX_WATCH_SYMBOLS', '250'))  # Cap A6 orderbook WSS subscriptions. 400 pegged a core pre-2f51930 (GIL spin, since fixed); 150 was belt-and-braces — stepped to 250 on Sep 5 with host headroom verified (load 0.15-0.39 / 2 cores)
        
        # ===== Discovery / Concurrency (Rate-Limit Task 1.5) =====
        self.DISCOVERY_MAX_WORKERS = int('3')   # Smoother concurrency vs 5
        
        # ===== REST Cache TTLs (Rate-Limit Tasks) =====
        self.OHLCV_CACHE_LOOKAHEAD = True       # Enables candle-TTL caching
        self.OHLCV_LIMIT = int('210')           # A4 needs max 210 candles (vs 300)
        self.POSITIONS_CACHE_TTL = float('5.0')
        self.TICKER_CACHE_TTL = float('5.0')
        self.BALANCE_CACHE_TTL = float('60.0')
        self.ORDER_STATUS_CACHE_TTL = float('5.0')
        self.WHALE_CACHE_TTL = float(os.getenv('WHALE_CACHE_TTL', '300.0'))
        self.SENTINEL_TICK_SECONDS = float(os.getenv('SENTINEL_TICK_SECONDS', '0.5'))
        self.WATERMARK_PERSIST_INTERVAL = float(os.getenv('WATERMARK_PERSIST_INTERVAL', '30.0'))
        
        # ===== Global Rate-Limit Budget (Task 1.2) =====
        self.RATE_LIMIT_MAX_WEIGHT_PER_MIN = int('1500')  # Binance cap is 2400
        
        # Position Boundary Sync
        self.MIN_POSITION_SIZE = float('10.0')  # SETTING FOR TESTNET (Previous Live: 10.0)
        self.MAX_POSITION_SIZE = float('30.0')  # SETTING FOR TESTNET (Previous Live: 30.0)

        # ===== Global Synchronization (For Legacy/Risk Layers compatibility) =====
        self.INITIAL_CAPITAL = self.FUTURES_VIRTUAL_CAPITAL
        self.POSITION_SIZE_PERCENT = self.FUTURES_POSITION_SIZE_PERCENT
        self.MAX_LEVERAGE = self.FUTURES_MAX_LEVERAGE
        # Strategy-Specific Overrides (The "Breathing Room" Knobs)
        self.A5_MAX_LEVERAGE = int('3')
        self.A5_STOP_LOSS_ROE = float('10.0')
        self.A6_STOP_LOSS_ROE = float('10.0')

        # 2026-09-05 confidence-gated risk program (evidence in AGENTS.md):
        self.A6_SKIP_MID_BAND = self._str_to_bool(os.getenv('A6_SKIP_MID_BAND', 'true'))   # 0.85-0.90: -$25.56 all-time, negative at every leverage
        self.A6_PROBE_SIZE_MULTIPLIER = float(os.getenv('A6_PROBE_SIZE_MULTIPLIER', '0.5'))  # sub-0.85 band was breakeven at best (+$3.69 golden era)
        self.ELITE_STOP_LOSS_ROE = float(os.getenv('ELITE_STOP_LOSS_ROE', '15.0'))         # conf>=0.90 ROE budget -> 6x at the 2.5% stop floor
        
        # GLOBAL ROE Shield (The "Safety Guard")
        self.GLOBAL_STOP_LOSS_ROE = float('10.0')
        self.MAX_ROE_DRAWDOWN = float('10.0')
        self.MAX_EQUITY_RISK_PERCENT = float('5.0')
        # Phase-1 core fix: enforce a minimum RAW price distance to the stop.
        # At 10x the ATR stop collapses to ~0.71% raw (wick-noise distance) and
        # hits 0-for-17; 2.5% is the noise-survival floor from the SL forensics.
        # Vol-sync layers may set a wider stop; this only widens too-tight ones.
        self.MIN_RAW_STOP_PERCENT = float(os.getenv('MIN_RAW_STOP_PERCENT', '2.5'))
        self.TAKE_PROFIT_PERCENT = self.FUTURES_TAKE_PROFIT_PERCENT
        self.MAX_DAILY_LOSS_PERCENT = self.FUTURES_MAX_DAILY_LOSS_PERCENT
        self.MAX_DRAWDOWN_PERCENT = self.FUTURES_MAX_DRAWDOWN_PERCENT
        self.MAX_OPEN_POSITIONS = self.FUTURES_MAX_OPEN_POSITIONS
        
        # Parallel Execution & Safety Cooldown Configurations
        self.FUTURES_SYMBOL_COOLDOWN_MINUTES = int('15')
        # DISCOVERY_MAX_WORKERS set above (Task 1.5: 3 workers). Do NOT redefine here.
        
        # Core Mode & Currency (Synched)
        # Core Mode & Currency (Synched)
        self.TRADING_MODE = os.getenv('TRADING_MODE', 'paper').lower()
        self.BASE_CURRENCY = 'USDT'
        trading_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT'
        self.TRADING_PAIRS = [p.strip() for p in trading_pairs_str.split(',')]
        
        # Margin configuration (Phase 32)
        self.FUTURES_MARGIN_MODE = 'ISOLATED'.upper()
        
        # Risk Layer Sync (Fixes technical debt from Phase 45)
        self.MAX_LEVERAGE_ABSOLUTE = self.FUTURES_MAX_LEVERAGE

        # ===== Spot Trading Configuration =====
        self.ENABLE_SPOT_TRADING = self._str_to_bool('false')
        self.ENABLE_SPOT_LOGGER = self._str_to_bool('true')
        self.SPOT_VIRTUAL_CAPITAL = float('3.0')
        self.SPOT_POSITION_SIZE_PERCENT = float('10')
        self.SPOT_STOP_LOSS_PERCENT = float('2')
        self.SPOT_TAKE_PROFIT_PERCENT = float('4')
        self.SPOT_USE_FULL_RISK_SYSTEM = self._str_to_bool('true')
        self.SPOT_MAX_DAILY_LOSS_PERCENT = float('5')
        self.SPOT_MAX_DRAWDOWN_PERCENT = float('15')
        spot_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT'
        self.SPOT_PAIRS = [pair.strip() for pair in spot_pairs_str.split(',')]
        self.SPOT_TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.SPOT_LOG_SIGNALS = self._str_to_bool('true')
        self.SPOT_DAILY_SUMMARY = self._str_to_bool('true')
        self.SPOT_DAILY_SUMMARY_TIME = '00:00'
        
        # ===== Arbitrage Scanner Configuration =====
        self.ENABLE_ARBITRAGE_SCANNER = self._str_to_bool('true')
        self.ARBITRAGE_VIRTUAL_CAPITAL = float('3.0')
        self.ARBITRAGE_SIMPLE = self._str_to_bool('true')
        self.ARBITRAGE_TRIANGULAR = self._str_to_bool('true')
        self.ARBITRAGE_CROSS_TRIANGULAR = self._str_to_bool('false')
        self.ARBITRAGE_MIN_PROFIT_PERCENT = float('2.0')
        self.ARBITRAGE_CHECK_INTERVAL = int('30')
        self.ARBITRAGE_INCLUDE_ALL_FEES = self._str_to_bool('true')
        self.ARBITRAGE_MIN_VOLUME = float('10000')
        self.ARBITRAGE_EXCHANGES = 'binance,kucoin,bybit,okx,gate,huobi,kraken,coinbase,bitget,mexc'
        arb_pairs_str = 'BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT,XRP/USDT'
        self.ARBITRAGE_PAIRS = [pair.strip() for pair in arb_pairs_str.split(',')]
        self.ARBITRAGE_TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.ARBITRAGE_LOG_TOP_N_PER_HOUR = int('5')
        self.ARBITRAGE_DAILY_SUMMARY = self._str_to_bool('true')
        self.ARBITRAGE_DAILY_SUMMARY_TIME = '00:00'
        
        # (Legacy overrides REMOVED to prevent FUTURES_* settings from being overwritten)
        # self.POSITION_SIZE_PERCENT, self.MAX_LEVERAGE, etc are now managed in the Global section above
        
        # ===== Strategy Selection Configuration =====
        # NOTE: A3, A4, A5 enabled for TESTNET only. DISABLE for LIVE prod.
        self.STRATEGY_A1_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A2_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A3_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A4_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A1_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A2_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A3_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A4_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A5_ENABLED = self._str_to_bool('false')
        self.STRATEGY_A6_ENABLED = self._str_to_bool('true')
        self.STRATEGY_A7_ENABLED = self._str_to_bool(os.getenv('STRATEGY_A7_ENABLED', 'false'))
        self.STRATEGY_A8_ENABLED = self._str_to_bool(os.getenv('STRATEGY_A8_ENABLED', 'false'))
        self.STRATEGY_A9_ENABLED = self._str_to_bool(os.getenv('STRATEGY_A9_ENABLED', 'false'))
        self.A8_IGNITION_THRESHOLD = float(os.getenv('A8_IGNITION_THRESHOLD', '0.40'))
        self.A6_ALLOW_SHORT = self._str_to_bool('false')
        
        # Per-strategy paper mode: strategies flagged as paper generate signals
        # against live data (real orderbook/candles) but place zero orders.
        # Env-overridable so each server controls its own mix.
        self.STRATEGY_A6_PAPER = self._str_to_bool(os.getenv('STRATEGY_A6_PAPER', 'false'))
        self.STRATEGY_A7_PAPER = self._str_to_bool(os.getenv('STRATEGY_A7_PAPER', 'false'))
        self.STRATEGY_A8_PAPER = self._str_to_bool(os.getenv('STRATEGY_A8_PAPER', 'false'))
        self.STRATEGY_A9_PAPER = self._str_to_bool(os.getenv('STRATEGY_A9_PAPER', 'false'))
        # A9 replay-validated gates (2026-09-05, measured on 640 real-fill replays):
        self.A9_SKIP_CONF_BAND = self._str_to_bool(os.getenv('A9_SKIP_CONF_BAND', 'true'))       # 0.80-0.85 lost -0.38%/trade
        self.A9_MAX_EXTENSION_PCT = float(os.getenv('A9_MAX_EXTENSION_PCT', '8.0'))              # skip entries >8% off the 12h low; 0 disables
        self.A9_BRAKE_ENABLED = self._str_to_bool(os.getenv('A9_BRAKE_ENABLED', 'true'))         # pause when recent cohort win rate collapses
        self.A9_BRAKE_WINDOW = int(os.getenv('A9_BRAKE_WINDOW', '20'))
        self.A9_BRAKE_MIN_WINRATE = float(os.getenv('A9_BRAKE_MIN_WINRATE', '35.0'))
        
        # Strategy-specific Risk Overrides
        self.STRATEGY_A3_SL_ROE = float('5.0')
        
        # ===== Fee Configuration (Simulation) =====
        self.FUTURES_FEE_PERCENT = float('0.04')
        self.SPOT_FEE_PERCENT = float('0.1')
        
        # ===== Trailing Stop Configuration =====
        # ONE place to control trailing. Change these two values only.
        self.TRAILING_STOP_ACTIVATION = float('5.0')  # % profit before trailing starts
        self.TRAILING_STOP_DISTANCE = float('3.0')    # % trail distance from peak
        # Tiered trailing (B1): widen the exchange callback as a position proves it runs.
        # Tier thresholds are profit % from entry; callback rates replace the trailing order.
        # Env-overridable so testnet can exercise tiers at low thresholds without committing
        # testnet-specific values to the repo.
        self.TRAILING_TIER_1_AT = float(os.getenv('TRAILING_TIER_1_AT', '8.0'))  # above this profit → tier 1
        self.TRAILING_TIER_1_CALLBACK = float(os.getenv('TRAILING_TIER_1_CALLBACK', '5.0'))  # tier 1 callback %
        self.TRAILING_TIER_2_AT = float(os.getenv('TRAILING_TIER_2_AT', '20.0'))  # above this profit → tier 2
        self.TRAILING_TIER_2_CALLBACK = float(os.getenv('TRAILING_TIER_2_CALLBACK', '8.0'))  # tier 2 callback %
        # A1: buffer % ahead of current mark used to re-arm the exchange trailing on a
        # tier upgrade (avoids -2021 "Order would immediately trigger" when activation
        # is set at/behind the live mark).
        self.TRAILING_REARM_BUFFER = float(os.getenv('TRAILING_REARM_BUFFER', '0.5'))
        # E1: retries for the exchange trailing re-place on tier upgrade (self-heal
        # transient -2021/-2011 instead of stranding the position software-managed).
        self.TIER_REPLACE_RETRIES = int(os.getenv('TIER_REPLACE_RETRIES', '3'))
        # 2026-09-05: tier re-place no longer retries in-loop (that blocked the
        # sentinel thread ~2s/tick); one attempt per tick with this cooldown.
        self.TIER_RETRY_COOLDOWN = float(os.getenv('TIER_RETRY_COOLDOWN', '60'))
        # 2026-09-05: cap trailing activation in PRICE terms. The 15%-ROE/leverage
        # formula needs +15% price at 1x — unreachable for strategies whose winners
        # peak at +2..8% (A6 trailing exits avg +1..4%; SKR gave back +12% to the
        # SL because activation never armed). Cap applies to A6 live AND the paper
        # exit engine. 0 disables (pre-Sep-5 behavior).
        self.TRAILING_ACTIVATION_PRICE_CAP = float(os.getenv('TRAILING_ACTIVATION_PRICE_CAP', '4.0'))
        # Leverage-aware trailing (2026-08-29): the exchange callbackRate is a % of
        # PRICE, so ROE give-back = callback x leverage. A flat 3% price trail gave
        # back 21% ROE at 7x (XMR FUT-2FDE2B0F: peak +39.85% ROE, closed +18.57%).
        # Bracket the price callback by leverage so ROE give-back stays roughly
        # constant. Binance bounds: callbackRate 1.0-10.0%.
        self.TRAILING_CALLBACK_LEV_HIGH = float(os.getenv('TRAILING_CALLBACK_LEV_HIGH', '1.0'))  # lev >= 5
        self.TRAILING_CALLBACK_LEV_MID = float(os.getenv('TRAILING_CALLBACK_LEV_MID', '2.0'))    # lev 2-4
        self.TRAILING_CALLBACK_LEV_LOW = float(os.getenv('TRAILING_CALLBACK_LEV_LOW', '3.0'))    # lev 1
        # Activation is also price-based: 5% price = 35% ROE at 7x before trailing
        # even starts (XMR armed 0.7% below its peak). Arm earlier on high leverage:
        # activation = max(floor, target_roe / leverage).
        self.TRAILING_ACTIVATION_TARGET_ROE = float(os.getenv('TRAILING_ACTIVATION_TARGET_ROE', '15.0'))
        self.TRAILING_ACTIVATION_LEV_FLOOR = float(os.getenv('TRAILING_ACTIVATION_LEV_FLOOR', '2.0'))
        # C1: re-entry blacklist — consecutive SL losses before blocking the symbol
        # for the session. 2 as designed on Aug 20; 1 (set 6182127) is only safe
        # while the D4 startup seed is broken — with a working seed it ratchets
        # toward zero trading (a blocked symbol can never exit profitably and reset).
        self.FUTURES_MAX_LOSS_STREAK = int(os.getenv('FUTURES_MAX_LOSS_STREAK', '2'))
        # D4: seed only considers exits inside this window so blacklists decay
        self.SYMBOL_BLACKLIST_WINDOW_HOURS = float(os.getenv('SYMBOL_BLACKLIST_WINDOW_HOURS', '72'))
        self.TRAILING_TP_ENABLED = self._str_to_bool('true')

        # Exchange-Side Execution
        # EXCHANGE_SIDE_SL is the master switch:
        #   true  -> place exchange-side stops (native trailing where supported,
        #            hard STOP_MARKET fallback + sentinel where not)
        #   false -> bot-side sentinel layer only (legacy behavior)
        self.EXCHANGE_SIDE_SL = self._str_to_bool(os.getenv('EXCHANGE_SIDE_SL', 'false'))
        self.ENABLE_EXCHANGE_STOPS = self._str_to_bool(os.getenv('ENABLE_EXCHANGE_STOPS', 'false'))
        self.EXCHANGE_TP_ORDER_TYPE = 'TAKE_PROFIT_MARKET'
        # B2: seconds between orphaned-algo-order sweeps (cancel SL/trailing/TP on
        # symbols with no live position so they don't accumulate).
        self.ORPHAN_SWEEP_INTERVAL = float(os.getenv('ORPHAN_SWEEP_INTERVAL', '300.0'))

        # NOTE: MAX_DAILY_LOSS_PERCENT is NOT overwritten here — it must stay at
        # the value from FUTURES_MAX_DAILY_LOSS_PERCENT (line 144). A prior duplicate
        # here set it to 15%, silently inflating the daily loss limit from 5% to 15%
        # (the bug that let Aug-13's -$8.73 through without halting).
        
        # ===== Tiered Risk Management (Phase 14) =====
        self.TIERED_RISK_ENABLED = self._str_to_bool('true')
        self.NORMAL_SIGNAL_THRESHOLD = float('50.0')
        self.ELITE_SIGNAL_THRESHOLD = float('70.0')
        self.ELITE_CONFIDENCE_LEVEL = float('0.90')
        
        self.CORRELATION_THRESHOLD = float('0.7')
        self.VOLATILITY_LOOKBACK_PERIODS = int('20')
        self.MIN_LIQUIDITY_DEPTH = float('10000')
        
        # ===== Circuit Breaker Configuration =====
        # D2: consecutive-loss circuit breaker. Halts trading for TRADE_FAILURE_HALT_HOURS
        # after CONSECUTIVE_LOSSES_THRESHOLD consecutive losses (a win resets the counter).
        # Enabled by default (was false); env-overridable.
        self.ENABLE_CIRCUIT_BREAKER = self._str_to_bool(os.getenv('ENABLE_CIRCUIT_BREAKER', 'true'))
        self.TRADE_FAILURE_HALT_HOURS = float('0.5')
        self.CONSECUTIVE_LOSSES_THRESHOLD = int('5')
        self.FLASH_CRASH_THRESHOLD = float('-10')
        
        # ===== Portfolio Loss Circuit Breaker =====
        self.LOSS_CB_ENABLED = self._str_to_bool('true')
        self.LOSS_CB_PCT = float('10.0')
        self.LOSS_CB_COOLDOWN_MINUTES = int('30')

        # ===== Tier-Flip Signal System (2026-08-22) =====
        # Evidence-based tiers from 231 exchange-truth trades:
        # - conf >=0.90 x lev 4-5x = money cell (+$37.71, 58% WR) -> HOT 25%/3x
        # - session boost REMOVED at source (inflated minimum-walls = old dead zone)
        # - asia hours off (33% WR bleed, -$13.40)
        # NOTE: numeric 0.87-0.89 dead-zone rejected by replay — with boost gone
        # the band executes genuine runners; poison cause removed at source instead.
        self.TIER_HOT_CONF = float(os.getenv('TIER_HOT_CONF', '0.90'))
        self.TIER_HOT_SIZE = float(os.getenv('TIER_HOT_SIZE', '0.25'))
        self.TIER_HOT_LEV = int(os.getenv('TIER_HOT_LEV', '3'))
        self.TIER_BASE_SIZE = float(os.getenv('TIER_BASE_SIZE', '0.10'))
        self.TIER_BASE_LEV = int(os.getenv('TIER_BASE_LEV', '2'))
        self.LEV_CAP = int(os.getenv('LEV_CAP', '5'))
        self.ASIA_TRADING_ENABLED = self._str_to_bool(os.getenv('ASIA_TRADING_ENABLED', 'false'))
        self.ASIA_END_HOUR_UTC = int(os.getenv('ASIA_END_HOUR_UTC', '8'))

        # ===== Momentum Confirmation Gate (Phase 1 core fix) =====
        # A wall/imbalance signal alone no longer executes. The engine holds a
        # pending signal for up to MOMENTUM_MAX_WAIT_SECONDS and only commits if
        # price confirms >= MOMENTUM_MIN_MOVE_PCT in the signal side with volume
        # ratio >= MOMENTUM_MIN_VOL_RATIO on the next data window. Kills the
        # dead-entry bucket (24 entries / 12% win / -$8.28 this week) while
        # keeping genuine runners (fwd 8%+ = 90% win). Opt-out via .env.
        self.MOMENTUM_GATE_ENABLED = self._str_to_bool(os.getenv('MOMENTUM_GATE_ENABLED', 'false'))
        self.MOMENTUM_GATE_STRATEGIES = [s.strip().upper() for s in
                                         os.getenv('MOMENTUM_GATE_STRATEGIES', 'A6').split(',') if s.strip()]
        self.MOMENTUM_MIN_MOVE_PCT = float(os.getenv('MOMENTUM_MIN_MOVE_PCT', '0.5'))
        self.MOMENTUM_MIN_VOL_RATIO = float(os.getenv('MOMENTUM_MIN_VOL_RATIO', '1.5'))
        self.MOMENTUM_MAX_WAIT_SECONDS = float(os.getenv('MOMENTUM_MAX_WAIT_SECONDS', '900'))

        
        # ===== Telegram Integration (3 Separate Bots) =====
        # Futures Bot
        self.TELEGRAM_FUTURES_ENABLED = self._str_to_bool('true')
        self.TELEGRAM_FUTURES_BOT_TOKEN = os.getenv('TELEGRAM_FUTURES_BOT_TOKEN', '')
        self.TELEGRAM_FUTURES_CHAT_ID = os.getenv('TELEGRAM_FUTURES_CHAT_ID', '')
        
        # Spot Bot
        self.TELEGRAM_SPOT_ENABLED = self._str_to_bool('false')
        self.TELEGRAM_SPOT_BOT_TOKEN = os.getenv('TELEGRAM_SPOT_BOT_TOKEN', '')
        self.TELEGRAM_SPOT_CHAT_ID = os.getenv('TELEGRAM_SPOT_CHAT_ID', '')
        
        # Arbitrage Bot
        self.TELEGRAM_ARBITRAGE_ENABLED = self._str_to_bool('false')
        self.TELEGRAM_ARBITRAGE_BOT_TOKEN = os.getenv('TELEGRAM_ARBITRAGE_BOT_TOKEN', '')
        self.TELEGRAM_ARBITRAGE_CHAT_ID = os.getenv('TELEGRAM_ARBITRAGE_CHAT_ID', '')
        
        # Legacy Telegram settings (backwards compatibility)
        self.TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.TELEGRAM_USER_ID = os.getenv('TELEGRAM_USER_ID', '')
        self.TELEGRAM_NOTIFICATIONS = self._str_to_bool('true')
        self.TELEGRAM_DAILY_SUMMARY = self._str_to_bool('false')
        self.TELEGRAM_DAILY_SUMMARY_TIME = '00:00'
        
        # ===== Logging Configuration =====
        self.LOG_API_CALLS = self._str_to_bool('true')
        self.LOG_POSITION_REJECTIONS = self._str_to_bool('true')
        self.LOG_TOKEN_METRICS = self._str_to_bool('false')
        self.LOG_RISK_MANAGEMENT = self._str_to_bool('true')
        self.LOG_TRADE_EXECUTION = self._str_to_bool('true')
        self.LOG_PERFORMANCE = self._str_to_bool('true')
        self.LOG_SYSTEM_EVENTS = self._str_to_bool('true')
        self.LOG_ERROR_TRACES = self._str_to_bool('true')
        self.LOG_LEVEL = 'INFO'.upper()
        self.LOG_OUTPUT = 'both'
        self.LOG_FILE_PATH = './logs'
        self.LOG_FILE_MAX_SIZE = int('10')
        self.LOG_FILE_BACKUP_COUNT = int('5')
        self.MUTE_REJECTION_LOGS = self._str_to_bool('false')
        self.ERROR_LOG_FILE = 'apex_error.log'

        # ===== MongoDB Configuration =====
        self.MONGODB_ENABLED = self._str_to_bool('false')
        # ===== SQLite Configuration =====
        self.SQLITE_ENABLED = self._str_to_bool('true')
        self.SQLITE_DB_NAME = os.getenv('SQLITE_DB_NAME', 'apex_hunter_v14.db')

        self.MONGODB_HOST = os.getenv('MONGODB_HOST', '')
        self.MONGODB_PORT = int(os.getenv('MONGODB_PORT', '27017'))
        self.MONGODB_DATABASE = os.getenv('MONGODB_DATABASE', 'apex_hunter_v14')
        self.MONGODB_USERNAME = os.getenv('MONGODB_USERNAME', '')
        self.MONGODB_PASSWORD = os.getenv('MONGODB_PASSWORD', '')
        self.MONGODB_RETENTION_DAYS = int('30')

        # ===== Cleanup Configuration =====
        self.CLEAN_LOGS = self._str_to_bool('no')
        self.CLEAN_DB = self._str_to_bool('no')
        self.CLEAN_TELEGRAM = self._str_to_bool('no')

        # ===== Testing/Filter Configuration =====
        self.TESTING_MODE = self._str_to_bool('false')
        self.TESTING_ADX_MIN = float('15')
        self.TESTING_VOLUME_MULT = float('0.8')
        self.MIN_VOLUME_USDT = float('2500')
        self.MIN_VOLUME_RATIO_ESCAPE = float('1.5')
        self.MAX_VOLATILITY_PERCENT = float('5.0')
        self.FORCE_TRADES = self._str_to_bool('false')

        # ===== System Configuration =====
        self.TIMEFRAME = '15m'
        self.HEARTBEAT_INTERVAL = int('60')
        self.API_TIMEOUT = int('10')
        self.RETRY_ATTEMPTS = int('3')
        self.RETRY_DELAY = int('1')
        self.RATE_LIMIT_BUFFER = float('0.8')
        self.DATA_PERSISTENCE = self._str_to_bool('true')
        self.DATA_DIRECTORY = './data'
        
        # ===== Advanced Configuration =====
        self.ENABLE_PAPER_TRADING_SLIPPAGE = self._str_to_bool(
            'true'
        )
        self.PAPER_TRADING_SLIPPAGE_PERCENT = float(
            '0.1'
        )
        self.ENABLE_PERFORMANCE_ANALYTICS = self._str_to_bool(
            'true'
        )
        self.REBALANCE_ON_PROFIT = self._str_to_bool('false')
        self.REBALANCE_THRESHOLD = float('20')
        
        # ===== Safety Configuration =====
        self.REQUIRE_CONFIRMATION_FOR_LIVE = self._str_to_bool(
            'true'
        )
        self.ENABLE_EMERGENCY_SHUTDOWN = self._str_to_bool(
            'true'
        )
        self.EMERGENCY_SHUTDOWN_PASSWORD = os.getenv(
            'EMERGENCY_SHUTDOWN_PASSWORD',
            'change_this_password'
        )
        self.MAX_API_ERRORS_PER_HOUR = int('10')

        # ===== Portfolio Profit Ratchet (Phase: Gains Lock) =====
        self.PROFIT_RATCHET_ENABLED = self._str_to_bool('false')
        self.PROFIT_RATCHET_ACTIVATION = float('5.0')
        self.PROFIT_RATCHET_TRAILING = float('2.0')
        self.PROFIT_RATCHET_FLOOR = float('3.0')
        self.PROFIT_RATCHET_COOLDOWN = int('5')
        self.PROFIT_RATCHET_SLIPPAGE_BUFFER = float('0.2')
    
    def _validate_configuration(self):
        """Validate critical configuration values"""
        
        # Validate Exchange Credentials (Only for active exchanges)
        if self.TRADING_MODE == 'live':
            # KuCoin Validation
            if any(ex == 'kucoin' for ex in [self.EXCHANGE, self.FUTURES_EXCHANGE, self.SPOT_EXCHANGE]):
                if not self.KUCOIN_API_KEY or not self.KUCOIN_API_SECRET or not self.KUCOIN_API_PASSPHRASE:
                    raise ValueError("KuCoin API credentials are required for live KuCoin trading")
            
            # Binance Validation
            if any(ex == 'binance' for ex in [self.EXCHANGE, self.FUTURES_EXCHANGE, self.SPOT_EXCHANGE]):
                if not self.BINANCE_API_KEY or not self.BINANCE_API_SECRET:
                    raise ValueError("Binance API credentials are required for live Binance trading")
        
        # Validate Telegram credentials if notifications enabled
        if self.TELEGRAM_NOTIFICATIONS:
            if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_USER_ID:
                raise ValueError("Telegram credentials required when notifications are enabled")
        
        # Validate numeric ranges
        if not 0.1 <= self.POSITION_SIZE_PERCENT <= 100:
            raise ValueError("POSITION_SIZE_PERCENT must be between 0.1 and 100")
        
        if self.MAX_LEVERAGE < 1:
            raise ValueError("MAX_LEVERAGE must be at least 1")
        
        if not 0 < self.CORRELATION_THRESHOLD <= 1:
            raise ValueError("CORRELATION_THRESHOLD must be between 0 and 1")
        
        # Validate mode settings
        if self.TRADING_MODE not in ['paper', 'simulation', 'live']:
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        
        # Validate exchange environment
        if self.EXCHANGE_ENVIRONMENT not in ['testnet', 'production']:
            raise ValueError("EXCHANGE_ENVIRONMENT must be 'testnet' or 'production'")
        
        # Validate log level
        if self.LOG_LEVEL not in ['DEBUG', 'INFO', 'WARNING', 'ERROR']:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, or ERROR")
        
        # Create necessary directories
        if self.DATA_PERSISTENCE:
            os.makedirs(self.DATA_DIRECTORY, exist_ok=True)
        
        if self.LOG_OUTPUT in ['file', 'both']:
            os.makedirs(self.LOG_FILE_PATH, exist_ok=True)
    
    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean"""
        return value.lower() in ('true', '1', 'yes', 'on')
    
    def get_drawdown_adjusted_position_size(self, current_drawdown: float) -> float:
        """
        Calculate position size adjustment based on current drawdown
        
        Args:
            current_drawdown: Current drawdown percentage (positive value)
        
        Returns:
            Adjusted position size multiplier (0.0 to 1.0)
        """
        if current_drawdown >= self.MAX_DRAWDOWN_PERCENT:
            return 0.0  # No trading
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.67:
            return 0.33  # 33% of normal size
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.33:
            return 0.67  # 67% of normal size
        else:
            return 1.0  # Full size
    
    def get_drawdown_adjusted_leverage(self, current_drawdown: float) -> int:
        """
        Calculate leverage adjustment based on current drawdown
        
        Args:
            current_drawdown: Current drawdown percentage (positive value)
        
        Returns:
            Adjusted maximum leverage
        """
        if current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.67:
            return max(1, self.MAX_LEVERAGE // 2)
        elif current_drawdown >= self.MAX_DRAWDOWN_PERCENT * 0.33:
            return max(1, int(self.MAX_LEVERAGE * 0.7))
        else:
            return self.MAX_LEVERAGE

    def get_leverage_cap(self, confidence: float) -> int:
        """
        Phase-1 core fix: confidence-aware leverage cap.

        Forensics on 283 mainnet trades (Aug 7-27):
          - conf >= 0.90 at 5x sees 60% win / +1.64 avg (rescues the move)
          - conf >= 0.90 at 6-10x collapses (10x = 12.5% win, -1.15 avg)
          - high leverage shrinks the RAW stop to wick-noise distance (0 wins in
            17 SLs at 10x), so the cap doubles as the raw-stop-floor protection.
        Returns the effective working cap: 5x base, 6x for conf >= TIER_HOT_CONF,
        never above the ABSOLUTE emergency ceiling (FUTURES_MAX_LEVERAGE).
        """
        hot_conf = getattr(self, 'TIER_HOT_CONF', 0.90)
        cap = getattr(self, 'LEV_CAP_HIGH_CONF', 6) if float(confidence) >= hot_conf \
            else getattr(self, 'LEV_CAP_BASE', 5)
        return max(1, min(int(cap), int(getattr(self, 'FUTURES_MAX_LEVERAGE', 10))))

    def get_trailing_callback(self, leverage) -> float:
        """
        Leverage-aware TRAILING_STOP_MARKET callbackRate (% of PRICE).

        ROE give-back = price callback x leverage, so the callback is bracketed
        by the position's leverage to keep give-back roughly constant:
          lev >= 5 -> TRAILING_CALLBACK_LEV_HIGH (1%  -> 5-10% ROE)
          lev 2-4  -> TRAILING_CALLBACK_LEV_MID  (2%  -> 4-8% ROE)
          lev 1    -> TRAILING_CALLBACK_LEV_LOW  (3%  -> 3% ROE, legacy default)
        Clamped to Binance bounds 1.0-10.0%.
        """
        try:
            lev = int(float(leverage or 1))
        except (TypeError, ValueError):
            lev = 1
        if lev >= 5:
            cb = self.TRAILING_CALLBACK_LEV_HIGH
        elif lev >= 2:
            cb = self.TRAILING_CALLBACK_LEV_MID
        else:
            cb = self.TRAILING_CALLBACK_LEV_LOW
        return max(1.0, min(10.0, float(cb)))

    def get_tier_callback(self, leverage, tier) -> float:
        """
        Tier callback = leverage-aware base + tier width (1% per tier), so the
        trail still widens as a position proves it runs, but in ROE-consistent
        steps instead of the flat 5%/8% price callbacks (25%/40% ROE at 8x).
        """
        base = self.get_trailing_callback(leverage)
        width = {1: 1.0, 2: 2.0}.get(int(tier), 0.0)
        return max(1.0, min(10.0, base + width))

    def get_trailing_activation(self, leverage) -> float:
        """
        Leverage-aware activation (% of PRICE) for the initial trailing order:
        arms at ~TRAILING_ACTIVATION_TARGET_ROE % ROE, floored at
        TRAILING_ACTIVATION_LEV_FLOOR so low leverage keeps a sane distance.
        (7x: 15/7 = 2.14% price ~= 15% ROE instead of flat 5% = 35% ROE.)
        """
        try:
            lev = max(int(float(leverage or 1)), 1)
        except (TypeError, ValueError):
            lev = 1
        act = max(float(self.TRAILING_ACTIVATION_LEV_FLOOR),
                  float(self.TRAILING_ACTIVATION_TARGET_ROE) / lev)
        # 2026-09-05: price-based cap — at 1x the ROE formula demands +15% price,
        # which the strategy's actual winner profile (+2..8%) never reaches, so
        # the trailing never armed (SKR: +12% peak -> full give-back to SL).
        cap = float(getattr(self, 'TRAILING_ACTIVATION_PRICE_CAP', 0) or 0)
        if cap > 0:
            act = min(act, cap)
        return act
    
    def is_live_trading(self) -> bool:
        """Check if bot is in live trading mode"""
        return self.TRADING_MODE == 'live'
    
    def is_production_environment(self) -> bool:
        """Check if connected to production exchange"""
        return self.EXCHANGE_ENVIRONMENT == 'production'
    
    def __repr__(self) -> str:
        """String representation of configuration"""
        return (
            f"Config(mode={self.TRADING_MODE}, "
            f"env={self.EXCHANGE_ENVIRONMENT}, "
            f"capital={self.INITIAL_CAPITAL} {self.BASE_CURRENCY})"
        )
