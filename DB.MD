# APEX HUNTER V14 - JSON Database Documentation

## 📊 Overview

APEX HUNTER V14 uses JSON file-based storage for comprehensive logging and analytics of all trading activities. This approach provides immediate functionality with a clean migration path to MongoDB Atlas when ready. This document provides complete setup instructions and schema definitions for all JSON data files.

## 🚀 Quick Setup

### 1. No Setup Required (JSON Files)
**JSON storage works immediately** - no database installation needed!
Your data will be stored in `data/` directory as human-readable JSON files.

### Alternative: Install Local MongoDB (Optional)

#### **macOS (with Homebrew):**
```bash
brew install mongodb-community
brew services start mongodb-community
```

#### **Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install mongodb
sudo systemctl start mongodb
sudo systemctl enable mongodb
```

#### **Windows:**
1. Download from [mongodb.com](https://www.mongodb.com/try/download/community)
2. Run installer
3. Start MongoDB service

#### **Verify Installation:**
```bash
mongod --version
# Should show version info

mongo --eval "db.runCommand('ping')"
# Should show: { "ok" : 1 }
```

### 2. Environment Configuration
Add to your `.env` file:
```env
# MongoDB Configuration
MONGODB_ENABLED=true
MONGODB_HOST=localhost
MONGODB_PORT=27017
MONGODB_DATABASE=apex_hunter_v14
# MONGODB_USERNAME=  # Uncomment if using authentication
# MONGODB_PASSWORD=  # Uncomment if using authentication
MONGODB_RETENTION_DAYS=30
```

**Note:** Local MongoDB runs without authentication by default. The database `apex_hunter_v14` will be created automatically.

### 3. Install Dependencies
```bash
pip install pymongo motor
```

### 4. Verify Connection
Run the bot once to verify MongoDB connection:
```bash
python3 main.py --interval 5
```
You should see: `✅ MongoDB connected to database: apex_hunter_v14`

## 📋 Collections Schema

### 🎯 `futures_trades` - Complete Futures Trading History

**Purpose:** Store all futures trades with complete lifecycle data including trailing stops.

**Schema:**
```javascript
{
  _id: ObjectId,                    // Auto-generated
  timestamp: Date,                  // When trade was logged
  strategy: String,                 // "A1: EMA Only", "A2: EMA+RSI", etc.
  symbol: String,                   // "BTC/USDT"
  side: String,                     // "buy" | "sell"
  entry_price: Number,              // Entry price
  exit_price: Number,               // Exit price (if closed)
  entry_time: Date,                 // When position was opened
  exit_time: Date,                  // When position was closed
  leverage: Number,                 // Leverage used (1-10)
  position_size: Number,            // Position size in USDT
  stop_loss: Number,                // Current stop loss price
  take_profit: Number,              // Take profit price
  pnl_amount: Number,               // Profit/Loss in USDT
  pnl_percent: Number,              // Profit/Loss percentage
  reason: String,                   // "stop_loss", "take_profit", "manual"
  capital_after: Number,            // Capital after trade

  // Trailing Stop Data
  trailing_stop_active: Boolean,    // Whether trailing is active
  highest_price: Number,            // Highest price for longs
  lowest_price: Number,             // Lowest price for shorts
  trailing_activation_price: Number,// Price when trailing activated
  original_stop_loss: Number,       // Original stop loss

  // Metadata
  confidence: Number,               // Strategy confidence (0-1)
  metadata: Object,                 // Additional context

  // TTL Index (auto-delete after retention period)
  expires_at: Date
}
```

**Indexes:**
```javascript
// Primary query index
{ timestamp: -1, strategy: 1, symbol: 1 }

// Performance analysis index
{ strategy: 1, pnl_amount: -1 }

