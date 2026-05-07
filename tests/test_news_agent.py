"""
Tests for News & Social Sentiment NLP Agent
"""

import pytest
from agents.news_agent import _local_news_signal, _validate

class TestNewsAgent:
    def test_local_news_bullish(self):
        headlines = [
            {"title": "Bitcoin ETF approval by SEC", "source": "Bloomberg"},
            {"title": "Institutional adoption grows", "source": "CoinDesk"}
        ]
        result = _local_news_signal(headlines)
        # Approval (bullish) + SEC (bearish but overridden? No, SEC is bearish kw).
        # Let's check scoring logic: "approval" (+1), "sec" (-1) -> 0 * 2.0 (major event) = 0 for first headline.
        # Second: "institutional" (+1), "adoption" (+1) -> 2.0 * 1.0 = 2.0
        # Avg = 1.0 (neutral) -> Let's make it clearly bullish
        headlines2 = [
            {"title": "Bitcoin ETF approval rally", "source": "Bloomberg"}, # +2
            {"title": "Institutional adoption surge", "source": "CoinDesk"} # +2
        ]
        res2 = _local_news_signal(headlines2)
        assert res2["weighted_sentiment_score"] > 2.0
        assert res2["sentiment_label"] == "bullish"
        assert res2["trading_action"] == "boost"

    def test_local_news_major_bearish_event(self):
        headlines = [
            {"title": "Major exchange hack exploit drains $500M", "source": "Reuters"}
        ]
        # hack (-1), exploit (-1) -> -2
        # source Reuters (1.5)
        # major kw hack (2.0)
        # Final: -2 * 1.5 * 2.0 = -6.0 -> capped at -5.0
        result = _local_news_signal(headlines)
        assert result["weighted_sentiment_score"] <= -4.0
        assert result["sentiment_label"] == "very_bearish"
        assert result["trading_action"] == "pause"
        assert result["major_event_detected"] == True

    def test_empty_headlines(self):
        result = _local_news_signal([])
        assert result["trading_action"] == "neutral"
        assert result["weighted_sentiment_score"] == 0.0

    def test_validate(self):
        parsed = {
            "weighted_sentiment_score": 10.0, # clamped
            "trading_action": "buy_all" # invalid
        }
        res = _validate(parsed)
        assert res["weighted_sentiment_score"] == 5.0
        assert res["trading_action"] == "neutral"
