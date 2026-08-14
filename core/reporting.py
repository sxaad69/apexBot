"""
Reporting Mixin — extracted from main.py PaperTradingEngine (pure move, no logic change).

Holds all hourly-report generation and dispatch (Telegram):
  - _aggregate_hourly_report_data
  - _send_hourly_reports_from_db / _send_futures/spot/arbitrage_hourly_report_from_db
  - _check_and_send_hourly_report
  - _send_hourly_reports / _send_futures/spot/arbitrage_hourly_report
  - print_summary

Mixin design: PaperTradingEngine inherits this, so all self.* references resolve
to the engine instance exactly as before. Method names/signatures are unchanged.
"""

from datetime import datetime, timedelta


class ReportingMixin:

    def _aggregate_hourly_report_data(self):
        """Aggregate hourly report data from activity_log.db and apex_hunter.db"""
        try:
            now = datetime.now()
            start_time = self.last_report_time
            
            report_data = {
                'futures': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'spot': {'total_analyses': 0, 'signals_generated': 0, 'total_rejections': 0, 'trades_opened': 0},
                'arbitrage': {'total_analyses': 0, 'opportunities_found': 0, 'trades_executed': 0, 'total_rejections': 0}
            }

            # 1. Pull Scanning Data from activity_log.db
            try:
                import sqlite3, json
                conn = sqlite3.connect('data/activity_log.db')
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                
                # Query sweep summaries for the reporting period
                start_str = start_time.strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("SELECT metadata FROM activity_log WHERE type = 'sweep_summary' AND timestamp >= ?", (start_str,))
                
                for row in cursor.fetchall():
                    meta = json.loads(row['metadata'])
                    # Aggregate Analysis Count
                    scanned = meta.get('symbols_scanned', 0)
                    report_data['futures']['total_analyses'] += scanned
                    
                    # Aggregate Rejection Count
                    rejections = meta.get('strategy_rejections', {})
                    for strat_rej in rejections.values():
                        for sym_list in strat_rej.values():
                            report_data['futures']['total_rejections'] += len(sym_list)
                
                conn.close()
            except Exception as e:
                self.logger.error(f"Error pulling scanning data from activity_log: {e}")

            # 2. Pull Trade Data from apex_hunter.db
            try:
                conn = self.db._get_connection(self.db.main_db)
                try:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM trades WHERE entry_time >= ?", (start_time.isoformat(),))
                    report_data['futures']['trades_opened'] = cursor.fetchone()[0]
                    report_data['futures']['signals_generated'] = report_data['futures']['trades_opened']
                finally:
                    conn.close()
            except Exception as e:
                self.logger.error(f"Error pulling trade data: {e}")

            return report_data

        except Exception as e:
            self.logger.error(f"Error aggregating hourly report data: {e}")
            return report_data
    def _send_hourly_reports_from_db(self, report_data):
        """Send hourly reports using database data"""
        try:
            # Generate time range for the report
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=self.report_interval_hours)
            time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M UTC')}"

            # Send futures report
            self._send_futures_hourly_report_from_db(report_data['futures'], time_range)

            # Send spot report (if spot trading enabled)
            if hasattr(self.config, 'ENABLE_SPOT_TRADING') and self.config.ENABLE_SPOT_TRADING:
                self._send_spot_hourly_report_from_db(report_data['spot'], time_range)

            # Send arbitrage report (if arbitrage enabled)
            if hasattr(self.config, 'ENABLE_ARBITRAGE_SCANNER') and self.config.ENABLE_ARBITRAGE_SCANNER:
                self._send_arbitrage_hourly_report_from_db(report_data['arbitrage'], time_range)

        except Exception as e:
            self.logger.error(f"Error sending hourly reports from database: {e}")
    def _send_futures_hourly_report_from_db(self, futures_data, time_range):
        """Send futures hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY FUTURES REPORT
⏰ {time_range}

🔄 Market Analysis:
• Total Analyses: {futures_data['total_analyses']:,}
• Signals Generated: {futures_data['signals_generated']:,}
• Total Rejections: {futures_data['total_rejections']:,}
• Trades Opened: {futures_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(futures_data['signals_generated'] / max(futures_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(futures_data['trades_opened'] / max(futures_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to futures Telegram bot
            if self.telegram and hasattr(self.telegram, 'futures_bot') and self.telegram.futures_bot:
                self.telegram.futures_bot.send_message(
                    message=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Futures hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Futures Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending futures hourly report from database: {e}")
    def _send_spot_hourly_report_from_db(self, spot_data, time_range):
        """Send spot hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY SPOT REPORT
