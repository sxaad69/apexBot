"""
Strategy A5: Advanced Market Microstructure
Professional strategy using order flow, whale detection, and session-based confidence
"""

import pandas as pd
import numpy as np
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
import time as time_module
import ccxt

from .base_strategy import BaseStrategy
from .filters import get_strategy_filters


class StrategyA5(BaseStrategy):
    """
    Advanced Market Microstructure Strategy
    - Order book imbalance analysis for real market pressure
    - Whale trade detection for institutional flow
    - Session-based confidence (higher during peak hours)
    - 24/7 trading with adaptive confidence levels
    High-conviction signals for consistent profits
    """

    def __init__(self, config, logger):
        super().__init__(config, logger, "A5: Market Microstructure")

        # Conviction requirements (adaptive based on session)
        self.testing_mode = getattr(config, 'TESTING_MODE', False)
        self.base_confidence_threshold = 0.50 if self.testing_mode else 0.75
        self.peak_confidence_threshold = 0.60 if self.testing_mode else 0.85
        self.min_orderbook_imbalance = 0.10 if self.testing_mode else 0.25
        self.min_whale_value = 1000 if self.testing_mode else 50000
        self.volume_multiplier = 0.8 if self.testing_mode else 1.5

        # Session definitions (UTC hours)
        # Crypto trades 24/7 but has peak activity periods
        self.sessions = {
            'asia': {'start': 0, 'end': 8, 'confidence_boost': 0.0},      # 12AM-8AM UTC
            'europe': {'start': 8, 'end': 14, 'confidence_boost': 0.05},  # 8AM-2PM UTC
            'us_peak': {'start': 14, 'end': 21, 'confidence_boost': 0.10}, # 2PM-9PM UTC (highest activity)
            'us_late': {'start': 21, 'end': 24, 'confidence_boost': 0.0}  # 9PM-12AM UTC
        }

        # ATR multipliers for microstructure trades (slightly wider for safety)
        self.atr_sl_mult = 2.0
        self.atr_tp_mult = 4.0

        # Universal filters
        self.filters = get_strategy_filters(config)

        self.logger.info("Strategy A5 initialized - Market Microstructure Analysis (24/7)")
        self.logger.info(f"Base confidence: {self.base_confidence_threshold}, Peak: {self.peak_confidence_threshold}")
        self.logger.info(f"Order book imbalance min: {self.min_orderbook_imbalance * 100}%")

    def analyze_order_book(self, symbol: str, market_type: str = 'spot') -> float:
        """Analyze order book imbalance for real market pressure"""
        try:
            # Get exchange client from engine if available, otherwise fallback to creating one
            if hasattr(self, 'exchange_client') and self.exchange_client:
                exchange = self.exchange_client.exchange
            else:
                # Fallback (less efficient)
                exchange_class = getattr(ccxt, self.config.FUTURES_EXCHANGE.lower() if market_type == 'futures' else self.config.SPOT_EXCHANGE.lower())
                exchange = exchange_class()

            # Fetch order book (top 20 levels)
            orderbook = exchange.fetch_order_book(symbol, limit=20)

            if not orderbook or 'bids' not in orderbook or 'asks' not in orderbook:
                return 0.0

            # Calculate volume-weighted imbalance
            bid_volume = sum([bid[1] for bid in orderbook['bids'][:10]])  # Top 10 bids
            ask_volume = sum([ask[1] for ask in orderbook['asks'][:10]])  # Top 10 asks

            if bid_volume + ask_volume == 0:
                return 0.0

            # Imbalance: positive = buying pressure, negative = selling pressure
            imbalance = (bid_volume - ask_volume) / (bid_volume + ask_volume)

            return imbalance

        except Exception as e:
            self.logger.debug(f"Order book analysis failed for {symbol}: {e}")
            return 0.0

    def detect_whales(self, symbol: str, market_type: str = 'spot') -> Dict:
        """Detect large institutional trades (whales)"""
        try:
            # Get exchange client from engine if available
            if hasattr(self, 'exchange_client') and self.exchange_client:
                exchange = self.exchange_client.exchange
            else:
                # Fallback (less efficient)
                exchange_class = getattr(ccxt, self.config.FUTURES_EXCHANGE.lower() if market_type == 'futures' else self.config.SPOT_EXCHANGE.lower())
                exchange = exchange_class()

            # Fetch recent trades (last 100)
            recent_trades = exchange.fetch_trades(symbol, limit=100)

            if not recent_trades:
                return {'count': 0, 'buy_pressure': 0, 'sell_pressure': 0, 'total_value': 0}

            # Analyze whale activity (last 5 minutes)
            current_time = time_module.time() * 1000
            five_min_ago = current_time - (5 * 60 * 1000)

            whale_trades = []
            for trade in recent_trades:
                if trade['timestamp'] > five_min_ago:
                    value = trade['price'] * trade['amount']
                    if value >= self.min_whale_value:
                        whale_trades.append({
                            'side': trade['side'],
                            'value': value,
                            'timestamp': trade['timestamp']
                        })

            # Calculate whale pressure
            buy_whales = [w for w in whale_trades if w['side'] == 'buy']
            sell_whales = [w for w in whale_trades if w['side'] == 'sell']

            buy_pressure = len(buy_whales)
            sell_pressure = len(sell_whales)
            total_value = sum(w['value'] for w in whale_trades)

            return {
                'count': len(whale_trades),
                'buy_pressure': buy_pressure,
                'sell_pressure': sell_pressure,
                'net_pressure': buy_pressure - sell_pressure,
                'total_value': total_value
            }

        except Exception as e:
            self.logger.debug(f"Whale detection failed for {symbol}: {e}")
            return {'count': 0, 'buy_pressure': 0, 'sell_pressure': 0, 'net_pressure': 0, 'total_value': 0}

    def get_market_regime(self, df: pd.DataFrame) -> str:
        """Determine market regime: trending, ranging, or volatile"""
        if len(df) < 20:
            return 'unknown'

        try:
            # Calculate ADX for trend strength
            adx = self.calculate_adx(df).iloc[-1] if 'adx' not in df.columns else df['adx'].iloc[-1]

            # Calculate volatility (20-period standard deviation of returns)
            returns = df['close'].pct_change().dropna()
            volatility = returns.tail(20).std() * 100  # Convert to percentage

            # Determine regime
            if adx > 30 and volatility < 3:
                return 'strong_trend'
            elif adx > 25 and volatility < 4:
                return 'moderate_trend'
            elif adx < 15 and volatility < 2:
                return 'ranging'
            elif volatility > 5:
                return 'volatile'
            else:
                return 'normal'

        except Exception as e:
            self.logger.debug(f"Regime analysis failed: {e}")
            return 'unknown'

    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators for advanced analysis"""
        df = df.copy()

        # Calculate ADX for market regime analysis
        df = self.calculate_adx(df)

        # Calculate volume metrics
        if len(df) >= 20:
            df['volume_avg_20'] = df['volume'].rolling(20).mean()
            df['volume_ratio'] = df['volume'] / df['volume_avg_20']

        # Calculate basic price metrics
        df['returns'] = df['close'].pct_change()
        df['volatility'] = df['returns'].rolling(20).std() * 100  # 20-period volatility

        return df

    def get_current_session(self) -> tuple:
        """
        Get current trading session and confidence boost

        Returns: (session_name, confidence_boost)
        """
        current_hour = datetime.utcnow().hour

        for session_name, session_data in self.sessions.items():
            if session_data['start'] <= current_hour < session_data['end']:
                return session_name, session_data['confidence_boost']

        return 'unknown', 0.0

    def get_session_threshold(self) -> float:
        """Get confidence threshold based on current session"""
        session_name, boost = self.get_current_session()

        # During peak hours, require higher confidence
        if session_name == 'us_peak':
            return self.peak_confidence_threshold
        else:
            return self.base_confidence_threshold

    def generate_signal(self, df: pd.DataFrame, symbol: str = 'BTC/USDT', market_type: str = 'spot') -> Optional[Dict]:
        """
        Generate high-conviction signals using advanced market microstructure analysis
        Now trades 24/7 with session-based confidence adjustments
        """
        if len(df) < 25:
            return None

        current_price = df['close'].iloc[-1]

        # Calculate indicators first
        df = self.calculate_indicators(df)
        df = self.calculate_adx(df)
        df = self.calculate_atr(df)

        # 1. Get current session info
        session_name, confidence_boost = self.get_current_session()
        confidence_threshold = self.get_session_threshold()

        # 2. Universal filters (ADX, volume, etc.)
        should_trade, filter_reason = self.filters.should_trade_symbol(df, symbol, self.name)
        if not should_trade:
            self.logger.debug(f"[A5] {symbol} FILTERED: {filter_reason}")
            return None

        # 3. Market regime analysis
        regime = self.get_market_regime(df)
        if regime in ['volatile', 'unknown']:
            return None  # Skip uncertain conditions

        # 4. Order book analysis
        orderbook_imbalance = self.analyze_order_book(symbol, market_type)
        if abs(orderbook_imbalance) < self.min_orderbook_imbalance:
            return None  # Not enough market pressure

        # 5. Whale detection
        whale_data = self.detect_whales(symbol, market_type)

        # 6. Volume analysis
        current_volume = df['volume'].iloc[-1]
        avg_volume = df['volume'].rolling(20).mean().iloc[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

        # 7. Calculate confidence score
        confidence = 0.5  # Base confidence

        # Order book contribution (up to 25%)
        confidence += abs(orderbook_imbalance) * 0.25

        # Whale contribution (up to 20%)
        if whale_data['count'] > 0:
            whale_score = min(whale_data['count'] * 0.05, 0.2)
            confidence += whale_score

        # Volume contribution (up to 10%)
        if volume_ratio > self.volume_multiplier:
            confidence += 0.1

        # Regime contribution (up to 10%)
        if regime in ['strong_trend', 'moderate_trend']:
            confidence += 0.1

        # Session boost (up to 10% during peak hours)
        confidence += confidence_boost

        # Must meet session-adjusted confidence threshold
        if confidence < confidence_threshold:
            return None

        # 8. Determine signal direction
        signal_side = None

        # Primary signal from order book
        if orderbook_imbalance > self.min_orderbook_imbalance:
            signal_side = 'buy'
        elif orderbook_imbalance < -self.min_orderbook_imbalance:
            signal_side = 'sell'

        # Confirm with whale data (if available and significant)
        if signal_side and whale_data['count'] >= 2:
            whale_bias = 'buy' if whale_data['net_pressure'] > 0 else 'sell'
            if signal_side != whale_bias:
                return None  # Conflicting whale flow

        if not signal_side:
            return None

        # 9. Calculate ATR-based dynamic stops
        stop_loss, take_profit = self.get_dynamic_stops(df, signal_side, self.atr_sl_mult, self.atr_tp_mult)

        # 10. Create signal
        signal = {
            'symbol': symbol,
            'side': signal_side,
            'entry_price': current_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'confidence': confidence,
            'strategy': self.name,
            'market_type': market_type,
            'session': session_name,
            'regime': regime,
            'orderbook_imbalance': orderbook_imbalance,
            'whale_count': whale_data['count'],
            'whale_net_pressure': whale_data['net_pressure'],
            'volume_ratio': volume_ratio,
            'indicators': {
                'adx': df['adx'].iloc[-1] if 'adx' in df.columns else None,
                'atr': df['atr'].iloc[-1] if 'atr' in df.columns else None,
                'volume': current_volume,
                'avg_volume': avg_volume
            }
        }

        self.logger.info(f"[A5] {symbol} {market_type.upper()} SIGNAL: {signal_side.upper()} @ ${current_price:.2f} "
                        f"(Confidence: {confidence:.2f}, Session: {session_name}, Regime: {regime})")

        return signal