// TTL index for auto-cleanup
{ expires_at: 1 }
```

### 📍 `spot_signals` - Spot Trading Signals

**Purpose:** Log all spot trading signals and their execution status.

**Schema:**
```javascript
{
  _id: ObjectId,
  timestamp: Date,
  strategy: String,
  symbol: String,
  side: String,           // "buy" | "sell"
  entry_price: Number,
  stop_loss: Number,
  take_profit: Number,
  confidence: Number,
  executed: Boolean,      // Whether trade was executed
  pnl_amount: Number,     // Profit if executed
  pnl_percent: Number,
  metadata: Object,
  expires_at: Date
}
```

**Indexes:**
```javascript
{ timestamp: -1, strategy: 1, symbol: 1 }
{ executed: 1, pnl_amount: -1 }
{ expires_at: 1 }
```

### 🔍 `arbitrage_opportunities` - Arbitrage Opportunities

**Purpose:** Track all arbitrage opportunities found and their execution.

**Schema:**
```javascript
{
  _id: ObjectId,
  timestamp: Date,
  type: String,           // "simple", "triangular", "cross"
  symbols: [String],      // ["BTC/USDT", "ETH/USDT"] for triangular
  exchanges: [String],    // ["binance", "kucoin"]
  buy_price: Number,      // Buy price on first exchange
  sell_price: Number,     // Sell price on second exchange
  spread_percent: Number, // Price difference percentage
  profit_amount: Number,  // Potential profit in USDT
  profit_percent: Number, // Profit percentage
  executed: Boolean,      // Whether opportunity was executed
  fees: Number,           // Total fees
  net_profit: Number,     // Profit after fees
  metadata: Object,       // Additional arbitrage data
  expires_at: Date
}
```

**Indexes:**
```javascript
{ timestamp: -1, type: 1, profit_percent: -1 }
{ executed: 1, profit_amount: -1 }
{ expires_at: 1 }
```

### 📈 `trailing_stops` - Trailing Stop Events

**Purpose:** Log all trailing stop activations and adjustments.

**Schema:**
```javascript
{
  _id: ObjectId,
  timestamp: Date,
  action: String,         // "activated" | "updated"
  symbol: String,
  strategy: String,
  current_price: Number,  // Price when event occurred
  profit_percent: Number, // Current profit percentage
  old_stop_loss: Number,  // Previous stop loss
  new_stop_loss: Number,  // New stop loss
  highest_price: Number,  // For longs: highest price reached
  lowest_price: Number,   // For shorts: lowest price reached
  position_side: String,  // "buy" | "sell"
  metadata: Object,
  expires_at: Date
}
```

**Indexes:**
```javascript
{ timestamp: -1, strategy: 1, symbol: 1 }
{ action: 1, profit_percent: -1 }
{ expires_at: 1 }
```

### 🛡️ `risk_rejections` - Risk Management Rejections

**Purpose:** Log all trades rejected by the 11-layer risk management system.

**Schema:**
```javascript
{
  _id: ObjectId,
  timestamp: Date,
  symbol: String,
  strategy: String,
  reason: String,                    // Specific rejection reason
  layer_name: String,                // "PositionSizingLayer", "LeverageControlLayer", etc.
  layer_number: Number,              // 1-11
  trade_params: Object,              // Original trade parameters
  account_state: Object,             // Account state at rejection time
  metadata: Object,                  // Additional context
  expires_at: Date
}
```

**Indexes:**
```javascript
{ timestamp: -1, layer_name: 1, symbol: 1 }
{ strategy: 1, reason: 1 }
{ expires_at: 1 }
```

### 📝 `system_logs` - System Events & Errors

**Purpose:** General system logging (API calls, errors, performance metrics).

**Schema:**
```javascript
{
  _id: ObjectId,
  timestamp: Date,
  category: String,      // "api_calls", "performance", "system_events", "error_traces"
  level: String,         // "INFO", "WARNING", "ERROR", "DEBUG"
  message: String,
  metadata: Object,      // Additional structured data
  expires_at: Date
}
```

**Indexes:**
```javascript
{ timestamp: -1, level: 1 }
{ category: 1, timestamp: -1 }
{ expires_at: 1 }
```

## 🔍 Query Examples

### Strategy Performance Analysis
```javascript
// Get win rate and P&L by strategy (last 30 days)
db.futures_trades.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 30*24*60*60*1000) }
    }
  },
  {
    $group: {
      _id: "$strategy",
      total_trades: { $sum: 1 },
      winning_trades: { $sum: { $cond: [{ $gt: ["$pnl_amount", 0] }, 1, 0] } },
      total_pnl: { $sum: "$pnl_amount" },
      avg_pnl: { $avg: "$pnl_amount" }
    }
  },
  {
    $project: {
      strategy: "$_id",
      total_trades: 1,
      win_rate: { $multiply: [{ $divide: ["$winning_trades", "$total_trades"] }, 100] },
      total_pnl: { $round: ["$total_pnl", 2] },
      avg_pnl: { $round: ["$avg_pnl", 2] }
    }
  },
  { $sort: { total_pnl: -1 } }
])
```

### Daily P&L Summary
```javascript
// Get daily P&L for dashboard
db.futures_trades.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 30*24*60*60*1000) }
    }
  },
  {
    $group: {
      _id: {
        $dateToString: { format: "%Y-%m-%d", date: "$timestamp" }
      },
      total_pnl: { $sum: "$pnl_amount" },
      trade_count: { $sum: 1 }
    }
  },
  {
    $project: {
      date: "$_id",
      total_pnl: { $round: ["$total_pnl", 2] },
      trade_count: 1
    }
  },
  { $sort: { date: -1 } }
])
```

### Risk Rejection Analysis
```javascript
// Get most common rejection reasons
db.risk_rejections.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 7*24*60*60*1000) }
    }
  },
  {
    $group: {
      _id: "$layer_name",
      rejection_count: { $sum: 1 },
      latest_rejection: { $max: "$timestamp" }
    }
  },
  {
    $project: {
      layer_name: "$_id",
      rejection_count: 1,
      latest_rejection: 1
    }
  },
  { $sort: { rejection_count: -1 } }
])
```

### Trailing Stop Effectiveness
```javascript
// Analyze trailing stop performance
db.trailing_stops.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 30*24*60*60*1000) },
      action: "activated"
    }
  },
  {
    $lookup: {
      from: "futures_trades",
      let: { symbol: "$symbol", strategy: "$strategy", entry_time: "$timestamp" },
      pipeline: [
        {
          $match: {
            $expr: {
              $and: [
                { $eq: ["$symbol", "$$symbol"] },
                { $eq: ["$strategy", "$$strategy"] },
                { $gte: ["$entry_time", "$$entry_time"] }
              ]
            }
          }
        }
      ],
      as: "related_trades"
    }
  },
  {
    $unwind: "$related_trades"
  },
  {
    $group: {
      _id: null,
      total_activations: { $sum: 1 },
      profitable_exits: {
        $sum: { $cond: [{ $gt: ["$related_trades.pnl_amount", 0] }, 1, 0] }
      }
    }
  }
])
```

## 📊 Dashboard Integration

### Strategy Comparison Dashboard
```javascript
// Data for strategy comparison charts
db.futures_trades.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 90*24*60*60*1000) }
    }
  },
  {
    $group: {
      _id: { strategy: "$strategy", symbol: "$symbol" },
      total_trades: { $sum: 1 },
      total_pnl: { $sum: "$pnl_amount" },
      win_rate: {
        $avg: { $cond: [{ $gt: ["$pnl_amount", 0] }, 1, 0] }
      }
    }
  }
])
```

### Risk Portfolio Analysis
```javascript
// Risk layer impact analysis
db.risk_rejections.aggregate([
  {
    $match: {
      timestamp: { $gte: new Date(Date.now() - 30*24*60*60*1000) }
    }
  },
  {
    $group: {
      _id: {
        layer: "$layer_name",
        strategy: "$strategy"
      },
      rejection_count: { $sum: 1 }
    }
  },
  {
    $sort: { rejection_count: -1 }
  }
])
```

## 🧹 Data Retention & Cleanup

### Automatic Cleanup
- **TTL Indexes:** Documents automatically expire after `MONGODB_RETENTION_DAYS`
- **Default:** 30 days retention
- **Configurable:** Set `MONGODB_RETENTION_DAYS=7` for 7 days or `MONGODB_RETENTION_DAYS=90` for 90 days

### Manual Cleanup
```javascript
// Remove old data manually
db.futures_trades.deleteMany({
  timestamp: { $lt: new Date(Date.now() - 60*24*60*60*1000) } // Older than 60 days
});
```

### Environment Variable Cleanup

#### **On Bot Startup:**
```bash
# Clean logs and database on startup
CLEAN_LOGS=yes CLEAN_DB=yes python3 main.py

