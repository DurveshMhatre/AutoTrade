"""
Integration smoke-test for the orchestrator agent.

Calls run_orchestrator with realistic mock data across multiple scenarios
and prints the result. Run with: python tests/test_orchestrator.py
"""

import sys, os, json

# Ensure project root is on the path so imports resolve correctly
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.orchestrator import run_orchestrator

# ---------------------------------------------------------------------------
# Mock market data — multiple scenarios
# ---------------------------------------------------------------------------

# Scenario 1: Ideal bullish — should analyze
MOCK_MARKET_BULLISH = {
    "symbol": "BTC/USDT",
    "close": 67_450.20,
    "ema_20": 67_320.00,
    "ema_50": 67_100.50,
    "rsi": 54.2,
    "macd_line": 120.5,
    "macd_signal": 95.3,
    "macd_hist": 25.2,
    "bb_upper": 68_200.00,
    "bb_mid": 67_200.00,
    "bb_lower": 66_200.00,
    "atr": 310.0,
    "volume": 1_450.0,
    "volume_sma20": 1_200.0,
    "trend": "bullish",
    "volatility": "normal",
    "volume_surge": False,
}

# Scenario 2: Extreme volatility — should NOT analyze
MOCK_MARKET_EXTREME = {
    "symbol": "BTC/USDT",
    "close": 62_000.00,
    "ema_20": 63_500.00,
    "ema_50": 64_800.00,
    "rsi": 18.5,
    "macd_line": -350.0,
    "macd_signal": -200.0,
    "macd_hist": -150.0,
    "bb_upper": 65_000.00,
    "bb_mid": 63_500.00,
    "bb_lower": 62_000.00,
    "atr": 950.0,
    "volume": 3_200.0,
    "volume_sma20": 1_100.0,
    "trend": "bearish",
    "volatility": "high",
    "volume_surge": True,
}

# Scenario 3: Neutral trend — should NOT analyze
MOCK_MARKET_NEUTRAL = {
    "symbol": "BTC/USDT",
    "close": 66_000.00,
    "ema_20": 66_010.00,
    "ema_50": 65_990.00,
    "rsi": 50.0,
    "macd_line": 2.0,
    "macd_signal": 1.5,
    "macd_hist": 0.5,
    "bb_upper": 66_100.00,
    "bb_mid": 66_000.00,
    "bb_lower": 65_900.00,
    "atr": 150.0,
    "volume": 950.0,
    "volume_sma20": 1_000.0,
    "trend": "neutral",
    "volatility": "low",
    "volume_surge": False,
}

# ---------------------------------------------------------------------------
# Mock portfolio data
# ---------------------------------------------------------------------------
PORTFOLIO_NORMAL = {
    "open_positions": 1,
    "max_positions": 3,
    "daily_loss_pct": 0.012,
    "equity_usdt": 10_000.0,
}

PORTFOLIO_FULL = {
    "open_positions": 3,
    "max_positions": 3,
    "daily_loss_pct": 0.048,
    "equity_usdt": 9_500.0,
}

SCENARIOS = [
    ("BULLISH + capacity (expect analyze=true)", MOCK_MARKET_BULLISH, PORTFOLIO_NORMAL),
    ("EXTREME volatility (expect analyze=false)", MOCK_MARKET_EXTREME, PORTFOLIO_NORMAL),
    ("NEUTRAL trend (expect analyze=false)", MOCK_MARKET_NEUTRAL, PORTFOLIO_NORMAL),
    ("BULLISH but portfolio FULL (expect analyze=false)", MOCK_MARKET_BULLISH, PORTFOLIO_FULL),
]

# ---------------------------------------------------------------------------
# Run all scenarios
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=" * 60)
    print("  ORCHESTRATOR AGENT -- LIVE TEST (4 scenarios)")
    print("=" * 60)

    for label, market, portfolio in SCENARIOS:
        print(f"\n{'-' * 60}")
        print(f"  {label}")
        print(f"{'-' * 60}")

        print("\n[MARKET DATA]")
        print(json.dumps(market, indent=2))

        print("\n[PORTFOLIO]")
        print(json.dumps(portfolio, indent=2))

        print("\n[STATUS] Calling Claude API ...")
        result = run_orchestrator(market, portfolio)

        print("\n[RESULT] Orchestrator Decision:")
        print(json.dumps(result, indent=2))

        # Structural assertions
        assert isinstance(result["analyze"], bool), \
            f"analyze must be bool, got {type(result['analyze'])}"
        assert result["risk_level"] in {"low", "medium", "high"}, \
            f"Invalid risk_level: {result['risk_level']}"
        assert 0.0 <= result["confidence"] <= 1.0, \
            f"Confidence out of range: {result['confidence']}"
        assert isinstance(result["reason"], str) and len(result["reason"]) > 0, \
            "Reason must be a non-empty string"
        print("\n[PASS] Assertions OK")

    print("\n" + "=" * 60)
    print("  ALL 4 SCENARIOS PASSED")
    print("=" * 60)
