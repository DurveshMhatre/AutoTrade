"""
Test suite for the Risk Management Module
==========================================
Tests cover all hard blocks and the approved-trade path with
correct position sizing and price level calculations.
"""

import pytest

from core.risk_manager import evaluate_trade


# ---------------------------------------------------------------------------
# Shared fixtures — realistic defaults
# ---------------------------------------------------------------------------
def _default_config():
    return {
        "MIN_CONFIDENCE": 0.60,
        "MAX_POSITIONS": 3,
        "DAILY_LOSS_LIMIT_PCT": 0.05,
        "RISK_PER_TRADE_PCT": 0.02,
        "STOP_LOSS_PCT": 0.015,
        "TAKE_PROFIT_PCT": 0.03,
    }


def _default_portfolio():
    return {
        "usdt_balance": 10_000.0,
        "open_positions": 1,
        "daily_loss_pct": 0.01,
    }


def _default_market_data():
    return {
        "close": 65_000.0,
        "volatility": "normal",
        "trend": "bullish",
        "rsi": 54.0,
    }


def _buy_signal(confidence=0.80):
    return {
        "signal": "BUY",
        "confidence": confidence,
        "reason": "Strong bullish trend",
        "key_indicators": {
            "trend": "bullish",
            "rsi": 54.0,
            "macd_hist": 20.0,
            "price_vs_bb_mid": "above",
        },
    }


def _sell_signal(confidence=0.80):
    return {
        "signal": "SELL",
        "confidence": confidence,
        "reason": "Bearish trend confirmed",
        "key_indicators": {
            "trend": "bearish",
            "rsi": 42.0,
            "macd_hist": -18.0,
            "price_vs_bb_mid": "below",
        },
    }


