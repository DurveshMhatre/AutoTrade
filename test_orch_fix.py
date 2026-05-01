"""Quick test of the orchestrator fix with real market data."""
from agents.orchestrator import _local_orchestrator

# Simulate exact market data from logs (RSI=45.57, bullish, normal vol)
market = {
    "rsi": 45.57,
    "trend": "bullish",
    "volatility": "normal",
    "close": 77221.96,
    "bb_upper": 77400.0,
    "bb_lower": 77050.0,  # width = 350, which is 0.45% of 77222
}

portfolio = {
    "open_positions": 0,
    "max_positions": 3,
    "daily_loss_pct": 0.0,
}

result = _local_orchestrator(market, portfolio)
print(f"analyze: {result['analyze']}")
print(f"reason:  {result['reason']}")
print(f"risk:    {result['risk_level']}")
print(f"conf:    {result['confidence']}")
print()
bb_width = 77400.0 - 77050.0
pct = bb_width / 77222 * 100
print(f"BB width: ${bb_width:.2f} ({pct:.3f}% of close)")
print(f"Old threshold: 0.500% (${77222*0.005:.2f}) -> would have BLOCKED")
print(f"New threshold: 0.200% (${77222*0.002:.2f}) -> now PASSES")
