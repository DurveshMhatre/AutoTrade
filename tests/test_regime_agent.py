"""
Tests for the Market Regime Detection Agent
============================================
Verifies all 7 regime classifications with synthetic multi-timeframe data.
"""

import pytest
from agents.regime_agent import (
    run_regime_agent,
    _local_regime_signal,
    _validate,
    VALID_REGIMES,
    VALID_BIASES,
    _cached_regime,
)


# ---------------------------------------------------------------------------
# Helpers — build synthetic MTF indicator dicts
# ---------------------------------------------------------------------------

def _make_mtf(
    daily_trend="bullish",
    daily_rsi=55.0,
    daily_adx=30.0,
    daily_ema_20=100.0,
    daily_ema_50=95.0,
    daily_ema_200=85.0,
    h4_trend="bullish",
    h4_adx=28.0,
    h1_trend="bullish",
    h1_volume_trend="neutral",
    m5_rsi=50.0,
    m5_adx=25.0,
    m5_volume_surge=False,
) -> dict:
    """Build a synthetic multi-timeframe indicator dict."""
    return {
        "5m": {
            "close": 100.0,
            "rsi": m5_rsi,
            "adx": m5_adx,
            "trend": "bullish",
            "volume_surge": m5_volume_surge,
        },
        "1h": {
            "close": 100.0,
            "ema_20": 100.0,
            "ema_50": 98.0,
            "rsi": 55.0,
            "adx": 25.0,
            "trend": h1_trend,
            "volume_trend": h1_volume_trend,
        },
        "4h": {
            "close": 100.0,
            "ema_20": 100.0,
            "ema_50": 97.0,
            "rsi": 55.0,
            "adx": h4_adx,
            "trend": h4_trend,
            "volume_trend": "neutral",
        },
        "1d": {
            "close": 100.0,
            "ema_20": daily_ema_20,
            "ema_50": daily_ema_50,
            "ema_200": daily_ema_200,
            "rsi": daily_rsi,
            "adx": daily_adx,
            "trend": daily_trend,
            "volume_trend": "neutral",
        },
    }


# ---------------------------------------------------------------------------
# Tests — regime classification
# ---------------------------------------------------------------------------

class TestRegimeClassification:
    """Test each of the 7 regimes fires correctly."""

    def test_strong_trend_up(self):
        """Bullish EMA stack + high ADX → STRONG_TREND_UP."""
        mtf = _make_mtf(
            daily_trend="bullish", daily_adx=32, daily_rsi=58,
            daily_ema_20=100, daily_ema_50=95, daily_ema_200=85,
            h4_trend="bullish", h4_adx=30,
            h1_trend="bullish",
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "STRONG_TREND_UP"
        assert result["strategy_bias"] == "trend_follow"
        assert result["position_size_multiplier"] > 1.0

    def test_weak_trend_up(self):
        """Bullish daily but EMA stack not aligned → WEAK_TREND_UP."""
        mtf = _make_mtf(
            daily_trend="bullish", daily_adx=26, daily_rsi=52,
            daily_ema_20=100, daily_ema_50=102, daily_ema_200=85,  # 20 < 50 breaks stack
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "WEAK_TREND_UP"
        assert result["strategy_bias"] == "trend_follow"
        assert result["position_size_multiplier"] <= 1.0

    def test_ranging(self):
        """Moderate ADX, mixed signals → RANGING."""
        mtf = _make_mtf(
            daily_trend="neutral", daily_adx=22, daily_rsi=50,
            daily_ema_20=100, daily_ema_50=100, daily_ema_200=95,
            h4_adx=22,
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "RANGING"
        assert result["strategy_bias"] == "mean_revert"

    def test_strong_trend_down(self):
        """Bearish EMA stack + high ADX → STRONG_TREND_DOWN."""
        mtf = _make_mtf(
            daily_trend="bearish", daily_adx=35, daily_rsi=35,
            daily_ema_20=85, daily_ema_50=95, daily_ema_200=100,  # bearish stack
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "STRONG_TREND_DOWN"
        assert result["strategy_bias"] == "flat"
        assert result["position_size_multiplier"] == 0.0

    def test_distribution(self):
        """Bullish with high RSI + declining volume → DISTRIBUTION."""
        mtf = _make_mtf(
            daily_trend="bullish", daily_adx=22, daily_rsi=72,
            h4_adx=18,
            h1_volume_trend="decreasing",
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "DISTRIBUTION"
        assert result["strategy_bias"] == "flat"

    def test_capitulation(self):
        """Extreme low RSI + volume spike + bearish → CAPITULATION."""
        mtf = _make_mtf(
            daily_trend="bearish", daily_adx=40, daily_rsi=18,
            m5_volume_surge=True,
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "CAPITULATION"

    def test_chop(self):
        """Very low ADX on daily + 4H → CHOP."""
        mtf = _make_mtf(
            daily_trend="neutral", daily_adx=15, daily_rsi=50,
            daily_ema_20=100, daily_ema_50=100, daily_ema_200=100,
            h4_adx=14,
        )
        result = _local_regime_signal(mtf)
        assert result["regime"] == "CHOP"
        assert result["strategy_bias"] == "flat"
        assert result["position_size_multiplier"] == 0.0


# ---------------------------------------------------------------------------
# Tests — validation
# ---------------------------------------------------------------------------

class TestRegimeValidation:
    """Test the response validation function."""

    def test_valid_regime(self):
        result = _validate({"regime": "STRONG_TREND_UP", "confidence": 0.85})
        assert result["regime"] == "STRONG_TREND_UP"
        assert result["confidence"] == 0.85

    def test_invalid_regime_defaults(self):
        result = _validate({"regime": "UNKNOWN_REGIME"})
        assert result["regime"] == "RANGING"

    def test_confidence_clamped(self):
        result = _validate({"regime": "CHOP", "confidence": 1.5})
        assert result["confidence"] == 1.0

    def test_invalid_bias_defaults(self):
        result = _validate({"regime": "CHOP", "strategy_bias": "yolo"})
        assert result["strategy_bias"] == "flat"

    def test_multiplier_clamped(self):
        result = _validate({"regime": "CHOP", "position_size_multiplier": 5.0})
        assert result["position_size_multiplier"] == 1.5


# ---------------------------------------------------------------------------
# Tests — public entry point
# ---------------------------------------------------------------------------

class TestRunRegimeAgent:
    """Test the public run_regime_agent function."""

    def test_returns_valid_dict(self):
        """Entry point should always return a valid dict."""
        mtf = _make_mtf()
        result = run_regime_agent(mtf, force=True)
        assert "regime" in result
        assert result["regime"] in VALID_REGIMES
        assert "confidence" in result
        assert "strategy_bias" in result
        assert result["strategy_bias"] in VALID_BIASES
        assert "position_size_multiplier" in result
        assert "reasoning" in result

    def test_empty_input_doesnt_crash(self):
        """Empty input should return safe default, not crash."""
        result = run_regime_agent({}, force=True)
        assert result["regime"] in VALID_REGIMES