⏰ {time_range}

💰 Market Analysis:
• Total Analyses: {spot_data['total_analyses']:,}
• Signals Generated: {spot_data['signals_generated']:,}
• Total Rejections: {spot_data['total_rejections']:,}
• Trades Opened: {spot_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(spot_data['signals_generated'] / max(spot_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(spot_data['trades_opened'] / max(spot_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to spot Telegram bot
            if self.telegram and hasattr(self.telegram, 'spot_bot') and self.telegram.spot_bot:
                self.telegram.spot_bot.send_message(
                    message=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Spot hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Spot Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending spot hourly report from database: {e}")
    def _send_arbitrage_hourly_report_from_db(self, arb_data, time_range):
        """Send arbitrage hourly report using database data"""
        try:
            report_message = f"""
📊 HOURLY ARBITRAGE REPORT
⏰ {time_range}

🔀 Arbitrage Activity:
• Opportunities Found: {arb_data['opportunities_found']:,}
• Trades Executed: {arb_data['trades_executed']:,}
• Total Rejections: {arb_data['total_rejections']:,}

📈 Performance:
• Execution Rate: {(arb_data['trades_executed'] / max(arb_data['opportunities_found'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to arbitrage Telegram bot
            if self.telegram and hasattr(self.telegram, 'arbitrage_bot') and self.telegram.arbitrage_bot:
                self.telegram.arbitrage_bot.send_message(
                    message=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Arbitrage hourly report sent to Telegram (from database)")
            else:
                self.logger.warning("Arbitrage Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending arbitrage hourly report from database: {e}")
    def _check_and_send_hourly_report(self):
        """Check if it's time to send hourly report and send if needed"""
        if not self.hourly_reports_enabled:
            return

        now = datetime.now()
        time_since_last_report = (now - self.last_report_time).total_seconds() / 3600  # Hours

        if time_since_last_report >= self.report_interval_hours:
            # Aggregate data from database for the reporting period
            report_data = self._aggregate_hourly_report_data()

            # Send hourly reports using database data
            self._send_hourly_reports_from_db(report_data)
            self.last_report_time = now
    def _send_hourly_reports(self):
        """Generate and send hourly reports to appropriate Telegram bots"""
        try:
            # Generate time range for the report
            end_time = datetime.now()
            start_time = end_time - timedelta(hours=self.report_interval_hours)
            time_range = f"{start_time.strftime('%H:%M')} - {end_time.strftime('%H:%M UTC')}"

            # Send futures report
            self._send_futures_hourly_report(time_range)

            # Send spot report (if spot trading enabled)
            if hasattr(self.config, 'ENABLE_SPOT_TRADING') and self.config.ENABLE_SPOT_TRADING:
                self._send_spot_hourly_report(time_range)

            # Send arbitrage report (if arbitrage enabled)
            if hasattr(self.config, 'ENABLE_ARBITRAGE_SCANNER') and self.config.ENABLE_ARBITRAGE_SCANNER:
                self._send_arbitrage_hourly_report(time_range)

        except Exception as e:
            self.logger.error(f"Error sending hourly reports: {e}")
    def _send_futures_hourly_report(self, time_range):
        """Send futures hourly report to Telegram"""
        try:
            futures_data = self.hourly_metrics['futures']

            report_message = f"""
📊 HOURLY FUTURES REPORT
⏰ {time_range}

🔄 Market Analysis:
• Total Analyses: {futures_data['total_analyses']:,}
• Signals Generated: {futures_data['signals_generated']:,}
• Total Rejections: {futures_data['total_rejections']:,}
• Trades Opened: {futures_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(futures_data['signals_generated'] / max(futures_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(futures_data['trades_opened'] / max(futures_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to futures Telegram bot
            if self.telegram and hasattr(self.telegram, 'futures_bot') and self.telegram.futures_bot:
                self.telegram.futures_bot.send_message(
                    chat_id=self.config.TELEGRAM_FUTURES_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Futures hourly report sent to Telegram")
            else:
                self.logger.warning("Futures Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending futures hourly report: {e}")
    def _send_spot_hourly_report(self, time_range):
        """Send spot hourly report to Telegram"""
        try:
            spot_data = self.hourly_metrics['spot']

            report_message = f"""
📊 HOURLY SPOT REPORT
⏰ {time_range}

💰 Market Analysis:
• Total Analyses: {spot_data['total_analyses']:,}
• Signals Generated: {spot_data['signals_generated']:,}
• Total Rejections: {spot_data['total_rejections']:,}
• Trades Opened: {spot_data['trades_opened']:,}

📈 Performance:
• Signal Rate: {(spot_data['signals_generated'] / max(spot_data['total_analyses'], 1) * 100):.1f}%
• Conversion Rate: {(spot_data['trades_opened'] / max(spot_data['signals_generated'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to spot Telegram bot
            if self.telegram and hasattr(self.telegram, 'spot_bot') and self.telegram.spot_bot:
                self.telegram.spot_bot.send_message(
                    chat_id=self.config.TELEGRAM_SPOT_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Spot hourly report sent to Telegram")
            else:
                self.logger.warning("Spot Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending spot hourly report: {e}")
    def _send_arbitrage_hourly_report(self, time_range):
        """Send arbitrage hourly report to Telegram"""
        try:
            arb_data = self.hourly_metrics['arbitrage']

            report_message = f"""
📊 HOURLY ARBITRAGE REPORT
⏰ {time_range}

🔀 Arbitrage Activity:
• Opportunities Found: {arb_data['opportunities_found']:,}
• Trades Executed: {arb_data['trades_executed']:,}
• Total Rejections: {arb_data['total_rejections']:,}

📈 Performance:
• Execution Rate: {(arb_data['trades_executed'] / max(arb_data['opportunities_found'], 1) * 100):.1f}%

APEX HUNTER V14 🤖
"""

            # Send to arbitrage Telegram bot
            if self.telegram and hasattr(self.telegram, 'arbitrage_bot') and self.telegram.arbitrage_bot:
                self.telegram.arbitrage_bot.send_message(
                    chat_id=self.config.TELEGRAM_ARBITRAGE_CHAT_ID,
                    text=report_message.strip(),
                    parse_mode='HTML'
                )
                self.logger.info("Arbitrage hourly report sent to Telegram")
            else:
                self.logger.warning("Arbitrage Telegram bot not available for hourly reports")

        except Exception as e:
            self.logger.error(f"Error sending arbitrage hourly report: {e}")
    def print_summary(self):
        """Print trading summary"""
        print("\n" + "=" * 80)
        print("  PAPER TRADING SUMMARY")
        print("=" * 80)

        initial_capital = getattr(self.config, 'FUTURES_VIRTUAL_CAPITAL', 100)

        if not self.trades:
            print("\n  No trades executed.")
            print("\n" + "=" * 80 + "\n")
            return

        # Summary by strategy
        for strategy in self.strategies:
            strategy_trades = [t for t in self.trades if t['strategy'] == strategy.name]

            if strategy_trades:
                wins = [t for t in strategy_trades if t['pnl'] > 0]
                total_pnl = sum(t['pnl'] for t in strategy_trades)
                win_rate = len(wins) / len(strategy_trades) * 100
                avg_leverage = sum(t.get('leverage', 1) for t in strategy_trades) / len(strategy_trades)

                print(f"\n  {strategy.name}:")
                print(f"    Trades: {len(strategy_trades)}")
                print(f"    Wins: {len(wins)} ({win_rate:.1f}%)")
                print(f"    Avg Leverage: {avg_leverage:.1f}x")
                print(f"    Final Shared Capital: ${self.total_capital:.2f}")
                print(f"    Total Strategy P&L: ${total_pnl:+.2f}")

                # Show breakdown by symbol
                symbols = set(t['symbol'] for t in strategy_trades)
                if len(symbols) > 1:
                    print(f"    Breakdown by symbol:")
                    for sym in sorted(symbols):
                        sym_trades = [t for t in strategy_trades if t['symbol'] == sym]
                        sym_pnl = sum(t['pnl'] for t in sym_trades)
                        print(f"      {sym}: {len(sym_trades)} trades, ${sym_pnl:+.2f}")

        print("\n" + "=" * 80 + "\n")
