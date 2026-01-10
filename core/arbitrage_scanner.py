"""
Arbitrage Scanner
Detects and logs arbitrage opportunities across multiple exchanges
Supports: Simple, Triangular, and Cross-Exchange Triangular arbitrage
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from collections import defaultdict


class ArbitrageScanner:
    """
    Multi-type arbitrage opportunity scanner
    Logs opportunities to Telegram without executing trades
    """
    
    def __init__(self, config, logger, exchange_manager, telegram_bot=None):
        """
        Initialize arbitrage scanner
        
        Args:
            config: Configuration object
            logger: Logger instance
            exchange_manager: ExchangeManager instance
            telegram_bot: Optional Telegram bot for notifications
        """
        self.config = config
        self.logger = logger
        self.exchange_manager = exchange_manager
        self.telegram = telegram_bot
        
        # Opportunity tracking
        self.opportunities_today = []
        self.hourly_opportunities = defaultdict(list)
        self.last_hourly_reset = datetime.now()
        
        # Daily stats
        self.daily_profit = 0.0
        self.total_opportunities = 0
        
        self.logger.system("Arbitrage scanner initialized")
    
    def scan_all_opportunities(self):
        """Scan for all types of arbitrage opportunities"""
        current_time = datetime.now()
        
        # Reset hourly counter
        if current_time - self.last_hourly_reset > timedelta(hours=1):
            self.hourly_opportunities.clear()
            self.last_hourly_reset = current_time
        
        opportunities = []
        
        # Simple arbitrage (cross-exchange)
        if self.config.ARBITRAGE_SIMPLE:
            simple_opps = self._scan_simple_arbitrage()
            opportunities.extend(simple_opps)
        
        # Triangular arbitrage (same exchange)
        if self.config.ARBITRAGE_TRIANGULAR:
            triangular_opps = self._scan_triangular_arbitrage()
            opportunities.extend(triangular_opps)
        
        # Log top N opportunities
        self._log_top_opportunities(opportunities)
        
        return opportunities
    
    def _scan_simple_arbitrage(self) -> List[Dict]:
        """
        Scan for simple cross-exchange arbitrage
        Buy low on Exchange A, Sell high on Exchange B
        """
        opportunities = []
        
        for symbol in self.config.ARBITRAGE_PAIRS:
            # Fetch tickers from all exchanges
            tickers = self.exchange_manager.fetch_tickers_all_exchanges(symbol)
            
            if len(tickers) < 2:
                continue  # Need at least 2 exchanges
            
            # Find best buy and sell prices
            exchanges = list(tickers.keys())
            
            for i, buy_exchange in enumerate(exchanges):
                for sell_exchange in exchanges[i+1:]:
                    opportunity = self._calculate_simple_arbitrage(
                        symbol,
                        buy_exchange,
                        sell_exchange,
                        tickers[buy_exchange],
                        tickers[sell_exchange]
                    )
                    
                    if opportunity and opportunity['profit_percent'] >= self.config.ARBITRAGE_MIN_PROFIT_PERCENT:
                        opportunities.append(opportunity)
        
        return opportunities
    
    def _calculate_simple_arbitrage(self, symbol: str, buy_ex: str, sell_ex: str,
                                    buy_ticker: Dict, sell_ticker: Dict) -> Optional[Dict]:
        """Calculate simple arbitrage opportunity"""
        try:
            buy_price = float(buy_ticker.get('ask', 0))  # Ask price (buy)
            sell_price = float(sell_ticker.get('bid', 0))  # Bid price (sell)
            
            if buy_price <= 0 or sell_price <= 0:
                return None
            
            # Calculate with virtual capital
            amount_usdt = self.config.ARBITRAGE_VIRTUAL_CAPITAL
            amount_crypto = amount_usdt / buy_price
            
            # Revenue from selling
            sell_value = amount_crypto * sell_price
            
            # Calculate fees
            fees = self._calculate_fees(buy_ex, sell_ex, amount_usdt, sell_value)
            
            # Net profit
            gross_profit = sell_value - amount_usdt
            net_profit = gross_profit - fees
            profit_percent = (net_profit / amount_usdt) * 100
            
            if net_profit > 0:
                return {
                    'type': 'simple',
                    'symbol': symbol,
                    'buy_exchange': buy_ex,
                    'sell_exchange': sell_ex,
                    'buy_price': buy_price,
                    'sell_price': sell_price,
                    'spread': sell_price - buy_price,
                    'spread_percent': ((sell_price - buy_price) / buy_price) * 100,
                    'amount_usdt': amount_usdt,
                    'fees': fees,
                    'gross_profit': gross_profit,
                    'net_profit': net_profit,
                    'profit_percent': profit_percent,
                    'timestamp': datetime.now()
                }
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Error calculating arbitrage for {symbol}: {e}")
            return None
    
    def _scan_triangular_arbitrage(self) -> List[Dict]:
        """
        Scan for triangular arbitrage on same exchange
        Example: USDT → BTC → ETH → USDT
        """
        opportunities = []
        
        # Get primary exchange
        primary_ex = self.exchange_manager.primary_exchange
        exchange_id = self.config.EXCHANGE
        
        # Common triangular paths
        paths = [
            ('BTC/USDT', 'ETH/BTC', 'ETH/USDT'),
            ('BTC/USDT', 'SOL/BTC', 'SOL/USDT'),
            ('ETH/USDT', 'SOL/ETH', 'SOL/USDT'),
        ]
        
        for path in paths:
            opportunity = self._calculate_triangular_arbitrage(exchange_id, primary_ex, path)
            
            if opportunity and opportunity['profit_percent'] >= self.config.ARBITRAGE_MIN_PROFIT_PERCENT:
                opportunities.append(opportunity)
        
        return opportunities
    
    def _calculate_triangular_arbitrage(self, exchange_id: str, exchange_client, path: Tuple[str, str, str]) -> Optional[Dict]:
        """Calculate triangular arbitrage for a path"""
        try:
            pair1, pair2, pair3 = path
            
            # Fetch tickers
            ticker1 = exchange_client.get_ticker(pair1)
            ticker2 = exchange_client.get_ticker(pair2)
            ticker3 = exchange_client.get_ticker(pair3)
            
            if not all([ticker1, ticker2, ticker3]):
                return None
            
            # Start with USDT
            start_amount = self.config.ARBITRAGE_VIRTUAL_CAPITAL
            
            # Trade 1: USDT → BTC
            price1 = float(ticker1.get('ask', 0))
            amount_after_1 = start_amount / price1 if price1 > 0 else 0
            
            # Trade 2: BTC → ETH
            price2 = float(ticker2.get('ask', 0))
            amount_after_2 = amount_after_1 / price2 if price2 > 0 else 0
            
            # Trade 3: ETH → USDT
            price3 = float(ticker3.get('bid', 0))
            final_amount = amount_after_2 * price3
            
            if final_amount <= 0:
                return None
            
            # Calculate fees (3 trades)
            fee_rate = 0.001  # 0.1% per trade (typical)
            total_fees = start_amount * fee_rate * 3
            
            # Net profit
            gross_profit = final_amount - start_amount
            net_profit = gross_profit - total_fees
            profit_percent = (net_profit / start_amount) * 100
            
            if net_profit > 0:
                return {
                    'type': 'triangular',
                    'exchange': exchange_id,
                    'path': f"{pair1} → {pair2} → {pair3}",
                    'start_amount': start_amount,
                    'final_amount': final_amount,
                    'fees': total_fees,
                    'gross_profit': gross_profit,
                    'net_profit': net_profit,
                    'profit_percent': profit_percent,
                    'timestamp': datetime.now()
                }
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Error calculating triangular arbitrage: {e}")
            return None
    
    def _calculate_fees(self, buy_exchange: str, sell_exchange: str,
                       buy_amount: float, sell_amount: float) -> float:
        """
        Calculate total fees for arbitrage trade
        Includes: trading fees, withdrawal fees, network fees
        """
        # Trading fees (typical: 0.1% maker/taker)
        buy_fee = buy_amount * 0.001  # 0.1%
        sell_fee = sell_amount * 0.001  # 0.1%
        
        # Withdrawal fee (estimated, varies by exchange)
        withdrawal_fee = 0.0005  # 0.0005 BTC ~ $20-50
        withdrawal_fee_usdt = withdrawal_fee * (sell_amount / buy_amount) * 50000  # Rough estimate
        
        # Network fee (blockchain)
        network_fee = 3.0  # ~$3 average
        
        # Total fees
        if self.config.ARBITRAGE_INCLUDE_ALL_FEES:
            total_fees = buy_fee + sell_fee + withdrawal_fee_usdt + network_fee
        else:
            total_fees = buy_fee + sell_fee
        
        return total_fees
    
    def _log_top_opportunities(self, opportunities: List[Dict]):
        """Log top N opportunities of the hour"""
        if not opportunities:
            return
        
        # Sort by profit
        sorted_opps = sorted(opportunities, key=lambda x: x['profit_percent'], reverse=True)
        
        # Get top N
        top_n = sorted_opps[:self.config.ARBITRAGE_LOG_TOP_N_PER_HOUR]
        
        # Log to Telegram
        for opp in top_n:
            # Check if already logged this hour
            hour_key = datetime.now().strftime('%Y-%m-%d-%H')
            opp_key = f"{opp['type']}-{opp.get('symbol', opp.get('path'))}"
            
            if opp_key in self.hourly_opportunities[hour_key]:
                continue  # Already logged
            
            self.hourly_opportunities[hour_key].append(opp_key)
            self._send_opportunity_notification(opp)
            
            # Update stats
            self.total_opportunities += 1
            self.daily_profit += opp['net_profit']
            self.opportunities_today.append(opp)
    
    def _send_opportunity_notification(self, opp: Dict):
        """Send opportunity to Telegram"""
        if not self.config.ARBITRAGE_TELEGRAM_NOTIFICATIONS or not self.telegram:
            return
        
        if opp['type'] == 'simple':
            message = f"""
