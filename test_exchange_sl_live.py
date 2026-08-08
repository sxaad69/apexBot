import sys, os, time, traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

import main as main_mod
from main import PaperTradingEngine
from config.config import Config
from exchange.ccxt_client import CCXTExchangeClient

class FakeLogger:
    def info(self, *a, **k): print("[info]", *a)
    def warning(self, *a, **k): print("[warn]", *a)
    def error(self, *a, **k): print("[error]", *a)
    def debug(self, *a, **k): pass
    def system(self, *a, **k): print("[system]", *a)

def make_engine():
    cfg = Config()
    logger = FakeLogger()
    eng = PaperTradingEngine.__new__(PaperTradingEngine)
    eng.config = cfg
    eng.logger = logger
    eng.telegram = None
    eng.mode = 'live'
    eng._order_status_cache = {}
    eng._order_status_ttl = getattr(cfg, 'ORDER_STATUS_CACHE_TTL', 5.0)
    eng._open_algo_cache = {}
    eng.exchange = CCXTExchangeClient(cfg, logger, cfg.FUTURES_EXCHANGE)
    eng.trade_manager = None
    eng.positions = {}
    return eng

def main():
    eng = make_engine()
    exchange = eng.exchange.exchange
    env = eng.config.EXCHANGE_ENVIRONMENT
    print(f"ENV={env} ccxt={__import__('ccxt').__version__}")
    if env != 'testnet':
        print("REFUSING to run against non-testnet environment")
        sys.exit(1)

    symbol = 'BTC/USDT:USDT'
    eng.exchange.exchange.load_markets()
    ticker = eng.exchange.get_ticker(symbol)
    px = float(ticker['last'])
    print(f"BTC price: {px}")

    # 1. Open a position (min 0.001 BTC)
    qty = 0.001
    order = exchange.create_order(symbol, 'market', 'buy', qty)
    print("Entry order:", order.get('id'))
    time.sleep(1)

    # 2. Test _market_supports_trailing_stop
    print("supports trailing:", eng._market_supports_trailing_stop(symbol))

    # 3. Place the three conditional orders via the REAL bot methods
    side = 'sell'
    sl_price = round(px * 0.97, 1)
    tp_price = round(px * 1.03, 1)
    act_price = round(px * 1.01, 1)

    sl_id = eng._place_exchange_conditional(symbol, side, 'STOP_MARKET', quantity=qty,
                                            trigger_price=sl_price,
                                            client_algo_id='tstrealSL0001')
    tr_id = eng._place_exchange_conditional(symbol, side, 'TRAILING_STOP_MARKET', quantity=qty,
                                            activate_price=act_price, callback_rate=3.0,
                                            client_algo_id='tstrealTR0001')
    tp_id = eng._place_exchange_conditional(symbol, side, 'TAKE_PROFIT_MARKET', quantity=qty,
                                            trigger_price=tp_price,
                                            client_algo_id='tstrealTP0001')
    print(f"SL={sl_id} TR={tr_id} TP={tp_id}")

    # 4. Verify they appear in openAlgoOrders via the REAL detection method
    time.sleep(1)
    open_ids = eng._get_cached_open_algo_ids(symbol)
    print(f"openAlgoIds set: {sorted(open_ids)}")
    for label, oid in (('SL', sl_id), ('TR', tr_id), ('TP', tp_id)):
        print(f"  {label} {oid} present: {str(oid) in open_ids}")

    # 5. Cancel all three via the REAL cancel method, verify they drop out
    for oid in (sl_id, tr_id, tp_id):
        eng._cancel_exchange_conditional(symbol, oid)
    time.sleep(1)
    eng._open_algo_cache.pop(symbol, None)  # bypass TTL
    open_ids2 = eng._get_cached_open_algo_ids(symbol)
    print(f"after cancel, openAlgoIds: {sorted(open_ids2)}")

    # 6. Close the position
    exchange.create_order(symbol, 'market', 'sell', qty, {'reduceOnly': True})
    print("closed position. ALL OK")

if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