# ---------------------------------------------------------------------------
# HARD BLOCK TESTS
# ---------------------------------------------------------------------------
class TestHardBlocks:
    def test_blocks_hold_signal(self):
        """HOLD signal → rejected immediately."""
        signal = {"signal": "HOLD", "confidence": 0.90}
        result = evaluate_trade(
            signal, _default_portfolio(), _default_market_data(), _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "hold_signal"

    def test_blocks_low_confidence(self):
        """Confidence below MIN_CONFIDENCE → rejected."""
        signal = _buy_signal(confidence=0.45)
        result = evaluate_trade(
            signal, _default_portfolio(), _default_market_data(), _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "low_confidence"

    def test_blocks_low_confidence_boundary(self):
        """Confidence exactly at MIN_CONFIDENCE (0.60) → should pass this check."""
        signal = _buy_signal(confidence=0.60)
        result = evaluate_trade(
            signal, _default_portfolio(), _default_market_data(), _default_config()
        )
        # Should NOT be blocked by low_confidence (may be approved or blocked by another rule)
        assert result["reason"] != "low_confidence"

    def test_blocks_max_positions(self):
        """Portfolio at max positions → rejected."""
        portfolio = _default_portfolio()
        portfolio["open_positions"] = 3
        result = evaluate_trade(
            _buy_signal(), portfolio, _default_market_data(), _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "max_positions"

    def test_blocks_daily_loss_limit(self):
        """Daily loss limit reached → rejected."""
        portfolio = _default_portfolio()
        portfolio["daily_loss_pct"] = 0.06
        result = evaluate_trade(
            _buy_signal(), portfolio, _default_market_data(), _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "daily_loss_limit"

    def test_blocks_insufficient_balance(self):
        """Balance below $50 → rejected."""
        portfolio = _default_portfolio()
        portfolio["usdt_balance"] = 30.0
        result = evaluate_trade(
            _buy_signal(), portfolio, _default_market_data(), _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "insufficient_balance"

    def test_blocks_high_volatility_low_confidence(self):
        """High volatility + confidence < 0.75 → rejected."""
        market = _default_market_data()
        market["volatility"] = "high"
        signal = _buy_signal(confidence=0.70)
        result = evaluate_trade(
            signal, _default_portfolio(), market, _default_config()
        )
        assert result["approved"] is False
        assert result["reason"] == "high_volatility"

    def test_allows_high_volatility_with_high_confidence(self):
        """High volatility + confidence >= 0.75 → should pass volatility check."""
        market = _default_market_data()
        market["volatility"] = "high"
        signal = _buy_signal(confidence=0.80)
        result = evaluate_trade(
            signal, _default_portfolio(), market, _default_config()
        )
        # Should NOT be blocked by high_volatility
        assert result["reason"] != "high_volatility"


# ---------------------------------------------------------------------------
# APPROVED TRADE TESTS
# ---------------------------------------------------------------------------
class TestApprovedTrades:
    def test_approves_valid_buy_signal(self):
        """All conditions met for BUY → approved with correct fields."""
        result = evaluate_trade(
            _buy_signal(), _default_portfolio(), _default_market_data(), _default_config()
        )
        assert result["approved"] is True
        assert result["reason"] == "approved"
        assert result["quantity"] is not None
        assert result["quantity"] > 0
        assert result["stop_loss_price"] is not None
        assert result["take_profit_price"] is not None
        assert result["risk_dollars"] is not None

    def test_approves_valid_sell_signal(self):
        """All conditions met for SELL → approved with correct price levels."""
        result = evaluate_trade(
            _sell_signal(), _default_portfolio(), _default_market_data(), _default_config()
        )
        assert result["approved"] is True
        assert result["reason"] == "approved"
        # SELL: stop_loss ABOVE close, take_profit BELOW close
        close = _default_market_data()["close"]
        assert result["stop_loss_price"] > close
        assert result["take_profit_price"] < close

    def test_correct_position_size_calculation(self):
        """Verify the exact position sizing math."""
        config = _default_config()
        portfolio = _default_portfolio()
        market = _default_market_data()

        result = evaluate_trade(_buy_signal(), portfolio, market, config)
        assert result["approved"] is True

        # Manual calculation:
        # max_risk_dollars = 10_000 * 0.02 = 200.0
        # stop_loss_distance = 65_000 * 0.015 = 975.0
        # quantity = 200 / 975 = 0.205128...
        expected_quantity = round(200.0 / 975.0, 6)
        assert result["quantity"] == expected_quantity
        assert result["risk_dollars"] == 200.0

    def test_correct_buy_price_levels(self):
        """Verify BUY stop-loss and take-profit prices."""
        config = _default_config()
        market = _default_market_data()
        close = market["close"]  # 65_000.0

        result = evaluate_trade(
            _buy_signal(), _default_portfolio(), market, config
        )
        assert result["approved"] is True

        # BUY: SL below, TP above
        expected_sl = round(close * (1 - 0.015), 2)   # 65000 * 0.985 = 64025.0
        expected_tp = round(close * (1 + 0.03), 2)     # 65000 * 1.03  = 66950.0
        assert result["stop_loss_price"] == expected_sl
        assert result["take_profit_price"] == expected_tp

    def test_correct_sell_price_levels(self):
        """Verify SELL stop-loss and take-profit prices."""
        config = _default_config()
        market = _default_market_data()
        close = market["close"]  # 65_000.0

        result = evaluate_trade(
            _sell_signal(), _default_portfolio(), market, config
        )
        assert result["approved"] is True

        # SELL: SL above, TP below
        expected_sl = round(close * (1 + 0.015), 2)   # 65000 * 1.015 = 65975.0
        expected_tp = round(close * (1 - 0.03), 2)     # 65000 * 0.97  = 63050.0
        assert result["stop_loss_price"] == expected_sl
        assert result["take_profit_price"] == expected_tp

    def test_position_too_small(self):
        """Tiny balance → quantity below minimum → rejected."""
        portfolio = _default_portfolio()
        portfolio["usdt_balance"] = 55.0  # Just above the $50 min balance check
        result = evaluate_trade(
            _buy_signal(), portfolio, _default_market_data(), _default_config()
        )
        # max_risk = 55 * 0.02 = 1.10
        # sl_dist = 65000 * 0.015 = 975
        # qty = 1.10 / 975 = 0.001128... → rounds to 0.001128 → > 0.0001, so approved
        # Need an even smaller balance to trigger position_too_small
        portfolio["usdt_balance"] = 51.0  # 51 * 0.02 = 1.02 / 975 = 0.001046... still > 0.0001
        # Let's use a very low risk pct to force it
        config = _default_config()
        config["RISK_PER_TRADE_PCT"] = 0.0001  # 0.01%
        # max_risk = 51 * 0.0001 = 0.0051
        # qty = 0.0051 / 975 = 0.000005... < 0.0001
        result = evaluate_trade(_buy_signal(), portfolio, _default_market_data(), config)
        assert result["approved"] is False
        assert result["reason"] == "position_too_small"

    def test_null_fields_on_rejection(self):
        """All monetary fields must be None when trade is rejected."""
        signal = {"signal": "HOLD", "confidence": 0.90}
        result = evaluate_trade(
            signal, _default_portfolio(), _default_market_data(), _default_config()
        )
        assert result["quantity"] is None
        assert result["stop_loss_price"] is None
        assert result["take_profit_price"] is None
        assert result["risk_dollars"] is None