# Clean only logs
CLEAN_LOGS=yes python3 main.py

# Clean only database
CLEAN_DB=yes python3 main.py
```

#### **Standalone Cleanup Script:**
```bash
# Clean everything with confirmation
python3 cleanup.py --logs --db

# Clean only logs
python3 cleanup.py --logs

# Preview what would be deleted
python3 cleanup.py --logs --dry-run

# Clean without confirmation prompts
python3 cleanup.py --logs --db --force
```

#### **Environment Variables:**
```env
# Cleanup Control
CLEAN_LOGS=no          # Delete all *.log files in logs/ folder
CLEAN_DB=no            # Clean all JSON files in data/ folder
```

**What Gets Cleaned:**
- **CLEAN_LOGS=yes:** Deletes all `logs/*.log` files
- **CLEAN_DB=yes:** Deletes all `data/*.json` files (futures_trades.json, spot_signals.json, etc.)

## 🔧 Performance Optimization

### Index Strategy
- **Time-based queries:** `{ timestamp: -1 }` for recent data
- **Strategy analysis:** `{ strategy: 1, symbol: 1 }` for filtering
- **Performance metrics:** `{ pnl_amount: -1 }` for sorting
- **TTL indexes:** `{ expires_at: 1 }` for automatic cleanup

### Connection Pooling
- **Max Pool Size:** 10 connections
- **Min Pool Size:** 2 connections
- **Timeout:** 5 seconds connection timeout
- **Async support:** Motor for non-blocking operations

## 📈 Monitoring & Analytics

### Collection Sizes
```javascript
// Check collection sizes
db.stats().collections.futures_trades
db.stats().collections.spot_signals
db.stats().collections.arbitrage_opportunities
db.stats().collections.trailing_stops
db.stats().collections.risk_rejections
```

### Performance Metrics
```javascript
// Database performance stats
db.serverStatus().opcounters
db.serverStatus().connections
```

## 🚨 Backup Strategy

### Local MongoDB Backup
- **Automatic TTL Cleanup:** Documents automatically expire based on retention settings
- **Manual Exports:** Use `mongodump` for backups
- **Database Files:** MongoDB data directory contains all data

### Local Backup Script
```bash
#!/bin/bash
# Create backups directory
mkdir -p /backups

# Backup script
DATE=$(date +%Y%m%d_%H%M%S)
mongodump --db apex_hunter_v14 --out /backups/backup_$DATE

# Optional: Compress backup
tar -czf /backups/backup_$DATE.tar.gz -C /backups backup_$DATE
rm -rf /backups/backup_$DATE

echo "Backup completed: /backups/backup_$DATE.tar.gz"
```

### Restore from Backup
```bash
#!/bin/bash
# Restore script
BACKUP_DIR="/backups/backup_20231201_120000"
mongorestore --db apex_hunter_v14 $BACKUP_DIR/apex_hunter_v14
```

## ⚠️ Security Considerations

### Local MongoDB Security
1. **Environment Variables:** Keep database credentials secure, never commit to version control
2. **Network Access:** MongoDB listens on localhost by default (127.0.0.1:27017)
3. **Authentication:** Enable MongoDB authentication for production use:
   ```bash
   # Enable authentication
   mongosh
   use admin
   db.createUser({user: "admin", pwd: "password", roles: ["userAdminAnyDatabase"]})
   ```
4. **Firewall:** Ensure MongoDB port (27017) is not exposed to external networks
5. **Data Directory:** Secure MongoDB data directory permissions

## 🔄 Migration from File Logging

If you have existing file logs and want to migrate to MongoDB:

1. **Keep both active:** MongoDB logging works alongside file logging
2. **Gradual migration:** Start with new data only
3. **Historical import:** Write scripts to parse and import old log files
4. **Verification:** Compare query results between file and MongoDB logs

## 📚 Additional Resources

- [MongoDB Atlas Documentation](https://docs.atlas.mongodb.com/)
- [PyMongo Documentation](https://pymongo.readthedocs.io/)
- [MongoDB Aggregation Framework](https://docs.mongodb.com/manual/aggregation/)
- [MongoDB Indexing Best Practices](https://docs.mongodb.com/manual/core/indexes/)

## 🎯 Next Steps

1. **Install Local MongoDB** using the instructions above for your OS
2. **Verify MongoDB is running** with `mongod --version` and connection test
3. **Configure environment variables** in `.env` (already done)
4. **Test connection** by running the bot: `python3 main.py --interval 5`
5. **Build dashboards** using the query examples above
6. **Monitor performance** and adjust indexes as needed

---

**This MongoDB integration provides complete visibility into your APEX HUNTER V14 trading operations with powerful analytics capabilities!** 📊🚀
