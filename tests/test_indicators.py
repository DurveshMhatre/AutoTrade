"""
Tests for core.indicators.compute_indicators
=============================================
Uses 100 synthetic OHLCV candles so every indicator (including EMA 50)
has sufficient history to produce non-NaN values.
"""

import math
import random
import pytest

from core.indicators import compute_indicators


# ---------------------------------------------------------------------------
# Fixture: generate 100 realistic-ish OHLCV candles
# ---------------------------------------------------------------------------
def _make_candles(n: int = 100) -> list:
    """Generate *n* synthetic OHLCV candle dicts with a random-walk price."""
    random.seed(42)
    candles = []
    price = 50_000.0  # starting price (BTC-like)

    for i in range(n):
        change = random.uniform(-500, 500)
        open_ = price
        close = price + change
        high = max(open_, close) + random.uniform(0, 300)
        low = min(open_, close) - random.uniform(0, 300)
        volume = random.uniform(50, 500)

        candles.append(
            {
                "timestamp": 1_700_000_000 + i * 60,
                "open": round(open_, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "close": round(close, 2),
                "volume": round(volume, 2),
            }
        )
        price = close
    return candles


CANDLES = _make_candles()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {
    "timestamp",
    "close",
    "ema_20",
    "ema_50",
    "rsi",
    "bb_upper",
    "bb_mid",
    "bb_lower",
    "macd_line",
    "macd_signal",
    "macd_hist",
    "atr",
    "volume",
    "volume_sma20",
    "trend",
    "volatility",
    "volume_surge",
}


def test_compute_returns_all_keys():
    """compute_indicators must return every expected key."""
    result = compute_indicators(CANDLES)
    assert isinstance(result, dict)
    assert set(result.keys()) == EXPECTED_KEYS


def test_trend_is_valid_value():
    """trend must be one of 'bullish', 'bearish', or 'neutral'."""
    result = compute_indicators(CANDLES)
    assert result["trend"] in ("bullish", "bearish", "neutral")


def test_no_nan_values_in_output():
    """No float field in the output should be NaN (NaN must be mapped to None)."""
    result = compute_indicators(CANDLES)
    for key, value in result.items():
        if isinstance(value, float):
            assert not math.isnan(value), f"NaN found in '{key}'"
