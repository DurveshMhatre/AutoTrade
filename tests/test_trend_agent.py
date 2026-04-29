"""
Integration smoke-test for the trend-following strategy agent.

Calls run_trend_agent with realistic mock market data and prints the result.
"""

import sys, os, json

# Ensure project root is on the path so imports resolve correctly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.trend_agent import run_trend_agent

# -- Mock market data (bullish scenario) --------------------------------------
MOCK_BULLISH = {
    "symbol": "BTC/USDT",
    "close": 67_450.20,
    "ema_short": 67_320.00,
    "ema_long": 67_100.50,
    "rsi": 54.2,
    "macd": 120.5,
    "macd_signal": 95.3,
    "macd_hist": 25.2,
    "bb_upper": 68_200.00,
    "bb_mid": 67_200.00,
    "bb_lower": 66_200.00,
    "atr": 310.0,
    "volume_sma": 1_200.0,
    "current_volume": 1_450.0,
    "trend": "bullish",
    "volatility": "normal",
    "volume_surge": True,
}

# -- Mock market data (bearish scenario) --------------------------------------
MOCK_BEARISH = {
    "symbol": "BTC/USDT",
    "close": 64_800.00,
    "ema_short": 64_900.00,
    "ema_long": 65_300.00,
    "rsi": 42.5,
    "macd": -85.0,
    "macd_signal": -60.0,
    "macd_hist": -25.0,
    "bb_upper": 66_000.00,
    "bb_mid": 65_100.00,
    "bb_lower": 64_200.00,
    "atr": 280.0,
    "volume_sma": 1_100.0,
    "current_volume": 980.0,
    "trend": "bearish",
    "volatility": "normal",
    "volume_surge": False,
}

# -- Mock market data (neutral / HOLD scenario) --------------------------------
MOCK_NEUTRAL = {
    "symbol": "BTC/USDT",
    "close": 66_000.00,
    "ema_short": 66_010.00,
    "ema_long": 65_990.00,
    "rsi": 50.0,
    "macd": 2.0,
    "macd_signal": 1.5,
    "macd_hist": 0.5,
    "bb_upper": 66_800.00,
    "bb_mid": 66_000.00,
    "bb_lower": 65_200.00,
    "atr": 150.0,
    "volume_sma": 1_000.0,
    "current_volume": 950.0,
    "trend": "neutral",
    "volatility": "low",
    "volume_surge": False,
}

SCENARIOS = [
    ("BULLISH scenario (expect BUY)", MOCK_BULLISH),
    ("BEARISH scenario (expect SELL)", MOCK_BEARISH),
    ("NEUTRAL scenario (expect HOLD)", MOCK_NEUTRAL),
]

# -- Run all scenarios ---------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  TREND AGENT -- LIVE TEST (3 scenarios)")
    print("=" * 60)

    for label, data in SCENARIOS:
        print(f"\n{'-' * 60}")
        print(f"  {label}")
        print(f"{'-' * 60}")

        print("\n[MARKET DATA]")
        print(json.dumps(data, indent=2))

        print("\n[STATUS] Calling Claude API ...")
        result = run_trend_agent(data)

        print("\n[RESULT] Trend Agent Signal:")
        print(json.dumps(result, indent=2))

        # Structural assertions
        assert result["signal"] in {"BUY", "SELL", "HOLD"}, \
            f"Invalid signal: {result['signal']}"
        assert 0.0 <= result["confidence"] <= 1.0, \
            f"Confidence out of range: {result['confidence']}"
        assert isinstance(result["reason"], str) and len(result["reason"]) > 0, \
            "Reason must be a non-empty string"
        assert isinstance(result["key_indicators"], dict), \
            "key_indicators must be a dict"
        print("\n[PASS] Assertions OK")

    print("\n" + "=" * 60)
    print("  ALL 3 SCENARIOS PASSED")
    print("=" * 60)
