"""
Telegram Bot Notifications
Supports 3 separate bots: Futures, Spot, Arbitrage
"""

import requests
from typing import Optional, Dict
from datetime import datetime


class TelegramBot:
    """
    Single Telegram bot instance
    """
    
    def __init__(self, bot_token: str, chat_id: str, bot_name: str = "Bot"):
        """
        Initialize Telegram bot
        
        Args:
            bot_token: Telegram bot token
            chat_id: Telegram chat ID
            bot_name: Name for logging (Futures, Spot, Arbitrage)
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.bot_name = bot_name
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        
        # Test connection
        self.is_connected = self._test_connection()
    
    def _test_connection(self) -> bool:
        """Test if bot is properly configured"""
        try:
            url = f"{self.base_url}/getMe"
            response = requests.get(url, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    bot_info = data.get('result', {})
                    username = bot_info.get('username', 'Unknown')
                    print(f"✅ {self.bot_name} Telegram bot connected: @{username}")
                    return True
            
            print(f"❌ {self.bot_name} Telegram bot connection failed")
            return False
            
        except Exception as e:
            print(f"❌ {self.bot_name} Telegram bot error: {e}")
            return False
    
    def send_message(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send message to Telegram
        
        Args:
            message: Message text
            parse_mode: HTML or Markdown
        
        Returns:
            Message ID (int) if sent successfully, None otherwise
        """
        if not self.is_connected:
            return None
        
        try:
            url = f"{self.base_url}/sendMessage"
            payload = {
                'chat_id': self.chat_id,
                'text': message,
                'parse_mode': parse_mode
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                return response.json().get('result', {}).get('message_id')
            else:
                print(f"❌ {self.bot_name} failed to send message: {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ {self.bot_name} send error: {e}")
            return None

    def delete_message(self, message_id: int) -> bool:
        """
        Delete a message from the chat
        
        Args:
            message_id: ID of the message to delete
            
        Returns:
            True if deleted successfully
        """
        if not self.is_connected:
            return False
            
        try:
            url = f"{self.base_url}/deleteMessage"
            payload = {
                'chat_id': self.chat_id,
                'message_id': message_id
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            return response.status_code == 200
                
        except Exception as e:
            # print(f"❌ {self.bot_name} delete error: {e}")
            return False
    
    def send_alert(self, title: str, message: str, level: str = "INFO") -> bool:
        """
        Send formatted alert
        
        Args:
            title: Alert title
            message: Alert message
            level: INFO, WARNING, ERROR, CRITICAL
        """
        emoji_map = {
            'INFO': 'ℹ️',
            'WARNING': '⚠️',
            'ERROR': '❌',
            'CRITICAL': '🚨'
        }
        
        emoji = emoji_map.get(level.upper(), 'ℹ️')
        
        formatted = f"""
{emoji} <b>{title}</b>

{message}
"""
        return self.send_message(formatted)


class TelegramNotificationManager:
    """
    Manages all 3 Telegram bots (Futures, Spot, Arbitrage)
    """
    
    def __init__(self, config, logger):
        """
        Initialize all Telegram bots based on config
        
        Args:
            config: Configuration object
            logger: Logger instance
        """
        self.config = config
        self.logger = logger
        
        # Initialize bots
        self.futures_bot = None
        self.spot_bot = None
        self.arbitrage_bot = None
        
        # Setup Futures bot
        if hasattr(config, 'TELEGRAM_FUTURES_ENABLED') and config.TELEGRAM_FUTURES_ENABLED:
            token = getattr(config, 'TELEGRAM_FUTURES_BOT_TOKEN', '')
            chat_id = getattr(config, 'TELEGRAM_FUTURES_CHAT_ID', '')
            
            if token and chat_id:
                self.futures_bot = TelegramBot(token, chat_id, "Futures")
                logger.system("Futures Telegram bot initialized")
            else:
                logger.warning("Futures Telegram enabled but credentials missing")
        
        # Setup Spot bot
        if hasattr(config, 'TELEGRAM_SPOT_ENABLED') and config.TELEGRAM_SPOT_ENABLED:
            token = getattr(config, 'TELEGRAM_SPOT_BOT_TOKEN', '')
            chat_id = getattr(config, 'TELEGRAM_SPOT_CHAT_ID', '')
            
            if token and chat_id:
                self.spot_bot = TelegramBot(token, chat_id, "Spot")
                logger.system("Spot Telegram bot initialized")
            else:
                logger.warning("Spot Telegram enabled but credentials missing")
        
        # Setup Arbitrage bot
        if hasattr(config, 'TELEGRAM_ARBITRAGE_ENABLED') and config.TELEGRAM_ARBITRAGE_ENABLED:
            token = getattr(config, 'TELEGRAM_ARBITRAGE_BOT_TOKEN', '')
            chat_id = getattr(config, 'TELEGRAM_ARBITRAGE_CHAT_ID', '')
            
            if token and chat_id:
                self.arbitrage_bot = TelegramBot(token, chat_id, "Arbitrage")
                logger.system("Arbitrage Telegram bot initialized")
            else:
                logger.warning("Arbitrage Telegram enabled but credentials missing")
        
        # Message history file
        from pathlib import Path
        self.history_file = Path(getattr(config, 'DATA_DIRECTORY', './data')) / 'telegram_history.json'
        self._ensure_history_file()

    def _ensure_history_file(self):
        """Ensure history file exists"""
        import json
        if not self.history_file.parent.exists():
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            
        if not self.history_file.exists():
            with open(self.history_file, 'w') as f:
                json.dump([], f)

    def _log_message(self, message_id: int, chat_id: str, bot_name: str):
        """Log message ID for future wiping"""
        import json
        try:
            entry = {
                'message_id': message_id,
                'chat_id': chat_id,
                'bot': bot_name,
                'timestamp': datetime.now().isoformat()
            }
            
            history = []
            if self.history_file.exists():
                with open(self.history_file, 'r') as f:
                    try:
                        history = json.load(f)
                    except json.JSONDecodeError:
                        history = []
            
            history.append(entry)
            
            with open(self.history_file, 'w') as f:
                json.dump(history, f, indent=2)
                
        except Exception as e:
            print(f"Failed to log Telegram message: {e}")

    def wipe_all_messages(self):
        """Delete all tracked messages from Telegram"""
        import json
        import time
        
        if not self.history_file.exists():
            print("ℹ️  No Telegram message history found to wipe")
            return
            
        print("🗑️  Wiping Telegram messages...")
        
        try:
            with open(self.history_file, 'r') as f:
                history = json.load(f)
        except Exception:
            print("❌ Failed to read telegram history")
            return
            
        if not history:
            print("ℹ️  Telegram history is empty")
            return
            
        deleted_count = 0
        failed_count = 0
        
        # Bots map
        bots = {
            'Futures': self.futures_bot,
            'Spot': self.spot_bot,
            'Arbitrage': self.arbitrage_bot
        }
        
        for entry in history:
            bot_name = entry.get('bot')
            message_id = entry.get('message_id')
            
            bot = bots.get(bot_name)
            if bot and bot.delete_message(message_id):
                deleted_count += 1
            else:
                failed_count += 1
            
            # Rate limit avoidance
            time.sleep(0.05)
            
        print(f"✅ Deleted {deleted_count} messages ({failed_count} failed/expired)")
        
        # Clear history file
        with open(self.history_file, 'w') as f:
            json.dump([], f)
    
    # Futures bot methods
    def send_futures_message(self, message: str) -> bool:
        """Send message to Futures bot"""
        if self.futures_bot:
            msg_id = self.futures_bot.send_message(message)
            if msg_id:
                self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
                return True
        return False
    
    def send_futures_trade_entry(self, trade: Dict) -> bool:
        """Send futures trade entry notification"""
        if not self.futures_bot:
            return False
        
        message = f"""
🎯 <b>FUTURES TRADE ENTRY</b>

<b>Symbol:</b> {trade.get('symbol')}
<b>Side:</b> {trade.get('side', '').upper()}
<b>Entry Price:</b> ${trade.get('entry_price', 0):,.2f}
<b>Size:</b> ${trade.get('size', 0):,.2f}
<b>Leverage:</b> {trade.get('leverage', 1)}x

<b>Stop Loss:</b> ${trade.get('stop_loss', 0):,.2f}
<b>Take Profit:</b> ${trade.get('take_profit', 0):,.2f}

<b>Strategy:</b> {trade.get('strategy', 'Unknown')}
<b>Confidence:</b> {trade.get('confidence', 0)*100:.1f}%

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        return self.futures_bot.send_message(message)
    
    def send_futures_trade_exit(self, trade: Dict) -> bool:
        """Send futures trade exit notification"""
        if not self.futures_bot:
            return False

        pnl = trade.get('pnl', 0)
        pnl_emoji = "✅" if pnl > 0 else "❌"
        leverage = trade.get('leverage', 1)

        message = f"""
{pnl_emoji} <b>FUTURES TRADE EXIT</b>

<b>Symbol:</b> {trade.get('symbol')}
<b>Exit Price:</b> ${trade.get('exit_price', 0):,.2f}
<b>Leverage:</b> {leverage}x

<b>P&L:</b> ${pnl:,.2f} ({trade.get('pnl_percent', 0):.2f}%)
<b>Duration:</b> {trade.get('duration', 'N/A')}

<b>Strategy:</b> {trade.get('strategy', 'Unknown')}
"""
        msg_id = self.futures_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
            return True
        return False

    def send_futures_trailing_stop_update(self, update: Dict) -> bool:
        """Send futures trailing stop update notification"""
        if not self.futures_bot:
            return False

        update_type = update.get('type', 'update')  # 'activated' or 'update'
        emoji = "🚀" if update_type == 'activated' else "📈"

        message = f"""
{emoji} <b>FUTURES TRAILING STOP {update_type.upper()}</b>

<b>Symbol:</b> {update.get('symbol')}
<b>Current Price:</b> ${update.get('current_price', 0):,.2f}

<b>Strategy:</b> {update.get('strategy', 'Unknown')}

<b>Profit:</b> {update.get('profit_percent', 0):.2f}%
<b>Highest Price:</b> ${update.get('highest_price', 0):,.2f}

<b>Stop Loss:</b> ${update.get('old_stop_loss', 0):,.2f} → ${update.get('new_stop_loss', 0):,.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.futures_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
            return True
        return False

    def send_futures_trailing_tp_update(self, update: Dict) -> bool:
        """Send futures trailing take profit update notification"""
        if not self.futures_bot:
            return False

        update_type = update.get('type', 'update')  # 'activated' or 'update'
        emoji = "🎯" if update_type == 'activated' else "💰"

        # Get peak/trough price depending on position type
        peak_price = update.get('peak_price') or update.get('trough_price', 0)
        peak_label = "Peak Price" if update.get('peak_price') else "Trough Price"

        message = f"""
{emoji} <b>FUTURES TRAILING TP {update_type.upper()}</b>

<b>Symbol:</b> {update.get('symbol')}
<b>Current Price:</b> ${update.get('current_price', 0):,.2f}

<b>Strategy:</b> {update.get('strategy', 'Unknown')}

<b>Profit:</b> {update.get('profit_percent', 0):.2f}%
<b>{peak_label}:</b> ${peak_price:,.2f}

<b>Take Profit:</b> ${update.get('old_take_profit', 0):,.2f} → ${update.get('new_take_profit', 0):,.2f}

💡 Locking in profits as trade extends!

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.futures_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
            return True
        return False

    def send_futures_daily_summary(self, summary: Dict) -> bool:
        """Send futures daily summary"""
        if not self.futures_bot:
            return False
        
        message = f"""
📊 <b>FUTURES DAILY SUMMARY</b>
Date: {summary.get('date', datetime.now().strftime('%Y-%m-%d'))}

💰 <b>Performance:</b>
Total P&L: ${summary.get('total_pnl', 0):,.2f} ({summary.get('pnl_percent', 0):.2f}%)
Trades: {summary.get('total_trades', 0)}
Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}
Win Rate: {summary.get('win_rate', 0):.1f}%

📈 <b>Best Trade:</b> ${summary.get('best_trade', 0):,.2f}
📉 <b>Worst Trade:</b> ${summary.get('worst_trade', 0):,.2f}

🛡️ <b>Risk Metrics:</b>
Max Drawdown: {summary.get('max_drawdown', 0):.2f}%
Sharpe Ratio: {summary.get('sharpe_ratio', 0):.2f}
"""
        msg_id = self.futures_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
            return True
        return False
    
    # Spot bot methods
    def send_spot_message(self, message: str) -> bool:
        """Send message to Spot bot"""
        if self.spot_bot:
            msg_id = self.spot_bot.send_message(message)
            if msg_id:
                self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
                return True
        return False
    
    def send_spot_signal(self, signal: Dict) -> bool:
        """Send spot trading signal"""
        if not self.spot_bot:
            return False
        
        trading_status = "ENABLED ⚠️" if getattr(self.config, 'SPOT_TRADING_ENABLED', 'no') == 'yes' else "DISABLED (Logging Only)"
        
        message = f"""
📍 <b>SPOT SIGNAL</b>

<b>Signal:</b> {signal.get('side', '').upper()} {signal.get('symbol')}
<b>Price:</b> ${signal.get('entry_price', 0):,.2f}
<b>Amount:</b> ${signal.get('position_size', 0):,.2f}

<b>Stop Loss:</b> ${signal.get('stop_loss', 0):,.2f}
<b>Take Profit:</b> ${signal.get('take_profit', 0):,.2f}

<b>Strategy:</b> {signal.get('strategy', 'Unknown')}
<b>Confidence:</b> {signal.get('confidence', 0)*100:.1f}%

<b>Trading:</b> {trading_status}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False

    def send_spot_trailing_stop_update(self, update: Dict) -> bool:
        """Send spot trailing stop update notification"""
        if not self.spot_bot:
            return False

        update_type = update.get('type', 'update')  # 'activated' or 'update'
        emoji = "🚀" if update_type == 'activated' else "📈"

        message = f"""
{emoji} <b>SPOT TRAILING STOP {update_type.upper()}</b>

<b>Symbol:</b> {update.get('symbol')}
<b>Current Price:</b> ${update.get('current_price', 0):,.2f}

<b>Strategy:</b> {update.get('strategy', 'Unknown')}
<b>Profit:</b> {update.get('profit_percent', 0):.2f}%

<b>Stop Loss:</b> ${update.get('old_stop_loss', 0):,.2f} → ${update.get('new_stop_loss', 0):,.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False

    def send_spot_trailing_tp_update(self, update: Dict) -> bool:
        """Send spot trailing take profit update notification"""
        if not self.spot_bot:
            return False

        update_type = update.get('type', 'update')  # 'activated' or 'update'
        emoji = "🎯" if update_type == 'activated' else "💰"

        message = f"""
{emoji} <b>SPOT TRAILING TP {update_type.upper()}</b>

<b>Symbol:</b> {update.get('symbol')}
<b>Current Price:</b> ${update.get('current_price', 0):,.2f}

<b>Strategy:</b> {update.get('strategy', 'Unknown')}
<b>Profit:</b> {update.get('profit_percent', 0):.2f}%

<b>Take Profit:</b> ${update.get('old_take_profit', 0):,.2f} → ${update.get('new_take_profit', 0):,.2f}

💡 Locking in profits!

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False

    def send_spot_trade_entry(self, trade: Dict) -> bool:
        """Send spot trade entry notification"""
        if not self.spot_bot:
            return False

        message = f"""
🟢 <b>SPOT TRADE ENTRY</b>

<b>Symbol:</b> {trade.get('symbol')}
<b>Side:</b> {trade.get('side', '').upper()}
<b>Entry Price:</b> ${trade.get('entry_price', 0):,.2f}
<b>Size:</b> ${trade.get('size', 0):,.2f}

<b>Stop Loss:</b> ${trade.get('stop_loss', 0):,.2f}
<b>Take Profit:</b> ${trade.get('take_profit', 0):,.2f}

<b>Strategy:</b> {trade.get('strategy', 'Unknown')}

⏰ {trade.get('timestamp', datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False

    def send_spot_trade_exit(self, trade: Dict) -> bool:
        """Send spot trade exit notification"""
        if not self.spot_bot:
            return False

        pnl = trade.get('pnl_usdt', 0)
        pnl_pct = trade.get('pnl_pct', 0)
        emoji = "🟢" if pnl >= 0 else "🔴"
        result = "WIN" if pnl >= 0 else "LOSS"

        message = f"""
{emoji} <b>SPOT TRADE EXIT - {result}</b>

<b>Symbol:</b> {trade.get('symbol')}
<b>Side:</b> {trade.get('side', '').upper()}

<b>Entry:</b> ${trade.get('entry_price', 0):,.2f}
<b>Exit:</b> ${trade.get('exit_price', 0):,.2f}
<b>Reason:</b> {trade.get('reason', 'Unknown').replace('_', ' ').upper()}

<b>P&L:</b> ${pnl:+,.2f} ({pnl_pct:+.2f}%)
<b>Duration:</b> {trade.get('duration', 0)} minutes

<b>Strategy:</b> {trade.get('strategy', 'Unknown')}
<b>Balance:</b> ${trade.get('balance_after', 0):,.2f}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False

    def send_spot_daily_summary(self, summary: Dict) -> bool:
        """Send spot daily summary"""
        if not self.spot_bot:
            return False
        
        message = f"""
📊 <b>SPOT DAILY SUMMARY</b>
Date: {summary.get('date', datetime.now().strftime('%Y-%m-%d'))}

💰 <b>Virtual Performance:</b>
Total P&L: ${summary.get('total_pnl', 0):,.2f} ({summary.get('pnl_percent', 0):.2f}%)
Signals: {summary.get('signals', 0)}
Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}
Win Rate: {summary.get('win_rate', 0):.1f}%

📊 <b>Comparison:</b>
Spot (No Leverage): +{summary.get('spot_return', 0):.2f}%
Futures (With Leverage): +{summary.get('futures_return', 0):.2f}%
Leverage Benefit: {summary.get('leverage_multiplier', 0):.2f}x
"""
        msg_id = self.spot_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
            return True
        return False
    
    # Arbitrage bot methods
    def send_arbitrage_message(self, message: str) -> bool:
        """Send message to Arbitrage bot"""
        if self.arbitrage_bot:
            msg_id = self.arbitrage_bot.send_message(message)
            if msg_id:
                self._log_message(msg_id, self.arbitrage_bot.chat_id, "Arbitrage")
                return True
        return False
    
    def send_arbitrage_opportunity(self, opportunity: Dict) -> bool:
        """Send arbitrage opportunity notification"""
        if not self.arbitrage_bot:
            return False
        
        opp_type = opportunity.get('type', 'simple')
        
        if opp_type == 'simple':
            message = f"""
🔍 <b>ARBITRAGE OPPORTUNITY</b>

<b>Type:</b> Cross-Exchange
<b>Pair:</b> {opportunity.get('symbol')}

<b>Buy:</b> {opportunity.get('buy_exchange', '').upper()} @ ${opportunity.get('buy_price', 0):,.2f}
<b>Sell:</b> {opportunity.get('sell_exchange', '').upper()} @ ${opportunity.get('sell_price', 0):,.2f}
<b>Spread:</b> {opportunity.get('spread_percent', 0):.2f}%

💰 <b>With ${opportunity.get('amount_usdt', 100)} USDT:</b>
Gross Profit: ${opportunity.get('gross_profit', 0):.2f}
Fees: ${opportunity.get('fees', 0):.2f}
Net Profit: ${opportunity.get('net_profit', 0):.2f} ✅
Profit %: {opportunity.get('profit_percent', 0):.2f}%

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        else:  # Triangular
            message = f"""
🔺 <b>TRIANGULAR ARBITRAGE</b>

<b>Exchange:</b> {opportunity.get('exchange', '').upper()}
<b>Path:</b> {opportunity.get('path', '')}

💰 <b>With ${opportunity.get('start_amount', 100)}:</b>
Final Amount: ${opportunity.get('final_amount', 0):.2f}
Fees: ${opportunity.get('fees', 0):.2f}
Net Profit: ${opportunity.get('net_profit', 0):.2f} ✅
Profit %: {opportunity.get('profit_percent', 0):.2f}%

⏰ {datetime.now().strftime('%H:%M:%S')}
"""
        
        msg_id = self.arbitrage_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.arbitrage_bot.chat_id, "Arbitrage")
            return True
        return False
    
    def send_arbitrage_daily_summary(self, summary: Dict) -> bool:
        """Send arbitrage daily summary"""
        if not self.arbitrage_bot:
            return False
        
        message = f"""
📊 <b>ARBITRAGE DAILY SUMMARY</b>
Date: {summary.get('date', datetime.now().strftime('%Y-%m-%d'))}

🔍 <b>Opportunities Found:</b> {summary.get('total_opportunities', 0)}

💰 <b>Potential Profit:</b>
Total: ${summary.get('total_profit', 0):.2f}
Average per Trade: ${summary.get('avg_profit', 0):.2f}

🏆 <b>Best Opportunity:</b>
{summary.get('best_symbol', 'N/A')}: {summary.get('best_profit_percent', 0):.2f}%

📈 <b>Most Active:</b>
Pair: {summary.get('most_active_pair', 'N/A')}
"""
        msg_id = self.arbitrage_bot.send_message(message)
        if msg_id:
            self._log_message(msg_id, self.arbitrage_bot.chat_id, "Arbitrage")
            return True
        return False
    
    # General methods
    def send_startup_message(self):
        """Send startup notification to all active bots"""
        startup_msg = f"""
🤖 <b>APEX HUNTER V14 STARTED</b>

<b>Environment:</b> {self.config.EXCHANGE_ENVIRONMENT.upper()}

<b>Trading Status:</b>
Futures: {'ENABLED ⚠️' if getattr(self.config, 'FUTURES_TRADING_ENABLED', 'no') == 'yes' else 'DISABLED ✅'}
Spot: {'ENABLED ⚠️' if getattr(self.config, 'SPOT_TRADING_ENABLED', 'no') == 'yes' else 'DISABLED ✅'}
Arbitrage: {'ENABLED ⚠️' if getattr(self.config, 'ARBITRAGE_TRADING_ENABLED', 'no') == 'yes' else 'DISABLED ✅'}

<b>Configured Exchanges:</b>
Futures: {self.config.FUTURES_EXCHANGE.upper()}
Spot: {self.config.SPOT_EXCHANGE.upper()}

Bot is now monitoring markets...
"""
        
        if self.futures_bot:
            msg_id = self.futures_bot.send_message(startup_msg)
            if msg_id: self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
        
        if self.spot_bot:
            msg_id = self.spot_bot.send_message(startup_msg)
            if msg_id: self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
        
        if self.arbitrage_bot:
            msg_id = self.arbitrage_bot.send_message(startup_msg)
            if msg_id: self._log_message(msg_id, self.arbitrage_bot.chat_id, "Arbitrage")
    
    def send_error_alert(self, error_msg: str, module: str = "System"):
        """Send error alert to all active bots"""
        alert = f"""
🚨 <b>ERROR ALERT</b>

<b>Module:</b> {module}
<b>Error:</b> {error_msg}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""
        
        if self.futures_bot:
            msg_id = self.futures_bot.send_alert("Error Alert", alert, "ERROR")
            if msg_id: self._log_message(msg_id, self.futures_bot.chat_id, "Futures")
        
        if self.spot_bot:
            msg_id = self.spot_bot.send_alert("Error Alert", alert, "ERROR")
            if msg_id: self._log_message(msg_id, self.spot_bot.chat_id, "Spot")
        
        if self.arbitrage_bot:
            msg_id = self.arbitrage_bot.send_alert("Error Alert", alert, "ERROR")
            if msg_id: self._log_message(msg_id, self.arbitrage_bot.chat_id, "Arbitrage")


# For backwards compatibility and convenience
TelegramNotifier = TelegramNotificationManager