🔍 ARBITRAGE OPPORTUNITY #{self.total_opportunities + 1}

Type: Simple Cross-Exchange
Pair: {opp['symbol']}

💰 Trade Details:
Buy: {opp['buy_exchange'].upper()} @ ${opp['buy_price']:.2f}
Sell: {opp['sell_exchange'].upper()} @ ${opp['sell_price']:.2f}
Spread: {opp['spread_percent']:.2f}%

📊 With ${opp['amount_usdt']:.2f} USDT:
Gross Profit: ${opp['gross_profit']:.2f}
Fees: ${opp['fees']:.2f}
Net Profit: ${opp['net_profit']:.2f} ✅
Profit %: {opp['profit_percent']:.2f}%

⏰ {datetime.now().strftime('%H:%M:%S UTC')}
"""
        else:  # triangular
            message = f"""
🔺 TRIANGULAR ARBITRAGE #{self.total_opportunities + 1}

Exchange: {opp['exchange'].upper()}
Path: {opp['path']}

💰 With ${opp['start_amount']:.2f}:
Final Amount: ${opp['final_amount']:.2f}
Fees: ${opp['fees']:.2f}
Net Profit: ${opp['net_profit']:.2f} ✅
Profit %: {opp['profit_percent']:.2f}%

⏰ {datetime.now().strftime('%H:%M:%S UTC')}
"""
        
        try:
            self.telegram.send_message(message)
        except Exception as e:
            self.logger.debug(f"Failed to send Telegram notification: {e}")
    
    def get_daily_summary(self) -> Dict:
        """Get daily arbitrage summary"""
        if not self.opportunities_today:
            return {
                'total_opportunities': 0,
                'total_profit': 0,
                'avg_profit': 0,
                'best_opportunity': None
            }
        
        return {
            'total_opportunities': len(self.opportunities_today),
            'total_profit': self.daily_profit,
            'avg_profit': self.daily_profit / len(self.opportunities_today),
            'best_opportunity': max(self.opportunities_today, key=lambda x: x['profit_percent'])
        }
    
    def reset_daily_stats(self):
        """Reset daily statistics"""
        self.opportunities_today = []
        self.daily_profit = 0.0
        self.total_opportunities = 0
        self.logger.system("Arbitrage daily stats reset")
