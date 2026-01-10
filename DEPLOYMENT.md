# 🚀 APEX HUNTER V14 - Fly.io Deployment Guide

Deploy your trading bot to run 24/7 in the cloud with Fly.io.

## 📋 Prerequisites

- Fly.io account (free tier available)
- Your KuCoin API credentials
- Your Telegram bot tokens

## 🛠️ Step 1: Install Fly.io CLI

### macOS
```bash
brew install flyctl
```

### Linux
```bash
curl -L https://fly.io/install.sh | sh
```

### Windows
```powershell
iwr https://fly.io/install.ps1 -useb | iex
```

## 🔐 Step 2: Login to Fly.io

```bash
flyctl auth login
```

This will open your browser for authentication.

## 📦 Step 3: Create Fly.io App

Navigate to your project directory:
```bash
cd /path/to/apex-hunter-v14-complete
```

Create the app (you can change the name):
```bash
flyctl apps create apex-hunter-v14
```

## 🔑 Step 4: Set Environment Variables (Secrets)

Fly.io uses secrets for sensitive data. Set ALL your environment variables:

### Required Secrets:
```bash
# Exchange Configuration
flyctl secrets set EXCHANGE_ENVIRONMENT=production
flyctl secrets set FUTURES_EXCHANGE=kucoin
flyctl secrets set SPOT_EXCHANGE=kucoin

# KuCoin API Credentials
flyctl secrets set KUCOIN_API_KEY="your_api_key_here"
flyctl secrets set KUCOIN_API_SECRET="your_api_secret_here"
flyctl secrets set KUCOIN_API_PASSPHRASE="your_passphrase_here"

# Trading Controls (KEEP AS 'no' FOR SAFETY!)
flyctl secrets set FUTURES_TRADING_ENABLED=no
flyctl secrets set SPOT_TRADING_ENABLED=no
flyctl secrets set ARBITRAGE_TRADING_ENABLED=no

# Telegram Bots
flyctl secrets set TELEGRAM_FUTURES_ENABLED=true
flyctl secrets set TELEGRAM_FUTURES_BOT_TOKEN="8360195396:AAENWx5Nf8-lxoya-XwPlGjkSFNOQGTa5j8"
flyctl secrets set TELEGRAM_FUTURES_CHAT_ID="8528475044"

flyctl secrets set TELEGRAM_SPOT_ENABLED=true
flyctl secrets set TELEGRAM_SPOT_BOT_TOKEN="7978066969:AAH50-jMD0bFgUAxaRBRQ1w8I1AYh2Px3kY"
flyctl secrets set TELEGRAM_SPOT_CHAT_ID="8528475044"

flyctl secrets set TELEGRAM_ARBITRAGE_ENABLED=true
flyctl secrets set TELEGRAM_ARBITRAGE_BOT_TOKEN="8580479614:AAFir8-pT1dMxfCuvIpYI08pYcvUry-HyJc"
flyctl secrets set TELEGRAM_ARBITRAGE_CHAT_ID="8528475044"

# Trading Configuration
flyctl secrets set FUTURES_PAIRS=auto
flyctl secrets set FUTURES_AUTO_TOP_N=30
flyctl secrets set FUTURES_AUTO_MIN_VOLUME=1000000
flyctl secrets set FUTURES_VIRTUAL_CAPITAL=100
flyctl secrets set FUTURES_STOP_LOSS_PERCENT=2
flyctl secrets set FUTURES_TAKE_PROFIT_PERCENT=4
```

### Optional: Set All Secrets at Once
Create a file `secrets.txt` with all your variables:
```
EXCHANGE_ENVIRONMENT=production
FUTURES_EXCHANGE=kucoin
KUCOIN_API_KEY=your_key
KUCOIN_API_SECRET=your_secret
KUCOIN_API_PASSPHRASE=your_passphrase
...
```

Then set all at once:
```bash
cat secrets.txt | flyctl secrets import
```

**⚠️ IMPORTANT: Delete `secrets.txt` after importing!**

## 💾 Step 5: Create Persistent Volume (Optional)

For storing logs and trade history:
```bash
flyctl volumes create apex_data --region iad --size 1
```

## 🚀 Step 6: Deploy!

```bash
flyctl deploy
```

This will:
1. Build the Docker image
2. Push to Fly.io
3. Start your bot

## 📊 Step 7: Monitor Your Bot

### View Logs
```bash
flyctl logs
```

### Real-time Logs
```bash
flyctl logs -f
```

### Check Status
```bash
flyctl status
```

### SSH into Container
```bash
flyctl ssh console
```

## 🔧 Managing Your Bot

### Restart
```bash
flyctl apps restart apex-hunter-v14
```

### Stop
```bash
flyctl apps stop apex-hunter-v14
```

### Resume
```bash
flyctl apps resume apex-hunter-v14
```

### Update Code
After making changes locally:
```bash
flyctl deploy
```

### Scale (Upgrade Resources)
```bash
flyctl scale memory 1024  # Increase to 1GB RAM
```

## 💰 Fly.io Free Tier

The free tier includes:
- Up to 3 shared-cpu-1x VMs
- 512MB RAM per VM
- 3GB persistent volume storage
- 160GB outbound data transfer

**Your bot should run 24/7 on the free tier!**

## 🔍 Troubleshooting

### Check if secrets are set:
```bash
flyctl secrets list
```

### View environment:
```bash
flyctl ssh console
env | grep -E 'KUCOIN|TELEGRAM|FUTURES'
```

### Deployment failed?
```bash
flyctl logs --instance <instance-id>
```

### Delete and recreate:
```bash
flyctl apps destroy apex-hunter-v14
flyctl apps create apex-hunter-v14
# Re-set secrets and deploy again
```

## 📱 Verify It's Working

1. Check logs: `flyctl logs -f`
2. You should see:
   ```
   ✅ Futures Telegram bot connected
   Found 30 top pairs
   BTC/USDT | Price: $90,725.10
   ```
3. Check your Telegram - you should receive startup message
4. Wait a few minutes - you should see trade entries

## 🛡️ Security Best Practices

1. ✅ Never commit `.env` file
2. ✅ Always use `flyctl secrets` for sensitive data
3. ✅ Keep `FUTURES_TRADING_ENABLED=no` until fully tested
4. ✅ Use API keys with restricted permissions
5. ✅ Enable 2FA on your exchange accounts
6. ✅ Regularly monitor logs and Telegram

## 📈 Next Steps After Deployment

1. Monitor for 24-48 hours in paper trading mode
2. Review trade quality via Telegram notifications
3. Check P&L results
4. Once confident, you can enable live trading:
   ```bash
   flyctl secrets set FUTURES_TRADING_ENABLED=yes
   ```

## 🆘 Need Help?

- Fly.io Docs: https://fly.io/docs
- Check logs: `flyctl logs -f`
- SSH into container: `flyctl ssh console`
- Community: https://community.fly.io

---

## Quick Reference Commands

```bash
# Deploy
flyctl deploy

# View logs
flyctl logs -f

# Status
flyctl status

# Restart
flyctl apps restart

# Stop
flyctl apps stop

# SSH
flyctl ssh console

# Scale
flyctl scale memory 1024
```

Happy Trading! 🚀
