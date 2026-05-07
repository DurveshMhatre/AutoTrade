"""
Tests for Support & Resistance Detection Agent
"""

import pytest
from agents.sr_agent import _local_sr_signal, _validate

def _make_candles(price=50000):
    candles = []
    # Diagonal line to avoid algorithmic micro-pivots
    for i in range(100):
        candles.append({
            "high": price + i,
            "low": price + i,
            "close": price + i,
            "volume": 100
        })
    # Add a clear high
    candles[50]["high"] = price + 5000
    # Add a clear low
    candles[60]["low"] = price - 5000
    return candles

class TestSRAgent:
    def test_local_sr_signal_mid_air(self):
        # Make a wide range to ensure price is in mid-air
        candles = _make_candles(50000)
        candles[50]["high"] = 55000
        candles[60]["low"] = 45000
        result = _local_sr_signal(candles, 50000)
        assert result["entry_quality"] == "fair"
        assert result["confidence_adjustment"] == -0.05

    def test_local_sr_signal_near_support(self):
        candles = _make_candles(45000)
        # Add strong support at 44500, resistance at 49000
        candles[50]["high"] = 49000
        candles[60]["low"] = 44500
        # Price is 44550. Dist to support = 50, Dist to res = 4450
        result = _local_sr_signal(candles, 44550)
        assert result["entry_quality"] == "excellent"
        assert result["confidence_adjustment"] > 0
        assert result["nearest_support"] <= 44550

    def test_local_sr_signal_near_resistance(self):
        candles = _make_candles(45000)
        # Add strong resistance at 48000
        candles[50]["high"] = 48000
        candles[60]["low"] = 41000
        # Price is 47950. Dist to res = 50.
        result = _local_sr_signal(candles, 47950)
        assert result["entry_quality"] == "poor"
        assert result["confidence_adjustment"] < 0
        assert result["nearest_resistance"] >= 47950

    def test_validate(self):
        parsed = {
            "nearest_support": 49000,
            "nearest_resistance": 51000,
            "entry_quality": "moon", # invalid
            "confidence_adjustment": 0.5 # clamped
        }
        res = _validate(parsed, 50000)
        assert res["nearest_support"] == 49000
        assert res["entry_quality"] == "fair"
        assert res["confidence_adjustment"] == 0.2
