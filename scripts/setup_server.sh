#!/bin/bash

# Apex Hunter V14 - Ubuntu 24.04 Server Setup Script
# This script automates the installation of all dependencies for EC2 deployment.

set -e

echo "🚀 Starting Apex Hunter V14 Server Setup..."

# 1. Update & Install System Dependencies
echo "📦 Updating system packages..."
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y build-essential wget curl git python3-pip python3-venv libssl-dev libffi-dev python3-dev

# 2. Add Swap File (Crucial for t3.micro / 1GB RAM)
echo "⚡ Adding 2GB Swap file for stability..."
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "✅ Swap file created and activated"
else
    echo "✅ Swap file already exists, skipping..."
fi

# 3. Install TA-Lib (Required for many technical strategies)
echo "📈 Installing TA-Lib (Technical Analysis Library)..."
if [ ! -f /usr/local/lib/libta_lib.so ]; then
    wget http://prdownloads.sourceforge.net/ta-lib/ta-lib-0.4.0-src.tar.gz
    tar -xzf ta-lib-0.4.0-src.tar.gz
    cd ta-lib/
    ./configure --prefix=/usr/local
    make
    sudo make install
    cd ..
    rm -rf ta-lib ta-lib-0.4.0-src.tar.gz
    sudo ldconfig
    echo "✅ TA-Lib installed successfully"
else
    echo "✅ TA-Lib already exists, skipping..."
fi

# 3. Setup Python Virtual Environment
echo "🐍 Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate

# 4. Install Python Dependencies
echo "📚 Installing Python packages..."
pip install --upgrade pip
pip install -r requirements.txt

# 5. Create Data Architecture
echo "📁 Creating data directories..."
mkdir -p data logs data/ohlcv_cache data/reports

# 6. Install systemd units (bot + daily forensics audit)
echo "⏰ Installing systemd units..."
sudo cp apex-bot.service /etc/systemd/system/apex-bot.service
sudo cp apex-forensics.service /etc/systemd/system/apex-forensics.service
sudo cp apex-forensics.timer /etc/systemd/system/apex-forensics.timer
sudo systemctl daemon-reload
sudo systemctl enable apex-forensics.timer
sudo systemctl start apex-forensics.timer
echo "✅ apex-forensics.timer enabled (daily 23:55 UTC settle audit)"

# 7. Final Instructions
echo ""
echo "===================================================="
echo "✅ SERVER SETUP COMPLETE!"
echo "===================================================="
echo "Next Steps:"
echo "1. Configure your .env file with production keys."
echo "2. Run the bot manually to test: source venv/bin/activate && python3 main.py"
echo "3. Start the bot service:  sudo systemctl enable --now apex-bot.service"
echo "4. Audit reports land in data/reports/forensics_report_YYYY-MM-DD.json"
echo "===================================================="
