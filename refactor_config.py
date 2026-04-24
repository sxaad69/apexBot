import re
import os

# 1. Read the .env file
env_vars = {}
with open('.env', 'r') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#'):
            parts = line.split('=', 1)
            if len(parts) == 2:
                env_vars[parts[0]] = parts[1]

# List of keys that MUST stay in .env
SECRETS = [
    'EXCHANGE', 'EXCHANGE_ENVIRONMENT', 'FUTURES_EXCHANGE', 'SPOT_EXCHANGE',
    'KUCOIN_API_KEY', 'KUCOIN_API_SECRET', 'KUCOIN_API_PASSPHRASE',
    'BINANCE_API_KEY', 'BINANCE_API_SECRET',
    'BYBIT_API_KEY', 'BYBIT_API_SECRET',
    'OKX_API_KEY', 'OKX_API_SECRET', 'OKX_API_PASSPHRASE',
    'GATE_API_KEY', 'GATE_API_SECRET',
    'TELEGRAM_BOT_TOKEN', 'TELEGRAM_USER_ID',
    'TELEGRAM_FUTURES_BOT_TOKEN', 'TELEGRAM_FUTURES_CHAT_ID',
    'TELEGRAM_SPOT_BOT_TOKEN', 'TELEGRAM_SPOT_CHAT_ID',
    'TELEGRAM_ARBITRAGE_BOT_TOKEN', 'TELEGRAM_ARBITRAGE_CHAT_ID',
    'EMERGENCY_SHUTDOWN_PASSWORD',
    'MONGODB_HOST', 'MONGODB_PORT', 'MONGODB_DATABASE', 'MONGODB_USERNAME', 'MONGODB_PASSWORD',
    'SQLITE_DB_NAME'
]

# 2. Update config.py
with open('config/config.py', 'r') as f:
    config_content = f.read()

def replace_getenv(match):
    full_match = match.group(0)
    key = match.group(1)
    default_val = match.group(2)
    
    # If it's a secret, keep it as os.getenv
    if key in SECRETS:
        return full_match
        
    # Otherwise, hardcode the value
    # Check if the user set it in .env
    val = env_vars.get(key)
    if val is None:
        # User didn't set it in .env, use the default from config.py
        val = default_val.strip("'\"")
    
    # Fix the dangerous values the user asked us to fix
    if key == 'FUTURES_MAX_OPEN_POSITIONS': val = '15'
    if key == 'MAX_DRAWDOWN_PERCENT': val = '10'
    if key == 'FUTURES_POSITION_SIZE_PERCENT': val = '2'
        
    # Determine the type to format the python code correctly
    if val.lower() in ('true', 'false', 'yes', 'no'):
        # It's a boolean-like string. config.py uses self._str_to_bool, so we can pass the string or just hardcode the bool
        # Since the original is self._str_to_bool(os.getenv(...)), we just return the string and let it wrap it
        return f"'{val}'"
    elif val.isdigit() or (val.replace('.', '', 1).isdigit() and val.count('.') == 1):
        # Number
        return f"'{val}'"
    else:
        # String
        return f"'{val}'"

# Find all os.getenv('KEY', 'default')
pattern = r"os\.getenv\(\s*['\"]([^'\"]+)['\"]\s*,\s*(['\"][^'\"]*['\"])\s*\)"
new_config_content = re.sub(pattern, replace_getenv, config_content)

# There are also os.getenv without default, like os.getenv('BINANCE_API_KEY', '') - these are in SECRETS so they won't be touched.

with open('config/config.py', 'w') as f:
    f.write(new_config_content)

# 3. Create a clean .env
clean_env = ""
for key in SECRETS:
    val = env_vars.get(key, '')
    clean_env += f"{key}={val}\n"

with open('.env', 'w') as f:
    f.write(clean_env)

print("Refactor complete.")
