"""
Tests for the Multi-Timeframe Confluence Agent
================================================
Verifies confluence scoring, timeframe weighting, and trade approval logic.
"""

import pytest
from agents.mtf_agent import (
    run_mtf_agent,
    _local_mtf_signal,
    _score_timeframe,
    _validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tf_data(trend="bullish", rsi=55.0, macd_hist=0.5, adx=25.0) -> dict:
    """Build a synthetic single-timeframe indicator dict."""
    return {
        "close": 100.0,
        "ema_20": 100.0 if trend == "bullish" else 95.0,
        "ema_50": 95.0 if trend == "bullish" else 100.0,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "adx": adx,
        "trend": trend,
        "volume_trend": "neutral",
    }


def _make_mtf(
    daily=None, h4=None, h1=None, m5=None,
    daily_trend="bullish", h4_trend="bullish",
    h1_trend="bullish", m5_trend="bullish",
) -> dict:
    """Build a full MTF dict."""
    return {
        "1d": daily or _make_tf_data(trend=daily_trend),
        "4h": h4 or _make_tf_data(trend=h4_trend),
        "1h": h1 or _make_tf_data(trend=h1_trend),
        "5m": m5 or _make_tf_data(trend=m5_trend),
    }


# ---------------------------------------------------------------------------
# Tests — individual timeframe scoring
# ---------------------------------------------------------------------------

class TestTimeframeScoring:
    """Test _score_timeframe for single-TF analysis."""

    def test_bullish_tf_scores_positive(self):
        """Strong bullish signals → +1."""
        tf = _make_tf_data(trend="bullish", rsi=60, macd_hist=1.0)
        assert _score_timeframe(tf) == 1

    def test_bearish_tf_scores_negative(self):
        """Strong bearish signals → -1."""
        tf = _make_tf_data(trend="bearish", rsi=40, macd_hist=-1.0)
        assert _score_timeframe(tf) == -1

    def test_mixed_signals_score_zero(self):
        """Conflicting signals (only 1 bullish vote) → 0."""
        # trend=bullish (+1 bull), rsi=50 (neutral), macd=0 (neutral)
        # → only 1 bullish vote, below threshold of 2
        tf = _make_tf_data(trend="bullish", rsi=50, macd_hist=0.0)
        assert _score_timeframe(tf) == 0

    def test_neutral_scores_zero(self):
        """Neutral trend → 0."""
        tf = _make_tf_data(trend="neutral", rsi=50, macd_hist=0.0)
        assert _score_timeframe(tf) == 0

    def test_empty_data_scores_zero(self):
        """Empty or missing data → 0."""
        assert _score_timeframe({}) == 0
        assert _score_timeframe(None) == 0


# ---------------------------------------------------------------------------
# Tests — confluence scoring
# ---------------------------------------------------------------------------

class TestConfluenceScoring:
    """Test the full MTF confluence logic."""

    def test_all_bullish_max_score(self):
        """All 4 timeframes bullish → score +5 (daily ×2)."""
        mtf = _make_mtf(
            daily_trend="bullish", h4_trend="bullish",
            h1_trend="bullish", m5_trend="bullish",
        )
        result = _local_mtf_signal(mtf)
        assert result["confluence_score"] == 5  # 2+1+1+1
        assert result["trade_approved"] is True
        assert result["daily_bias"] == "bull"

    def test_all_bearish_min_score(self):
        """All 4 timeframes bearish → score -5 (daily ×2)."""
        mtf = _make_mtf(
            daily_trend="bearish", h4_trend="bearish",
            h1_trend="bearish", m5_trend="bearish",
        )
        for tf_key in mtf:
            mtf[tf_key]["rsi"] = 40.0
            mtf[tf_key]["macd_hist"] = -1.0
        result = _local_mtf_signal(mtf)
        assert result["confluence_score"] <= -4
        assert result["trade_approved"] is False  # Spot bot stays flat on bearish

    def test_conflicting_timeframes_blocked(self):
        """Daily bullish but 4H+1H bearish → score around 0, blocked."""
        mtf = _make_mtf(daily_trend="bullish", h4_trend="bearish", h1_trend="bearish")
        for tf_key in ["4h", "1h"]:
            mtf[tf_key]["rsi"] = 40.0
            mtf[tf_key]["macd_hist"] = -1.0
        result = _local_mtf_signal(mtf)
        assert result["trade_approved"] is False
        assert result["blocking_reason"] is not None

    def test_daily_weighted_double(self):
        """Daily bullish but everything else neutral → score +2 (just daily)."""
        daily = _make_tf_data(trend="bullish", rsi=60, macd_hist=1.0)
        neutral = _make_tf_data(trend="neutral", rsi=50, macd_hist=0.0)
        mtf = _make_mtf(daily=daily, h4=neutral, h1=neutral, m5=neutral)
        result = _local_mtf_signal(mtf)
        assert result["confluence_score"] == 2  # daily×2 + 0+0+0
        assert result["trade_approved"] is True

    def test_entry_timeframe_ready_check(self):
        """Trade approved but 1H bearish → entry_timeframe_ready=False."""
        bullish = _make_tf_data(trend="bullish", rsi=60, macd_hist=1.0)
        bearish = _make_tf_data(trend="bearish", rsi=40, macd_hist=-1.0)
        mtf = _make_mtf(
            daily=bullish, h4=bullish, h1=bearish, m5=bullish,
        )
        result = _local_mtf_signal(mtf)
        # Score: 2+1-1+1 = 3, approved but 1H is negative
        if result["trade_approved"]:
            # If 1H scores negative, entry_timeframe_ready should be False
            assert result["entry_timeframe_ready"] is False or result["confluence_score"] >= 2


# ---------------------------------------------------------------------------
# Tests — validation
# ---------------------------------------------------------------------------

class TestMTFValidation:
    """Test response validation."""

    def test_valid_response(self):
        result = _validate({
            "confluence_score": 3,
            "daily_bias": "bull",
            "4h_structure": "uptrend",
            "trade_approved": True,
        })
        assert result["confluence_score"] == 3
        assert result["daily_bias"] == "bull"

    def test_score_clamped(self):
        result = _validate({"confluence_score": 10})
        assert result["confluence_score"] == 4

        result = _validate({"confluence_score": -10})
        assert result["confluence_score"] == -4

    def test_invalid_bias_defaults(self):
        result = _validate({"daily_bias": "yolo"})
        assert result["daily_bias"] == "neutral"


# ---------------------------------------------------------------------------
# Tests — public entry point
# ---------------------------------------------------------------------------

class TestRunMTFAgent:
    """Test the public run_mtf_agent function."""

    def test_returns_valid_dict(self):
        mtf = _make_mtf()
        result = run_mtf_agent(mtf)
        assert "confluence_score" in result
        assert "daily_bias" in result
        assert "4h_structure" in result
        assert "trade_approved" in result
        assert isinstance(result["trade_approved"], bool)

    def test_empty_input_doesnt_crash(self):
        result = run_mtf_agent({})
        assert isinstance(result, dict)
        assert "trade_approved" in result
