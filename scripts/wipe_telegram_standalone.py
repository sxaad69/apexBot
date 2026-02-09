import json
import os
import requests
import time
from pathlib import Path
from dotenv import load_dotenv

def wipe_telegram():
    load_dotenv()
    
    history_file = Path("data/telegram_history.json")
    if not history_file.exists():
        print("ℹ️ No Telegram message history found to wipe")
        return

    print("🗑️ Starting Telegram message wipe...")
    
    try:
        with open(history_file, 'r') as f:
            history = json.load(f)
    except Exception as e:
        print(f"❌ Error reading history: {e}")
        return

    if not history:
        print("ℹ️ History is empty")
        return

    # Use tokens from environment
    tokens = {
        "Futures": os.getenv("TELEGRAM_FUTURES_BOT_TOKEN"),
        "Spot": os.getenv("TELEGRAM_SPOT_BOT_TOKEN"),
        "Arbitrage": os.getenv("TELEGRAM_ARBITRAGE_BOT_TOKEN")
    }

    deleted_count = 0
    failed_count = 0
    
    for entry in history:
        msg_id = entry.get('message_id')
        chat_id = entry.get('chat_id')
        bot_type = entry.get('bot')
        token = tokens.get(bot_type)

        if not token or not msg_id or not chat_id:
            continue

        url = f"https://api.telegram.org/bot{token}/deleteMessage"
        payload = {"chat_id": chat_id, "message_id": msg_id}
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            if response.status_code == 200:
                deleted_count += 1
            else:
                # 400 often means already deleted or too old
                failed_count += 1
        except Exception as e:
            print(f"❌ Error deleting {msg_id}: {e}")
            failed_count += 1
        
        # Simple rate limit protection
        time.sleep(0.05)

    print(f"✅ Wipe complete: {deleted_count} messages deleted, {failed_count} failed/skipped")
    
    # Clear the history file after wipe
    with open(history_file, 'w') as f:
        json.dump([], f)

if __name__ == "__main__":
    wipe_telegram()
