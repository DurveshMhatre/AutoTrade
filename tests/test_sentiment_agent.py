"""
Tests for the On-Chain + Sentiment Intelligence Agent
======================================================
Verifies scoring logic, classification rules, and validation.
Uses mocked API responses to avoid network calls during testing.
"""

import pytest
from unittest.mock import patch, AsyncMock

from agents.sentiment_agent import (
    _local_sentiment_signal,
    _classify_fear_greed,
    _classify_funding,
    _validate,
    VALID_FG_LABELS,
    VALID_FUNDING_SIGNALS,
    VALID_BIAS_ADJUSTMENTS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_data(
    fg_score=50,
    fg_label="neutral",
    funding_rate=0.0,
) -> dict:
    """Build synthetic raw sentiment data."""
    return {
        "fear_greed": {"score": fg_score, "label": fg_label},
        "funding_rate": funding_rate,
    }


# ---------------------------------------------------------------------------
# Tests — Fear & Greed classification
# ---------------------------------------------------------------------------

class TestFearGreedClassification:
    """Test _classify_fear_greed at various score levels."""

    def test_extreme_fear(self):
        assert _classify_fear_greed(10) == "extreme_fear"
        assert _classify_fear_greed(20) == "extreme_fear"

    def test_fear(self):
        assert _classify_fear_greed(25) == "fear"
        assert _classify_fear_greed(40) == "fear"

    def test_neutral(self):
        assert _classify_fear_greed(50) == "neutral"

    def test_greed(self):
        assert _classify_fear_greed(65) == "greed"
        assert _classify_fear_greed(79) == "greed"

    def test_extreme_greed(self):
        assert _classify_fear_greed(80) == "extreme_greed"
        assert _classify_fear_greed(95) == "extreme_greed"


# ---------------------------------------------------------------------------
# Tests — funding rate classification
# ---------------------------------------------------------------------------

class TestFundingClassification:
    """Test _classify_funding at various rate levels."""

    def test_overleveraged_long(self):
        assert _classify_funding(0.002) == "overleveraged_long"
        assert _classify_funding(0.005) == "overleveraged_long"

    def test_overleveraged_short(self):
        assert _classify_funding(-0.001) == "overleveraged_short"

    def test_neutral(self):
        assert _classify_funding(0.0) == "neutral"
        assert _classify_funding(0.0005) == "neutral"
        assert _classify_funding(-0.0003) == "neutral"


# ---------------------------------------------------------------------------
# Tests — local sentiment signal
# ---------------------------------------------------------------------------

class TestLocalSentimentSignal:
    """Test the deterministic scoring logic."""

    def test_extreme_fear_reduces_score(self):
        """Extreme fear (FG < 15) should give negative sentiment score."""
        raw = _make_raw_data(fg_score=10)
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] < 0
        assert result["fear_greed_label"] == "extreme_fear"

    def test_extreme_greed_increases_score(self):
        """Extreme greed (FG > 85) should give positive sentiment score."""
        raw = _make_raw_data(fg_score=90)
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] > 0
        assert result["fear_greed_label"] == "extreme_greed"

    def test_overleveraged_long_reduces_score(self):
        """High positive funding → bearish signal (long squeeze risk)."""
        raw = _make_raw_data(funding_rate=0.005)
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] < 0
        assert result["funding_signal"] == "overleveraged_long"

    def test_overleveraged_short_boosts_score(self):
        """Negative funding → bullish signal (short squeeze potential)."""
        raw = _make_raw_data(funding_rate=-0.002)
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] > 0
        assert result["funding_signal"] == "overleveraged_short"

    def test_neutral_conditions(self):
        """Neutral F&G + neutral funding → neutral."""
        raw = _make_raw_data(fg_score=50, funding_rate=0.0)
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] == 0
        assert result["trade_bias_adjustment"] == "neutral"

    def test_strongly_negative_goes_flat(self):
        """Extreme fear + high funding → flat bias."""
        raw = _make_raw_data(
            fg_score=8,
            funding_rate=0.005,
        )
        result = _local_sentiment_signal(raw)
        assert result["combined_sentiment"] <= -6
        assert result["trade_bias_adjustment"] == "flat"


# ---------------------------------------------------------------------------
# Tests — validation
# ---------------------------------------------------------------------------

class TestSentimentValidation:
    """Test _validate normalisation."""

    def test_valid_response(self):
        result = _validate({
            "fear_greed_score": 25,
            "fear_greed_label": "fear",
            "funding_rate": 0.0005,
            "funding_signal": "neutral",
            "combined_sentiment": -2,
        })
        assert result["fear_greed_score"] == 25
        assert result["combined_sentiment"] == -2

    def test_fg_score_clamped(self):
        result = _validate({"fear_greed_score": 150})
        assert result["fear_greed_score"] == 100

        result = _validate({"fear_greed_score": -10})
        assert result["fear_greed_score"] == 0

    def test_combined_clamped(self):
        result = _validate({"combined_sentiment": 25})
        assert result["combined_sentiment"] == 10

    def test_invalid_bias_defaults(self):
        result = _validate({"trade_bias_adjustment": "moon"})
        assert result["trade_bias_adjustment"] == "neutral"

    def test_all_keys_present(self):
        result = _validate({})
        for key in [
            "fear_greed_score", "fear_greed_label", "funding_rate",
            "funding_signal", "combined_sentiment",
            "trade_bias_adjustment", "risk_note",
        ]:
            assert key in result
