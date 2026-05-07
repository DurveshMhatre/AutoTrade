import pytest
from core.trade_manager import TrailingStopManager

def test_trailing_stop_manager_buy_tier1():
    """Test BUY trade hitting Tier 1 target."""
    entry = 60000
    atr = 1000
    regime = "STRONG_TREND_UP"
    # Initial stop = 60000 - (1000 * 2.5) = 57500
    # R = 2500
    # Tier 1 = 60000 + (1.5 * 2500) = 63750
    # Tier 2 = 60000 + (3.0 * 2500) = 67500
    manager = TrailingStopManager(entry, "buy", atr, regime)
    
    assert manager.initial_stop == 57500
    assert manager.tier1_target == 63750
    assert manager.tier2_target == 67500
    
    # Simulate price going up but not hitting T1
    res = manager.update(62000, 1000)
    assert not res["actions"]
    assert manager.trailing_stop == 57500
    
    # Hit T1
    res = manager.update(64000, 1000)
    assert len(res["actions"]) == 1
    assert res["actions"][0]["action"] == "partial_close"
    assert res["actions"][0]["pct"] == 0.33
    assert manager.trailing_stop == 60000  # Breakeven stop
    
    # Hit T2
    res = manager.update(68000, 1000)
    assert len(res["actions"]) == 1
    assert res["actions"][0]["action"] == "partial_close"
    
def test_trailing_stop_manager_sell_trailing():
    """Test SELL trade trailing stop update."""
    entry = 60000
    atr = 1000
    regime = "STRONG_TREND_DOWN"
    # Initial stop = 60000 + (1000 * 2.5) = 62500
    # R = 2500
    manager = TrailingStopManager(entry, "sell", atr, regime)
    
    # Hit T1 (56250) and T2 (52500)
    manager.update(56000, 1000) # T1 hit
    manager.update(52000, 1000) # T2 hit
    
    # Price falls further, trailing stop tightens
    res = manager.update(50000, 500)
    # New trail = 50000 + (500 * 2.0) = 51000. 51000 < 60000 (breakeven), so it trails.
    assert manager.trailing_stop == 51000
    
    # Stop hit
    res2 = manager.update(51500, 500)
    assert len(res2["actions"]) == 1
    assert res2["actions"][0]["action"] == "close_all"
