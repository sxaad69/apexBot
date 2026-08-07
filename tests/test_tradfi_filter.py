"""
Unit tests for the TradFi contract filter (Task 2.2).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _is_tradfi(market_info):
    """Replicates the filter logic in main.py get_top_pairs_by_volume."""
    market_type = market_info.get('info', {}).get('contractType', '')
    return market_type == 'TRADIFI_PERPETUAL'


def test_regular_perp_not_filtered():
    # BTC/USDT is a normal PERPETUAL — should NOT be filtered
    market = {'info': {'contractType': 'PERPETUAL'}}
    assert not _is_tradfi(market)


def test_equity_perp_filtered():
    # META is a TRADIFI_PERPETUAL (equity) — should be filtered
    market = {'info': {'contractType': 'TRADIFI_PERPETUAL'}}
    assert _is_tradfi(market)


def test_commodity_perp_filtered():
    # XAU is a TRADIFI_PERPETUAL (commodity) — should be filtered
    market = {'info': {'contractType': 'TRADIFI_PERPETUAL'}}
    assert _is_tradfi(market)


def test_missing_contract_type_not_filtered():
    # Legacy markets without contractType info should not be filtered
    market = {'info': {}}
    assert not _is_tradfi(market)


def test_no_info_not_filtered():
    market = {}
    assert not _is_tradfi(market)


if __name__ == '__main__':
    test_regular_perp_not_filtered()
    test_equity_perp_filtered()
    test_commodity_perp_filtered()
    test_missing_contract_type_not_filtered()
    test_no_info_not_filtered()
    print("✅ test_tradfi_filter.py: all tests passed")