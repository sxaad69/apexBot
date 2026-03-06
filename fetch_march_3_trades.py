import sqlite3
import json
from datetime import datetime
from pathlib import Path
import sys


def fetch_trades_for_date(target_date_str, db_path):
    """
    Fetch all trades executed on a specific date from the SQLite database
    
    Args:
        target_date_str (str): Date in format 'YYYY-MM-DD' (e.g., '2026-03-03')
        db_path (str): Path to the SQLite database file
    """
    
    # Parse the target date
    target_date = datetime.strptime(target_date_str, '%Y-%m-%d').date()
    db_path = Path(db_path)
    
    if not db_path.exists():
        print(f"❌ Database not found at: {db_path}")
        return []
    print(f"🔍 Connecting to database: {db_path}")
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # This allows us to access rows by column name
        cursor = conn.cursor()
        
        # Query trades table for entries on the specific date
        # Since entry_time is stored as text in ISO format, we need to extract the date part
        cursor.execute("""
            SELECT * FROM trades 
            WHERE DATE(entry_time) = ? 
            OR DATE(exit_time) = ?
            ORDER BY entry_time ASC
        """, (target_date_str, target_date_str))
        
        rows = cursor.fetchall()
        
        # Convert rows to dictionaries
        trades = []
        for row in rows:
            trade_dict = dict(row)
            # Attempt to parse metadata JSON if it exists
            if trade_dict.get('metadata'):
                try:
                    trade_dict['metadata'] = json.loads(trade_dict['metadata'])
                except:
                    pass  # Keep as-is if parsing fails
            trades.append(trade_dict)
        
        conn.close()
        
        print(f"✅ Found {len(trades)} trades on {target_date_str}")
        return trades
        
    except Exception as e:
        print(f"❌ Error querying database: {e}")
        return []


def display_trades(trades):
    """
    Display the trades in a formatted way
    """
    if not trades:
        print("No trades found for the specified date.")
        return
    
    print(f"\n📈 Trades Executed on {trades[0]['entry_time'][:10] if trades and trades[0].get('entry_time') else 'N/A'}:")
    print("-" * 120)
    
    for i, trade in enumerate(trades, 1):
        print(f"Trade #{i}:")
        print(f"  ID: {trade.get('trade_id', 'N/A')}")
        print(f"  Symbol: {trade.get('symbol', 'N/A')}")
        print(f"  Strategy: {trade.get('strategy', 'N/A')}")
        print(f"  Side: {trade.get('side', 'N/A')}")
        print(f"  Type: {trade.get('market_type', 'N/A')}")
        print(f"  Size: {trade.get('size', 'N/A')}")
        print(f"  Entry Price: {trade.get('entry_price', 'N/A')}")
        print(f"  Entry Time: {trade.get('entry_time', 'N/A')}")
        print(f"  Exit Price: {trade.get('exit_price', 'N/A')}")
        print(f"  Exit Time: {trade.get('exit_time', 'N/A')}")
        print(f"  PnL Amount: {trade.get('pnl_amount', 'N/A')}")
        print(f"  PnL Percent: {trade.get('pnl_percent', 'N/A')}%")
        print(f"  Status: {trade.get('status', 'N/A')}")
        print(f"  Leverage: {trade.get('leverage', 'N/A')}")
        
        if trade.get('metadata'):
            print(f"  Metadata: {json.dumps(trade['metadata'], indent=4)[:200]}...")
        
        print("-" * 120)


def main():
    """
    Main function to fetch and display trades for March 3rd
    """
    print("🚀 APEX HUNTER - TRADE FETCHER FOR MARCH 3RD 🚀\n")
    
    # Target date - March 3rd, current year
    target_date = "2026-03-03"
    
    # Get database path from command line arguments
    if len(sys.argv) < 2:
        print("Usage: python fetch_march_3_trades.py <path_to_your_apex_hunter_db_file>")
        print("Example: python fetch_march_3_trades.py /home/user/apex_hunter/apex_hunter.db\n")
        return
    
    db_path = sys.argv[1]
    
    print(f"📅 Fetching trades for date: {target_date}")
    print(f"💾 Database path: {db_path}")
    
    # Fetch trades for the specified date
    trades = fetch_trades_for_date(target_date, db_path)
    
    # Display the trades
    display_trades(trades)


if __name__ == "__main__":
    main()