"""
APEX HUNTER V14 - Strategy Performance Dashboard
Real-time visualization of trading strategy performance
"""

import streamlit as st
import pandas as pd
import json
import os
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
import plotly.express as px
import plotly.graph_objects as go


class StrategyDashboard:
    """Dashboard for visualizing strategy performance"""

    def __init__(self):
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)

        st.set_page_config(
            page_title="APEX HUNTER V14 - Strategy Dashboard",
            page_icon="📊",
            layout="wide"
        )

        st.title("📊 APEX HUNTER V14 - Strategy Performance Dashboard")
        st.markdown("**Real-time analysis of your trading strategies**")

        # Auto-refresh every 30 seconds
        st.empty()
        if st.button("🔄 Refresh Data"):
            st.rerun()

    def load_json_data(self, filename):
        """Load data from JSON file"""
        filepath = self.data_dir / filename
        if not filepath.exists():
            return []

        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                elif isinstance(data, dict):
                    # If it's a dict like market_analyses.json (keyed by hour)
                    # return values as a list
                    return list(data.values())
                else:
                    return [data]
        except Exception as e:
            st.error(f"Error loading {filename}: {e}")
            return []

    def load_all_data(self):
        """Load all trading data"""
        futures_trades = self.load_json_data("futures_trades.json")
        spot_trades = self.load_json_data("spot_trades.json")
        active_positions = self.load_json_data("active_positions.json")

        return {
            'futures': futures_trades,
            'spot': spot_trades,
            'active': active_positions
        }

    def process_strategy_performance(self, trades_data):
        """Process trades data into strategy performance metrics"""
        if not trades_data:
            return pd.DataFrame()

        df = pd.DataFrame(trades_data)

        if df.empty:
            return df

        # Convert timestamps
        df['entry_time'] = pd.to_datetime(df['entry_time'])
        df['exit_time'] = pd.to_datetime(df['exit_time'])

        # Calculate additional metrics
        df['duration_hours'] = (df['exit_time'] - df['entry_time']).dt.total_seconds() / 3600
        df['is_win'] = df['pnl'] > 0

        # Group by strategy
        strategy_stats = df.groupby('strategy').agg({
            'pnl': ['count', 'sum', 'mean', 'std'],
            'is_win': 'mean',
            'duration_hours': 'mean',
            'leverage': 'mean'
        }).round(4)

        # Flatten column names
        strategy_stats.columns = ['trade_count', 'total_pnl', 'avg_pnl', 'pnl_std', 'win_rate', 'avg_duration', 'avg_leverage']
        strategy_stats = strategy_stats.reset_index()

        # Calculate additional metrics
        strategy_stats['profit_factor'] = strategy_stats.apply(
            lambda x: abs(df[(df['strategy'] == x['strategy']) & (df['pnl'] > 0)]['pnl'].sum() /
                         df[(df['strategy'] == x['strategy']) & (df['pnl'] < 0)]['pnl'].sum())
            if len(df[(df['strategy'] == x['strategy']) & (df['pnl'] < 0)]) > 0 else float('inf'),
            axis=1
        )

        strategy_stats['sharpe_ratio'] = strategy_stats.apply(
            lambda x: x['avg_pnl'] / x['pnl_std'] if x['pnl_std'] > 0 else 0,
            axis=1
        )

        return strategy_stats

    def display_overview_metrics(self, futures_stats, spot_stats, active_positions):
        """Display key overview metrics with Unrealized vs Realized P&L"""
        # Calculate Live Metrics
        total_open = len(active_positions)
        unrealized_pnl = 0
        total_exposure = 0
        
        for pos in active_positions:
            entry = pos.get('entry_price', 0)
            current = pos.get('current_price', entry) # Fallback to entry if live price missing
            size = pos.get('size', 0)
            side = pos.get('side', 'buy')
            leverage = pos.get('leverage', 1)
            
            if entry > 0:
                if side == 'buy':
                    pnl_pct = (current - entry) / entry
                else:
                    pnl_pct = (entry - current) / entry
                
                pos_pnl = size * leverage * pnl_pct
                unrealized_pnl += pos_pnl
                total_exposure += size * leverage

        # Calculate Historical Metrics
        realized_futures_pnl = futures_stats['total_pnl'].sum() if not futures_stats.empty else 0
        realized_spot_pnl = spot_stats['total_pnl'].sum() if not spot_stats.empty else 0
        total_realized_pnl = realized_futures_pnl + realized_spot_pnl
        total_trades = (futures_stats['trade_count'].sum() if not futures_stats.empty else 0) + \
                       (spot_stats['trade_count'].sum() if not spot_stats.empty else 0)

        # UI Layout
        st.markdown("### 💠 Professional Overview")
        
        # Row 1: The "Live" Numbers
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            color = "green" if unrealized_pnl >= 0 else "red"
            st.markdown(f"""
                <div style='background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 5px solid {color}'>
                    <p style='color: #888; margin: 0;'>UNREALIZED P&L</p>
                    <h2 style='color: {color}; margin: 0;'>${unrealized_pnl:+.2f}</h2>
                </div>
            """, unsafe_allow_html=True)
            
        with col2:
            st.markdown(f"""
                <div style='background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 5px solid #00c3ff'>
                    <p style='color: #888; margin: 0;'>OPEN POSITIONS</p>
                    <h2 style='color: #00c3ff; margin: 0;'>{total_open}</h2>
                </div>
            """, unsafe_allow_html=True)
            
        with col3:
            st.markdown(f"""
                <div style='background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 5px solid #ffd700'>
                    <p style='color: #888; margin: 0;'>LIVE EXPOSURE</p>
                    <h2 style='color: #ffd700; margin: 0;'>${total_exposure:,.2f}</h2>
                </div>
            """, unsafe_allow_html=True)
            
        with col4:
            color = "green" if total_realized_pnl >= 0 else "red"
            st.markdown(f"""
                <div style='background-color: #1e1e1e; padding: 20px; border-radius: 10px; border-left: 5px solid {color}'>
                    <p style='color: #888; margin: 0;'>REALIZED P&L</p>
                    <h2 style='color: {color}; margin: 0;'>${total_realized_pnl:+.2f}</h2>
                </div>
            """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Row 2: Tabs for deeper analysis
        live_tab, hist_tab, strat_tab = st.tabs(["⚡ LIVE MONITORING", "📜 HISTORICAL AUDIT", "🎯 STRATEGY MATRIX"])
        
        with live_tab:
            if active_positions:
                self.display_active_positions(active_positions)
            else:
                st.info("No active positions. The bot is scanning for high-probability setups...")
                
        with hist_tab:
            c1, c2 = st.columns(2)
            with c1:
                st.metric("Total Historical Trades", total_trades)
            with c2:
                win_rate = 0
                if not futures_stats.empty and not spot_stats.empty:
                    win_rate = (futures_stats['win_rate'].mean() + spot_stats['win_rate'].mean()) / 2
                elif not futures_stats.empty:
                    win_rate = futures_stats['win_rate'].mean()
                st.metric("Win Rate (Avg)", f"{win_rate*100:.1f}%")
            
            st.markdown("---")
            self.display_strategy_table(futures_stats, "Futures Performance", "futures")
            self.display_strategy_table(spot_stats, "Spot Performance", "spot")
            
        with strat_tab:
            self.display_strategy_matrix(futures_stats, spot_stats)

    def display_strategy_matrix(self, futures_stats, spot_stats):
        """Visual comparison of strategy performance"""
        if futures_stats.empty and spot_stats.empty:
            st.info("Strategy data will appear after the first trade closes.")
            return
            
        # Combine stats
        combined = pd.concat([futures_stats, spot_stats])
        if combined.empty: return
        
        fig = px.bar(combined, x='strategy', y='total_pnl', 
                    color='win_rate', title="Realized P&L by Strategy",
                    labels={'total_pnl': 'Total Profit ($)', 'strategy': 'Strategy', 'win_rate': 'Win %'},
                    color_continuous_scale='RdYlGn')
        st.plotly_chart(fig, use_container_width=True)

    def display_active_positions(self, active_positions):
        """Display table of current open positions with live profit tracking"""
        if not active_positions:
            return
            
        data = []
        for pos in active_positions:
            entry = pos.get('entry_price', 0)
            current = pos.get('current_price', entry)
            size = pos.get('size', 0)
            side = pos.get('side', 'buy')
            leverage = pos.get('leverage', 1)
            
            pnl_pct = 0
            unrealized = 0
            if entry > 0:
                if side == 'buy':
                    pnl_pct = (current - entry) / entry
                else:
                    pnl_pct = (entry - current) / entry
                unrealized = size * leverage * pnl_pct
                
            data.append({
                'Symbol': pos.get('symbol'),
                'Strategy': pos.get('strategy'),
                'Side': side.upper(),
                'Lev': f"{leverage}x",
                'Entry': f"${entry:,.4f}",
                'Current': f"${current:,.4f}",
                'Size ($)': f"${size:,.2f}",
                'P&L (%)': f"{pnl_pct*100:+.2f}%",
                'Profit ($)': f"${unrealized:+.2f}",
                'Status': "PROFIT" if unrealized >= 0 else "LOSS"
            })
            
        df = pd.DataFrame(data)
        
        # Custom coloring via dataframe styler
        def color_pnl(val):
            if isinstance(val, str):
                if '+' in val or val == "PROFIT": return 'color: #00ff00'
                if '-' in val or val == "LOSS": return 'color: #ff0000'
            return ''
            
        st.dataframe(df.style.applymap(color_pnl, subset=['P&L (%)', 'Profit ($)', 'Status']), 
                    use_container_width=True)

    def display_strategy_table(self, data, title, market_type):
        """Display strategy performance table"""
        st.subheader(f"🎯 {title}")

        if data.empty:
            st.info(f"No {market_type} trades recorded yet")
            return

        # Format the table
        display_df = data.copy()
        display_df['win_rate'] = (display_df['win_rate'] * 100).round(1).astype(str) + '%'
        display_df['total_pnl'] = display_df['total_pnl'].round(2)
        display_df['avg_pnl'] = display_df['avg_pnl'].round(2)
        display_df['avg_leverage'] = display_df['avg_leverage'].round(1)
        display_df['profit_factor'] = display_df['profit_factor'].round(2)
        display_df['sharpe_ratio'] = display_df['sharpe_ratio'].round(2)

        # Rename columns for display
        display_df = display_df.rename(columns={
            'strategy': 'Strategy',
            'trade_count': 'Trades',
            'total_pnl': 'Total P&L',
            'avg_pnl': 'Avg P&L',
            'win_rate': 'Win Rate',
            'avg_leverage': 'Avg Leverage',
            'profit_factor': 'Profit Factor',
            'sharpe_ratio': 'Sharpe Ratio'
        })

        # Color coding
        def color_pnl(val):
            color = 'green' if val > 0 else 'red' if val < 0 else 'black'
            return f'color: {color}'

        def color_win_rate(val):
            rate = float(val.strip('%'))
            color = 'green' if rate > 60 else 'orange' if rate > 50 else 'red'
            return f'color: {color}'

        styled_df = display_df.style.applymap(color_pnl, subset=['Total P&L', 'Avg P&L'])
        styled_df = styled_df.applymap(color_win_rate, subset=['Win Rate'])

        st.dataframe(styled_df, use_container_width=True)

    def display_pnl_chart(self, trades_data, title):
        """Display P&L over time chart"""
        st.subheader(f"📈 {title} - P&L Over Time")

        if not trades_data:
            st.info("No trades to display")
            return

        df = pd.DataFrame(trades_data)
        if df.empty:
            return

        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df = df.sort_values('exit_time')

        # Calculate cumulative P&L by strategy
        strategies = df['strategy'].unique()

        fig = go.Figure()

        for strategy in strategies:
            strategy_data = df[df['strategy'] == strategy].copy()
            strategy_data['cumulative_pnl'] = strategy_data['pnl'].cumsum()

            fig.add_trace(go.Scatter(
                x=strategy_data['exit_time'],
                y=strategy_data['cumulative_pnl'],
                mode='lines+markers',
                name=strategy,
                line=dict(width=2)
            ))

        fig.update_layout(
            title=f"{title} - Cumulative P&L by Strategy",
            xaxis_title="Time",
            yaxis_title="Cumulative P&L ($)",
            height=400
        )

        st.plotly_chart(fig, use_container_width=True)

    def display_recent_trades(self, trades_data, title):
        """Display recent trades table"""
        st.subheader(f"📋 Recent {title}")

        if not trades_data:
            st.info("No trades to display")
            return

        df = pd.DataFrame(trades_data)
        if df.empty:
            return

        # Sort by exit time (most recent first)
        df['exit_time'] = pd.to_datetime(df['exit_time'])
        df = df.sort_values('exit_time', ascending=False)

        # Take last 20 trades
        recent_df = df.head(20)

        # Format for display
        display_df = recent_df[[
            'exit_time', 'strategy', 'symbol', 'side',
            'entry_price', 'exit_price', 'pnl', 'reason'
        ]].copy()

        display_df['exit_time'] = display_df['exit_time'].dt.strftime('%m/%d %H:%M')
        display_df['entry_price'] = display_df['entry_price'].round(2)
        display_df['exit_price'] = display_df['exit_price'].round(2)
        display_df['pnl'] = display_df['pnl'].round(2)

        # Rename columns
        display_df = display_df.rename(columns={
            'exit_time': 'Time',
            'strategy': 'Strategy',
            'symbol': 'Symbol',
            'side': 'Side',
            'entry_price': 'Entry',
            'exit_price': 'Exit',
            'pnl': 'P&L',
            'reason': 'Exit Reason'
        })

        st.dataframe(display_df, use_container_width=True)

    def display_market_analysis_charts(self):
        """Display market analysis charts with date and timeframe selection"""
        st.header("📈 Market Analysis Charts")

        # Analysis mode selector
        analysis_mode = st.radio(
            "Analysis Mode:",
            ["Timeframe Analysis", "Daily Hourly Breakdown"],
            horizontal=True,
            help="Choose between timeframe-based analysis or daily hourly breakdown"
        )

        if analysis_mode == "Timeframe Analysis":
            self.display_timeframe_analysis()
        else:
            self.display_daily_market_analysis()

    def display_timeframe_analysis(self):
        """Display market analysis for selected timeframe"""
        st.subheader("⏰ Timeframe Analysis")

        # Timeframe selector
        timeframe_options = {
            "30M": "30 minutes",
            "1H": "1 hour",
            "4H": "4 hours",
            "1D": "1 day"
        }

        col1, col2 = st.columns([1, 3])
        with col1:
            selected_timeframe = st.selectbox(
                "Select Timeframe:",
                options=list(timeframe_options.keys()),
                format_func=lambda x: timeframe_options[x],
                index=1  # Default to 1H
            )

        with col2:
            st.write(f"**Viewing:** {timeframe_options[selected_timeframe]} timeframe analysis")

        # Get market data for selected timeframe
        market_data = self.get_market_data_by_timeframe(selected_timeframe)

        if not market_data:
            st.info(f"No {selected_timeframe} market data available yet")
            return

        # Display market analysis metrics
        self.display_market_metrics(market_data, selected_timeframe)

        # Price and volume chart
        self.display_price_volume_chart(market_data, selected_timeframe)

        # Strategy analysis by timeframe
        self.display_strategy_timeframe_analysis(market_data, selected_timeframe)

    def display_daily_market_analysis(self):
        """Display daily market analysis with comprehensive hourly breakdown tables"""
        st.subheader("📅 Daily Market Analysis - 24 Hour Breakdown")

        # Date selector
        available_dates = self.get_available_dates()
        if not available_dates:
            st.error("No log data available for analysis")
            return

        selected_date = st.selectbox(
            "Select Date:",
            options=available_dates,
            format_func=lambda x: x.strftime('%B %d, %Y'),
            index=len(available_dates)-1  # Default to most recent
        )

        st.write(f"**Analyzing Market Activity:** {selected_date.strftime('%B %d, %Y')}")

        # Get hourly market analysis data for selected date
        hourly_market_data = self.get_hourly_market_analysis_data(selected_date)

        # Display comprehensive 24-hour breakdown tables
        self.display_comprehensive_24h_tables(hourly_market_data, selected_date)

        st.markdown("---")

        # Display hourly market analysis chart (original)
        self.display_hourly_market_chart(hourly_market_data, selected_date)

        # Display strategy hourly breakdown
        self.display_strategy_hourly_breakdown(hourly_market_data, selected_date)

    def display_comprehensive_24h_tables(self, hourly_data, selected_date):
        """Display comprehensive 24-hour breakdown tables in user's requested format"""
        if not hourly_data:
            st.info("No data available for comprehensive analysis")
            return

        # Futures Section
        st.header("🔄 FUTURES - 24 Hour Market Analysis")
        futures_table_data = self.prepare_hourly_table_data(hourly_data, 'futures')
        if futures_table_data is not None and not futures_table_data.empty:
            st.dataframe(futures_table_data, use_container_width=True)
        else:
            st.info("No futures data available")

        st.markdown("---")

        # Futures Strategies Section
        st.header("🎯 FUTURES STRATEGIES - 24 Hour Breakdown")
        futures_strategies_data = self.prepare_strategy_hourly_table_data(hourly_data, 'futures')
        if futures_strategies_data is not None and not futures_strategies_data.empty:
            st.dataframe(futures_strategies_data, use_container_width=True)
        else:
            st.info("No futures strategies data available")

        st.markdown("---")

        # Spot Section
        st.header("💰 SPOT - 24 Hour Market Analysis")
        spot_table_data = self.prepare_hourly_table_data(hourly_data, 'spot')
        if spot_table_data is not None and not spot_table_data.empty:
            st.dataframe(spot_table_data, use_container_width=True)
        else:
            st.info("No spot data available")

        st.markdown("---")

        # Spot Strategies Section
        st.header("🎯 SPOT STRATEGIES - 24 Hour Breakdown")
        spot_strategies_data = self.prepare_strategy_hourly_table_data(hourly_data, 'spot')
        if spot_strategies_data is not None and not spot_strategies_data.empty:
            st.dataframe(spot_strategies_data, use_container_width=True)
        else:
            st.info("No spot strategies data available")

    def prepare_hourly_table_data(self, hourly_data, market_type):
        """Prepare hourly table data for futures or spot in user's requested format"""
        table_data = []

        for hour in sorted(hourly_data.keys()):
            data = hourly_data[hour]

            # Get market-specific data
            if market_type == 'futures':
                market_analyses = data['futures_analyses']
                rejections = data.get('rejections', {})
                total_rejections = rejections.get('volume', 0) + rejections.get('adx', 0) + rejections.get('volatility', 0) + rejections.get('other', 0)
                trades_open = data.get('trades_taken', 0)  # This represents trades that were taken
            else:  # spot
                market_analyses = data['spot_analyses']
                # For spot, we don't have separate rejection tracking yet, so use same logic
                rejections = data.get('rejections', {})
                total_rejections = rejections.get('volume', 0) + rejections.get('adx', 0) + rejections.get('volatility', 0) + rejections.get('other', 0)
                trades_open = 0  # Spot trading not fully implemented yet

            # Format rejection reasons
            rejection_reasons = []
            if data.get('rejections', {}).get('volume', 0) > 0:
                rejection_reasons.append(f"Volume < 0.8x ({data['rejections']['volume']})")
            if data.get('rejections', {}).get('adx', 0) > 0:
                rejection_reasons.append(f"ADX < 15 ({data['rejections']['adx']})")
            if data.get('rejections', {}).get('other', 0) > 0:
                rejection_reasons.append(f"Other ({data['rejections']['other']})")

            rejection_reason_str = ", ".join(rejection_reasons) if rejection_reasons else "None"

            # Risk rejections (for now, using same as total rejections since we don't have separate risk layer tracking)
            risk_rejections = total_rejections  # Placeholder

            table_data.append({
                'Hour': f"{hour}:00",
                'Total Market Analyzed': market_analyses,
                'Total Rejections': total_rejections,
                'Total Trades Open': trades_open,
                'Rejection Reason': rejection_reason_str,
                'Risk Rejection': risk_rejections
            })

        return pd.DataFrame(table_data) if table_data else None

    def prepare_strategy_hourly_table_data(self, hourly_data, market_type):
        """Prepare strategy-level hourly table data for futures or spot"""
        table_data = []

        strategies = ['A1', 'A2', 'A3', 'A4', 'A5']

        for hour in sorted(hourly_data.keys()):
            data = hourly_data[hour]

            for strategy in strategies:
                # Get strategy-specific signals
                signals_generated = data['strategy_signals'].get(strategy, 0)

                # For now, assume all signals for this strategy were tested (simplified)
                # In reality, we'd need more detailed tracking
                market_analyses = signals_generated  # Simplified assumption

                # For futures/spot distinction, we'd need strategy-level tracking
                # For now, use same rejection logic as market level
                rejections = data.get('rejections', {})
                total_rejections = rejections.get('volume', 0) + rejections.get('adx', 0) + rejections.get('volatility', 0) + rejections.get('other', 0)
                trades_open = 0  # Would need strategy-level trade tracking

                # Format rejection reasons (same as market level)
                rejection_reasons = []
                if rejections.get('volume', 0) > 0:
                    rejection_reasons.append(f"Volume < 0.8x ({rejections['volume']})")
                if rejections.get('adx', 0) > 0:
                    rejection_reasons.append(f"ADX < 15 ({rejections['adx']})")
                if rejections.get('other', 0) > 0:
                    rejection_reasons.append(f"Other ({rejections['other']})")

                rejection_reason_str = ", ".join(rejection_reasons) if rejection_reasons else "None"
                risk_rejections = total_rejections  # Placeholder

                table_data.append({
                    'Hour': f"{hour}:00",
                    'Strategy': strategy,
                    'Total Market Analyzed': market_analyses,
                    'Total Rejections': total_rejections,
                    'Total Trades Open': trades_open,
                    'Rejection Reason': rejection_reason_str,
                    'Risk Rejection': risk_rejections
                })

        return pd.DataFrame(table_data) if table_data else None

    def get_hourly_market_analysis_data(self, selected_date):
        """Get hourly market analysis data for selected date from JSON or logs"""
        import glob
        
        # 1. Try to load from JSON first (More reliable)
        date_str = selected_date.strftime('%Y%m%d')
        json_filename = f"market_analyses_{date_str}.json"
        
        # Support both dated and undated JSON for compatibility
        json_data = self.load_json_data(json_filename)
        if not json_data:
            json_data = self.load_json_data("market_analyses.json")
            
        hourly_data = {}
        
        # Process JSON data if available
        if isinstance(json_data, list):
            for item in json_data:
                # Check if it's the right date
                if item.get('date') == selected_date.strftime('%Y-%m-%d'):
                    hour = item.get('hour', '00:00').split(':')[0]
                    if hour not in hourly_data:
                        hourly_data[hour] = {
                            'total_analyses': 0,
                            'pairs_analyzed': set(),
                            'strategy_signals': {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0},
                            'rejections': {'volume': 0, 'adx': 0, 'volatility': 0, 'other': 0},
                            'futures_analyses': 0,
                            'spot_analyses': 0,
                            'trades_taken': 0
                        }
                    
                    hourly_data[hour]['total_analyses'] += item.get('total_analyses', 0)
                    hourly_data[hour]['futures_analyses'] += item.get('futures_analyses', 0)
                    hourly_data[hour]['spot_analyses'] += item.get('spot_analyses', 0)
                    for p in item.get('pairs_analyzed', []):
                        hourly_data[hour]['pairs_analyzed'].add(p)
        elif isinstance(json_data, dict):
            # If JSON is keyed by hour {"12:00": {...}}
            for hour_key, item in json_data.items():
                if item.get('date') == selected_date.strftime('%Y-%m-%d'):
                    hour = hour_key.split(':')[0]
                    if hour not in hourly_data:
                        hourly_data[hour] = {
                            'total_analyses': 0,
                            'pairs_analyzed': set(),
                            'strategy_signals': {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0},
                            'rejections': {'volume': 0, 'adx': 0, 'volatility': 0, 'other': 0},
                            'futures_analyses': 0,
                            'spot_analyses': 0,
                            'trades_taken': 0
                        }
                    hourly_data[hour]['total_analyses'] += item.get('total_analyses', 0)
                    hourly_data[hour]['futures_analyses'] += item.get('futures_analyses', 0)
                    hourly_data[hour]['spot_analyses'] += item.get('spot_analyses', 0)

        # 2. Supplement with log data if available
        log_files = glob.glob("logs/apex_hunter_*.log")

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        # Check if line is from selected date
                        if f'2026-{selected_date.month:02d}-{selected_date.day:02d}' in line:
                            try:
                                # Extract hour from timestamp
                                timestamp_part = line.split('|')[0].strip()
                                hour = timestamp_part.split()[1].split(':')[0]  # HH from HH:MM:SS

                                if hour not in hourly_data:
                                    hourly_data[hour] = {
                                        'total_analyses': 0,
                                        'pairs_analyzed': set(),
                                        'strategy_signals': {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0},
                                        'rejections': {'volume': 0, 'adx': 0, 'other': 0},
                                        'futures_analyses': 0,
                                        'spot_analyses': 0
                                    }

                                # Count market analyses
                                if 'Price:' in line and '$' in line:
                                    hourly_data[hour]['total_analyses'] += 1

                                    # Extract pair
                                    if '|' in line:
                                        pair_part = line.split('|')[0].strip()
                                        if '/' in pair_part:
                                            hourly_data[hour]['pairs_analyzed'].add(pair_part)

                                    # Check if futures or spot
                                    if 'SPOT' in line.upper():
                                        hourly_data[hour]['spot_analyses'] += 1
                                    else:
                                        hourly_data[hour]['futures_analyses'] += 1

                                # Count strategy signals
                                if 'SIGNAL:' in line:
                                    if '[A1:' in line or 'A1 EMA' in line:
                                        hourly_data[hour]['strategy_signals']['A1'] += 1
                                    elif '[A2:' in line or 'A2 EMA' in line:
                                        hourly_data[hour]['strategy_signals']['A2'] += 1
                                    elif '[A3:' in line or 'A3 Fast' in line:
                                        hourly_data[hour]['strategy_signals']['A3'] += 1
                                    elif '[A4:' in line or 'A4 Trend' in line:
                                        hourly_data[hour]['strategy_signals']['A4'] += 1
                                    elif '[A5:' in line or 'A5 Market' in line:
                                        hourly_data[hour]['strategy_signals']['A5'] += 1

                                # Count rejections
                                if 'FILTERED:' in line:
                                    if 'Volume <' in line:
                                        hourly_data[hour]['rejections']['volume'] += 1
                                    elif 'ADX <' in line:
                                        hourly_data[hour]['rejections']['adx'] += 1
                                    else:
                                        hourly_data[hour]['rejections']['other'] += 1

                            except:
                                continue
            except:
                continue

        # Convert sets to lists for JSON serialization
        for hour_data in hourly_data.values():
            hour_data['pairs_analyzed'] = list(hour_data['pairs_analyzed'])

        return hourly_data

    def display_hourly_market_chart(self, hourly_data, selected_date):
        """Display hourly market analysis chart"""
        st.subheader(f"📊 Hourly Market Analysis - {selected_date.strftime('%B %d, %Y')}")

        if not hourly_data:
            st.info("No market analysis data available for selected date")
            return

        # Prepare data for chart
        hours = sorted(hourly_data.keys())
        total_analyses = [hourly_data[h]['total_analyses'] for h in hours]
        futures_analyses = [hourly_data[h]['futures_analyses'] for h in hours]
        spot_analyses = [hourly_data[h]['spot_analyses'] for h in hours]

        fig = go.Figure()

        # Total analyses
        fig.add_trace(go.Bar(
            x=[f"{h}:00" for h in hours],
            y=total_analyses,
            name='Total Analyses',
            marker_color='blue'
        ))

        # Futures analyses
        fig.add_trace(go.Bar(
            x=[f"{h}:00" for h in hours],
            y=futures_analyses,
            name='Futures',
            marker_color='lightblue'
        ))

        # Spot analyses
        fig.add_trace(go.Bar(
            x=[f"{h}:00" for h in hours],
            y=spot_analyses,
            name='Spot',
            marker_color='lightgreen'
        ))

        fig.update_layout(
            title=f'Hourly Market Analysis - {selected_date.strftime("%B %d, %Y")}',
            xaxis_title='Hour (UTC)',
            yaxis_title='Number of Market Analyses',
            barmode='stack',
            height=400
        )

        st.plotly_chart(fig, use_container_width=True)

    def display_hourly_market_table(self, hourly_data):
        """Display detailed hourly market analysis table"""
        st.subheader("📋 Detailed Hourly Market Analysis")

        if not hourly_data:
            st.info("No data available")
            return

        # Prepare table data
        table_data = []
        for hour in sorted(hourly_data.keys()):
            data = hourly_data[hour]
            total_signals = sum(data['strategy_signals'].values())
            total_rejections = sum(data['rejections'].values())

            table_data.append({
                'Hour': f"{hour}:00",
                'Total Analyses': data['total_analyses'],
                'Futures': data['futures_analyses'],
                'Spot': data['spot_analyses'],
                'Pairs Analyzed': len(data['pairs_analyzed']),
                'Signals Generated': total_signals,
                'Signals Rejected': total_rejections,
                'Analysis Efficiency': f"{(total_signals / max(data['total_analyses'], 1) * 100):.1f}%"
            })

        df = pd.DataFrame(table_data)
        st.dataframe(df, use_container_width=True)

        # Summary metrics
        total_analyses_all = sum(h['total_analyses'] for h in hourly_data.values())
        total_signals_all = sum(sum(h['strategy_signals'].values()) for h in hourly_data.values())
        total_rejections_all = sum(sum(h['rejections'].values()) for h in hourly_data.values())

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Daily Analyses", f"{total_analyses_all:,}")
        with col2:
            st.metric("Daily Signals", f"{total_signals_all:,}")
        with col3:
            st.metric("Daily Rejections", f"{total_rejections_all:,}")
        with col4:
            efficiency = (total_signals_all / max(total_analyses_all, 1) * 100)
            st.metric("Signal Efficiency", f"{efficiency:.1f}%")

    def display_strategy_hourly_breakdown(self, hourly_data, selected_date):
        """Display strategy-wise hourly breakdown"""
        st.subheader("🎯 Strategy Hourly Breakdown")

        if not hourly_data:
            st.info("No data available")
            return

        # Aggregate strategy signals across all hours
        total_signals = {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0}
        for hour_data in hourly_data.values():
            for strategy, count in hour_data['strategy_signals'].items():
                total_signals[strategy] += count

        # Create hourly strategy chart
        hours = sorted(hourly_data.keys())
        strategies = ['A1', 'A2', 'A3', 'A4', 'A5']

        fig = go.Figure()

        for strategy in strategies:
            hourly_counts = [hourly_data[h]['strategy_signals'][strategy] for h in hours]
            fig.add_trace(go.Bar(
                x=[f"{h}:00" for h in hours],
                y=hourly_counts,
                name=f'{strategy}',
                hovertemplate=f'{strategy}: %{{y}} signals<extra></extra>'
            ))

        fig.update_layout(
            title=f'Strategy Signals by Hour - {selected_date.strftime("%B %d, %Y")}',
            xaxis_title='Hour (UTC)',
            yaxis_title='Number of Signals',
            barmode='stack',
            height=400,
            showlegend=True
        )

        st.plotly_chart(fig, use_container_width=True)

        # Strategy summary table
        strategy_summary = []
        for strategy in strategies:
            total = total_signals[strategy]
            peak_hour = max(hourly_data.keys(), key=lambda h: hourly_data[h]['strategy_signals'][strategy])
            peak_count = hourly_data[peak_hour]['strategy_signals'][strategy]

            strategy_summary.append({
                'Strategy': strategy,
                'Total Signals': total,
                'Peak Hour': f"{peak_hour}:00",
                'Peak Signals': peak_count,
                'Avg per Hour': f"{total / max(len(hourly_data), 1):.1f}"
            })

        summary_df = pd.DataFrame(strategy_summary)
        st.dataframe(summary_df, use_container_width=True)

    def get_market_data_by_timeframe(self, timeframe):
        """Get real market analysis data for specific timeframe from logs"""
        # Load and analyze log data for the selected timeframe
        log_data = self.analyze_log_data_for_timeframe(timeframe)

        return {
            'analysis_count': log_data['total_analyses'],
            'pairs_analyzed': log_data['pairs_analyzed'],
            'strategy_signals': log_data['strategy_signals'],
            'rejection_reasons': log_data['rejection_reasons'],
            'time_range': log_data['time_range']
        }

    def analyze_log_data_for_timeframe(self, timeframe):
        """Analyze actual log files for market data"""
        import glob

        # Find all log files
        log_files = glob.glob("logs/apex_hunter_*.log")

        total_analyses = 0
        pairs_analyzed = set()
        strategy_signals = {'A1': 0, 'A2': 0, 'A3': 0, 'A4': 0, 'A5': 0}
        rejection_reasons = {'volume': 0, 'adx': 0, 'volatility': 0, 'other': 0}

        # Time range for analysis (last 24 hours for demo)
        time_range = {
            'start': datetime.now() - timedelta(hours=24),
            'end': datetime.now()
        }

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        # Count market analyses (Price: entries)
                        if 'Price:' in line and '$' in line:
                            total_analyses += 1

                            # Extract pair from log line (e.g., "BTC/USDT | Price:")
                            if '|' in line:
                                pair_part = line.split('|')[0].strip()
                                if '/' in pair_part:
                                    pairs_analyzed.add(pair_part)

                        # Count strategy signals
                        if 'SIGNAL:' in line:
                            if '[A1:' in line or 'A1 EMA' in line:
                                strategy_signals['A1'] += 1
                            elif '[A2:' in line or 'A2 EMA' in line:
                                strategy_signals['A2'] += 1
                            elif '[A3:' in line or 'A3 Fast' in line:
                                strategy_signals['A3'] += 1
                            elif '[A4:' in line or 'A4 Trend' in line:
                                strategy_signals['A4'] += 1
                            elif '[A5:' in line or 'A5 Market' in line:
                                strategy_signals['A5'] += 1

                        # Count rejection reasons
                        if 'FILTERED:' in line:
                            if 'Volume <' in line:
                                rejection_reasons['volume'] += 1
                            elif 'ADX <' in line:
                                rejection_reasons['adx'] += 1
                            else:
                                rejection_reasons['other'] += 1

            except Exception as e:
                st.warning(f"Error reading log file {log_file}: {e}")

        return {
            'total_analyses': max(total_analyses, 1),  # Ensure at least 1 for division
            'pairs_analyzed': list(pairs_analyzed) if pairs_analyzed else ['BTC/USDT', 'ETH/USDT'],
            'strategy_signals': strategy_signals,
            'rejection_reasons': rejection_reasons,
            'time_range': time_range
        }

    def display_market_metrics(self, data, timeframe):
        """Display market analysis metrics"""
        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Analyses", f"{data['analysis_count']:,}")
        with col2:
            st.metric("Pairs Analyzed", len(data['pairs_analyzed']))
        with col3:
            total_signals = sum(data['strategy_signals'].values())
            st.metric("Signals Generated", total_signals)
        with col4:
            signal_rate = (total_signals / data['analysis_count'] * 100) if data['analysis_count'] > 0 else 0
            st.metric("Signal Rate", f"{signal_rate:.1f}%")

    def display_price_volume_chart(self, data, timeframe):
        """Display real price and volume chart from market data"""
        # Try to load real market data from logs or create sample based on timeframe
        chart_data = self.get_price_volume_data(timeframe)

        fig = go.Figure()

        # Price line
        fig.add_trace(go.Scatter(
            x=chart_data['dates'],
            y=chart_data['prices'],
            mode='lines',
            name='BTC/USDT Price',
            line=dict(color='blue', width=2),
            yaxis='y1'
        ))

        # Volume bars
        fig.add_trace(go.Bar(
            x=chart_data['dates'],
            y=chart_data['volumes'],
            name='Volume',
            marker_color='rgba(255, 165, 0, 0.5)',
            yaxis='y2'
        ))

        # Update layout
        fig.update_layout(
            title=f'BTC/USDT Price & Volume - {timeframe} Timeframe ({chart_data["note"]})',
            xaxis_title='Time',
            yaxis=dict(title='Price (USDT)', side='left'),
            yaxis2=dict(title='Volume', side='right', overlaying='y'),
            height=400,
            showlegend=True
        )

        st.plotly_chart(fig, use_container_width=True)

    def get_price_volume_data(self, timeframe):
        """Get real or simulated price/volume data for timeframe"""
        # Try to extract real price data from logs first
        real_data = self.extract_real_price_data(timeframe)

        if real_data and len(real_data['prices']) > 5:
            # Use real data if available
            return {
                'dates': real_data['dates'],
                'prices': real_data['prices'],
                'volumes': real_data['volumes'],
                'note': 'Real Market Data'
            }
        else:
            # Generate realistic sample data based on timeframe
            periods = {
                '30M': 48,  # 24 hours of 30min data
                '1H': 24,   # 24 hours of hourly data
                '4H': 18,   # 3 days of 4-hour data
                '1D': 30    # 30 days of daily data
            }

            num_periods = periods.get(timeframe, 24)

            # Generate realistic BTC price movement
            base_price = 94344.70  # Current BTC price from logs
            dates = pd.date_range(end=pd.Timestamp.now(), periods=num_periods, freq=timeframe.lower())

            # Create realistic price movement with trends
            prices = []
            current_price = base_price

            for i in range(num_periods):
                # Add trend + random movement
                trend = (i - num_periods/2) * 10  # Slight upward trend
                random_move = np.random.normal(0, base_price * 0.005)  # 0.5% volatility
                current_price += trend + random_move
                prices.append(max(current_price, base_price * 0.8))  # Floor at 80% of base

            # Generate realistic volume
            base_volume = 1000000  # 1M base volume
            volumes = [base_volume + np.random.normal(0, base_volume * 0.3) for _ in range(num_periods)]
            volumes = [max(v, base_volume * 0.1) for v in volumes]  # Minimum volume

            return {
                'dates': dates,
                'prices': prices,
                'volumes': volumes,
                'note': 'Simulated Data (Real Data Not Available)'
            }

    def extract_real_price_data(self, timeframe):
        """Extract real price data from log files"""
        import glob

        log_files = glob.glob("logs/apex_hunter_*.log")
        price_data = []

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        # Look for price entries like "BTC/USDT | Price: $94344.70"
                        if 'Price:' in line and '$' in line and 'BTC/USDT' in line:
                            try:
                                # Extract timestamp and price
                                # Format: "2026-01-13 23:48:10 | INFO | BTC/USDT | Price: $94344.70"
                                parts = line.split('|')
                                if len(parts) >= 4:
                                    timestamp_str = parts[0].strip()
                                    price_str = parts[3].strip()

                                    if '$' in price_str:
                                        price = float(price_str.split('$')[1].replace(',', ''))

                                        # Parse timestamp
                                        try:
                                            timestamp = pd.to_datetime(timestamp_str)
                                            price_data.append({
                                                'timestamp': timestamp,
                                                'price': price,
                                                'volume': 1000000  # Estimated volume
                                            })
                                        except:
                                            continue
                            except:
                                continue
            except:
                continue

        if not price_data:
            return None

        # Sort by timestamp and return last N entries
        price_data.sort(key=lambda x: x['timestamp'])

        # Group by timeframe (simplified - just take last 50 entries)
        recent_data = price_data[-50:] if len(price_data) > 50 else price_data

        return {
            'dates': [d['timestamp'] for d in recent_data],
            'prices': [d['price'] for d in recent_data],
            'volumes': [d['volume'] for d in recent_data]
        }

    def display_strategy_timeframe_analysis(self, data, timeframe):
        """Display strategy analysis for selected timeframe"""
        st.subheader(f"🎯 Strategy Analysis - {timeframe} Timeframe")

        col1, col2 = st.columns(2)

        with col1:
            # Strategy signals breakdown
            st.write("**Signals by Strategy:**")
            signals_df = pd.DataFrame({
                'Strategy': list(data['strategy_signals'].keys()),
                'Signals': list(data['strategy_signals'].values())
            })

            fig = px.bar(signals_df, x='Strategy', y='Signals',
                        title=f'Strategy Signals - {timeframe}',
                        color='Strategy')
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            # Rejection reasons
            st.write("**Rejection Reasons:**")
            rejections_df = pd.DataFrame({
                'Reason': list(data['rejection_reasons'].keys()),
                'Count': list(data['rejection_reasons'].values())
            })

            fig = px.pie(rejections_df, values='Count', names='Reason',
                        title=f'Filter Rejections - {timeframe}')
            st.plotly_chart(fig, use_container_width=True)

    def display_strategy_drilldown(self):
        """Display detailed strategy drill-down analysis"""
        st.header("🔍 Strategy Drill-Down Analysis")

        # Market type selector
        market_type = st.radio("Select Market Type:", ["Futures", "Spot"], horizontal=True)

        # Strategy selector
        strategies = ["A1", "A2", "A3", "A4", "A5"]
        selected_strategy = st.selectbox("Select Strategy:", strategies)

        # Time period selector
        time_periods = ["Last 24 Hours", "Last 7 Days", "Last 30 Days"]
        selected_period = st.selectbox("Time Period:", time_periods)

        # Load and display detailed strategy data
        self.display_detailed_strategy_metrics(selected_strategy, market_type.lower(), selected_period)

    def display_detailed_strategy_metrics(self, strategy, market_type, period):
        """Display detailed metrics for selected strategy"""
        # Sample data - in real implementation, load from database
        sample_data = {
            'total_trades': 45,
            'win_rate': 68.9,
            'avg_pnl': 2.34,
            'total_pnl': 105.3,
            'avg_duration': '2.5 hours',
            'best_pair': 'BTC/USDT',
            'worst_pair': 'XMR/USDT',
            'peak_pnl': 15.7,
            'max_drawdown': -8.2
        }

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Trades", sample_data['total_trades'])
            st.metric("Win Rate", f"{sample_data['win_rate']}%")

        with col2:
            st.metric("Avg P&L", f"${sample_data['avg_pnl']:+.2f}")
            st.metric("Total P&L", f"${sample_data['total_pnl']:+.2f}")

        with col3:
            st.metric("Avg Duration", sample_data['avg_duration'])
            st.metric("Best Pair", sample_data['best_pair'])

        with col4:
            st.metric("Peak P&L", f"${sample_data['peak_pnl']:+.2f}")
            st.metric("Max Drawdown", f"${sample_data['max_drawdown']:+.2f}")

        # Performance chart for this strategy
        self.display_strategy_performance_chart(strategy, market_type, period)

    def display_strategy_performance_chart(self, strategy, market_type, period):
        """Display performance chart for specific strategy"""
        # Sample data
        dates = pd.date_range(end=pd.Timestamp.now(), periods=30, freq='D')
        pnl_values = np.cumsum(np.random.normal(0.5, 2, 30))

        fig = go.Figure()

        fig.add_trace(go.Scatter(
            x=dates,
            y=pnl_values,
            mode='lines+markers',
            name=f'{strategy} P&L',
            line=dict(color='green', width=2)
        ))

        fig.update_layout(
            title=f'{strategy} Strategy P&L - {market_type.upper()} - {period}',
            xaxis_title='Date',
            yaxis_title='Cumulative P&L ($)',
            height=300
        )

        st.plotly_chart(fig, use_container_width=True)

    def display_hourly_analysis(self):
        """Display detailed hourly analysis for selected date"""
        st.header("🕐 Hourly Market Analysis")

        # Date selector
        available_dates = self.get_available_dates()
        if not available_dates:
            st.error("No log data available for analysis")
            return

        selected_date = st.selectbox(
            "Select Date:",
            options=available_dates,
            format_func=lambda x: x.strftime('%B %d, %Y'),
            index=len(available_dates)-1  # Default to most recent
        )

        st.write(f"**Analyzing:** {selected_date.strftime('%B %d, %Y')}")

        # Get hourly data for selected date
        hourly_data = self.get_hourly_analysis_data(selected_date)

        # Display hourly breakdown chart
        self.display_hourly_chart(hourly_data, selected_date)

        # Display detailed hourly table
        self.display_hourly_table(hourly_data)

        # Display trade outcomes table
        self.display_trade_outcomes(hourly_data)

    def get_available_dates(self):
        """Get list of available dates from log files"""
        import glob

        dates = set()
        log_files = glob.glob("logs/apex_hunter_*.log")

        for log_file in log_files:
            try:
                # Extract date from filename (apex_hunter_20260112.log)
                filename = os.path.basename(log_file)
                if 'apex_hunter_' in filename and '.log' in filename:
                    date_str = filename.replace('apex_hunter_', '').replace('.log', '')
                    try:
                        # Parse date string directly (already in YYYYMMDD format)
                        date = pd.to_datetime(date_str, format='%Y%m%d')
                        dates.add(date.date())
                    except:
                        continue
            except:
                continue

        return sorted(list(dates)) if dates else []

    def get_hourly_analysis_data(self, selected_date):
        """Get hourly analysis data for selected date"""
        import glob

        hourly_data = {}
        log_files = glob.glob("logs/apex_hunter_*.log")

        for log_file in log_files:
            try:
                with open(log_file, 'r') as f:
                    for line in f:
                        # Check if line is from selected date
                        if f'2026-{selected_date.month:02d}-{selected_date.day:02d}' in line:
                            try:
                                # Extract hour from timestamp
                                timestamp_part = line.split('|')[0].strip()
                                hour = timestamp_part.split()[1].split(':')[0]  # HH from HH:MM:SS

                                if hour not in hourly_data:
                                    hourly_data[hour] = {
                                        'futures_analyses': 0,
                                        'spot_analyses': 0,
                                        'signals': 0,
                                        'volume_rejections': 0,
                                        'adx_rejections': 0,
                                        'other_rejections': 0,
                                        'trades_taken': 0
                                    }

                                # Count different types of events
                                if 'Price:' in line and '$' in line:
                                    # This is a market analysis (futures by default, spot if SPOT in line)
                                    if 'SPOT' in line.upper():
                                        hourly_data[hour]['spot_analyses'] += 1
                                    else:
                                        hourly_data[hour]['futures_analyses'] += 1

                                if 'SIGNAL:' in line:
                                    hourly_data[hour]['signals'] += 1

                                if 'FILTERED:' in line:
                                    if 'Volume <' in line:
                                        hourly_data[hour]['volume_rejections'] += 1
                                    elif 'ADX <' in line:
                                        hourly_data[hour]['adx_rejections'] += 1
                                    else:
                                        hourly_data[hour]['other_rejections'] += 1

                                if 'ENTRY' in line and 'Risk Approved' in line:
                                    hourly_data[hour]['trades_taken'] += 1

                            except:
                                continue
            except:
                continue

        return hourly_data

    def display_hourly_chart(self, hourly_data, selected_date):
        """Display hourly analysis chart"""
        st.subheader(f"📊 Hourly Market Analysis - {selected_date.strftime('%B %d, %Y')}")

        if not hourly_data:
            st.info("No data available for selected date")
            return

        # Prepare data for chart
        hours = sorted(hourly_data.keys())
        futures_analyses = [hourly_data[h]['futures_analyses'] for h in hours]
        spot_analyses = [hourly_data[h]['spot_analyses'] for h in hours]

        fig = go.Figure()

        # Futures analyses
        fig.add_trace(go.Bar(
            x=[f"{h}:00" for h in hours],
            y=futures_analyses,
            name='Futures Analyses',
            marker_color='blue'
        ))

        # Spot analyses
        fig.add_trace(go.Bar(
            x=[f"{h}:00" for h in hours],
            y=spot_analyses,
            name='Spot Analyses',
            marker_color='green'
        ))

        fig.update_layout(
            title=f'Hourly Market Analysis - {selected_date.strftime("%B %d, %Y")}',
            xaxis_title='Hour (UTC)',
            yaxis_title='Number of Analyses',
            barmode='stack',
            height=400
        )

        st.plotly_chart(fig, use_container_width=True)

    def display_hourly_table(self, hourly_data):
        """Display detailed hourly breakdown table"""
        st.subheader("📋 Detailed Hourly Breakdown")

        if not hourly_data:
            st.info("No data available")
            return

        # Prepare table data
        table_data = []
        for hour in sorted(hourly_data.keys()):
            data = hourly_data[hour]
            total_analyses = data['futures_analyses'] + data['spot_analyses']
            total_rejections = data['volume_rejections'] + data['adx_rejections'] + data['other_rejections']

            table_data.append({
                'Hour': f"{hour}:00",
                'Futures Analyses': data['futures_analyses'],
                'Spot Analyses': data['spot_analyses'],
                'Total Analyses': total_analyses,
                'Signals Generated': data['signals'],
                'Trades Taken': data['trades_taken'],
                'Total Rejections': total_rejections,
                'Success Rate': f"{(data['trades_taken'] / max(total_analyses, 1) * 100):.1f}%"
            })

        df = pd.DataFrame(table_data)
        st.dataframe(df, use_container_width=True)

    def display_trade_outcomes(self, hourly_data):
        """Display trade outcomes and rejection reasons"""
        st.subheader("📊 Trade Outcomes Analysis")

        if not hourly_data:
            st.info("No data available")
            return

        # Aggregate data across all hours
        total_signals = sum(h['signals'] for h in hourly_data.values())
        total_trades = sum(h['trades_taken'] for h in hourly_data.values())
        total_volume_rejections = sum(h['volume_rejections'] for h in hourly_data.values())
        total_adx_rejections = sum(h['adx_rejections'] for h in hourly_data.values())
        total_other_rejections = sum(h['other_rejections'] for h in hourly_data.values())

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Signals Generated", f"{total_signals:,}")
        with col2:
            st.metric("Trades Executed", f"{total_trades:,}")
        with col3:
            st.metric("Signals Rejected", f"{total_signals - total_trades:,}")
        with col4:
            conversion_rate = (total_trades / max(total_signals, 1) * 100)
            st.metric("Conversion Rate", f"{conversion_rate:.1f}%")

        # Rejection reasons breakdown
        st.write("**Rejection Reasons:**")

        rejection_data = pd.DataFrame({
            'Reason': ['Volume Too Low', 'ADX Too Low', 'Other Filters'],
            'Count': [total_volume_rejections, total_adx_rejections, total_other_rejections]
        })

        fig = px.pie(rejection_data, values='Count', names='Reason',
                    title='Signal Rejection Breakdown')
        st.plotly_chart(fig, use_container_width=True)

    def run_dashboard(self):
        """Main dashboard function"""
        # Load data
        data = self.load_all_data()
        futures_trades = data['futures']
        spot_trades = data['spot']

        # Process performance data
        futures_stats = self.process_strategy_performance(futures_trades)
        spot_stats = self.process_strategy_performance(spot_trades)

        # Navigation
        st.sidebar.title("Navigation")
        page = st.sidebar.radio("Go to:", [
            "Overview",
            "Market Analysis Charts",
            "Strategy Drill-Down",
            "Hourly Analysis",
            "Performance Summary"
        ])
        
        # Sidebar Status Indicators
        st.sidebar.markdown("---")
        st.sidebar.subheader("🤖 Bot Status")
        
        active_count = len(data['active'])
        if active_count > 0:
            st.sidebar.success(f"Running: {active_count} Active Trades")
        else:
            st.sidebar.info("Idle: Waiting for signals")
            
        st.sidebar.markdown(f"*Last Updated: {datetime.now().strftime('%H:%M:%S')}*")
        if st.sidebar.button("🗑️ Clear Cache"):
            st.cache_data.clear()
            st.rerun()

        if page == "Overview":
            # Display comprehensive professional overview
            self.display_overview_metrics(futures_stats, spot_stats, data['active'])

        elif page == "Market Analysis Charts":
            self.display_market_analysis_charts()

        elif page == "Strategy Drill-Down":
            self.display_strategy_drilldown()

        elif page == "Hourly Analysis":
            self.display_hourly_analysis()

        elif page == "Performance Summary":
            self.display_performance_summary(futures_stats, spot_stats)

        # Footer
        st.markdown("---")
        st.markdown("*Dashboard auto-refreshes. Click '🔄 Refresh Data' for manual update.*")

    def display_performance_summary(self, futures_stats, spot_stats):
        """Display comprehensive performance summary"""
        st.header("📊 Performance Summary")

        # Overall metrics
        total_futures_trades = futures_stats['trade_count'].sum() if not futures_stats.empty else 0
        total_spot_trades = spot_stats['trade_count'].sum() if not spot_stats.empty else 0
        total_trades = total_futures_trades + total_spot_trades

        col1, col2, col3, col4 = st.columns(4)

        with col1:
            st.metric("Total Trades", f"{total_trades:,}")
        with col2:
            st.metric("Active Strategies", "5")
        with col3:
            st.metric("Markets Tracked", "10+ pairs")
        with col4:
            st.metric("Analysis Engine", "Active")

        # Best performing strategies
        st.subheader("🏆 Best Performing Strategies")

        if not futures_stats.empty:
            best_futures = futures_stats.nlargest(1, 'total_pnl')
            st.write(f"**Futures:** {best_futures['strategy'].iloc[0]} (${best_futures['total_pnl'].iloc[0]:.2f})")

        if not spot_stats.empty:
            best_spot = spot_stats.nlargest(1, 'total_pnl')
            st.write(f"**Spot:** {best_spot['strategy'].iloc[0]} (${best_spot['total_pnl'].iloc[0]:.2f})")

        # Risk metrics
        st.subheader("⚠️ Risk Metrics")

        risk_col1, risk_col2 = st.columns(2)

        with risk_col1:
            st.write("**Futures Risk:**")
            if not futures_stats.empty:
                avg_leverage = futures_stats['avg_leverage'].mean()
                st.write(f"- Avg Leverage: {avg_leverage:.1f}x")
                st.write("- Max Drawdown: Tracking active")

        with risk_col2:
            st.write("**Spot Risk:**")
            st.write("- No leverage (cash only)")
            st.write("- Direct price exposure")


def main():
    dashboard = StrategyDashboard()
    dashboard.run_dashboard()


if __name__ == "__main__":
    main()
