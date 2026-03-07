#!/bin/bash
# APEX HUNTER V14 - Quick Deploy to Fly.io
# This script automates the deployment process

set -e  # Exit on error

echo "🚀 APEX HUNTER V14 - Fly.io Deployment"
echo "======================================"
echo ""

# Check if flyctl is installed
if ! command -v flyctl &> /dev/null; then
    echo "❌ flyctl not found!"
    echo "Please install it first:"
    echo "  macOS: brew install flyctl"
    echo "  Linux: curl -L https://fly.io/install.sh | sh"
    exit 1
fi

# Check if logged in
if ! flyctl auth whoami &> /dev/null; then
    echo "🔐 Please login to Fly.io first:"
    flyctl auth login
fi

echo "✅ Fly.io CLI ready"
echo ""

# Check if app exists, create if not
echo "📱 Checking Fly.io app..."
if ! flyctl status &> /dev/null; then
    echo "Creating Fly.io app..."
    flyctl launch --no-deploy --copy-config
fi

# Read .env file and set secrets
echo "📝 Setting environment variables from .env file..."
echo ""

if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    echo "Please create .env file with your configuration"
    exit 1
fi

# Extract secrets from .env (excluding comments and empty lines)
SECRETS=$(grep -v '^#' .env | grep -v '^$' | grep '=' || true)

if [ -z "$SECRETS" ]; then
    echo "❌ No secrets found in .env file"
    exit 1
fi

# Count secrets
SECRET_COUNT=$(echo "$SECRETS" | wc -l | tr -d ' ')
echo "Found $SECRET_COUNT environment variables"
echo ""

# Confirm before proceeding
read -p "Deploy secrets to Fly.io? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Deployment cancelled"
    exit 1
fi

# Import secrets
echo "$SECRETS" | flyctl secrets import

echo ""
echo "✅ Secrets imported successfully"
echo ""

# Deploy
echo "🚀 Deploying to Fly.io..."
flyctl deploy

echo ""
echo "======================================"
echo "✅ Deployment Complete!"
echo "======================================"
echo ""
echo "📊 View logs:"
echo "  flyctl logs -f"
echo ""
echo "📱 Check Telegram for startup message"
echo ""
echo "🛡️ Bot is running in PAPER TRADING mode (no real money)"
echo ""
