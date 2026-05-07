"""
Tests for Order Flow Agent
"""

import pytest
from agents.order_flow_agent import _local_order_flow, _validate

class TestOrderFlowAgent:
    def test_local_order_flow_bullish(self):
        order_book = {
            "bids": [[49900, 10], [49800, 20]], # total vol 30
            "asks": [[50100, 5], [50200, 10]]   # total vol 15
        }
        vwap_data = {"vwap": 49000, "poc_price": 49000}
        # price 50000 > vwap, B/A ratio 2.0 > 1.5
        result = _local_order_flow(order_book, vwap_data, 50000)
        assert result["bid_ask_ratio"] == 2.0
        assert result["order_flow_bias"] == "bullish"
        assert result["price_vs_vwap"] == "above"
        assert result["order_flow_confidence_adj"] == 0.10 # 0.05 + 0.05

    def test_local_order_flow_bearish(self):
        order_book = {
            "bids": [[49900, 5]], 
            "asks": [[50100, 20]] 
        }
        vwap_data = {"vwap": 51000}
        # price 50000 < vwap, B/A ratio 0.25 < 0.67
        result = _local_order_flow(order_book, vwap_data, 50000)
        assert result["order_flow_bias"] == "bearish"
        assert result["price_vs_vwap"] == "below"
        assert result["order_flow_confidence_adj"] == -0.10

    def test_validate(self):
        parsed = {
            "bid_ask_ratio": 1.2,
            "order_flow_bias": "moon", # invalid
            "order_flow_confidence_adj": 0.5
        }
        res = _validate(parsed)
        assert res["order_flow_bias"] == "neutral"
        assert res["order_flow_confidence_adj"] == 0.2
