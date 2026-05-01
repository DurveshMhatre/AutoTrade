"""
Tests for the Analytics Computation Service
=============================================
Validates all pure-computation analytics functions
using mock trade data.
"""

import pytest
from datetime import datetime, timezone, timedelta

from backend.services.analytics import (
    compute_summary,
    compute_max_drawdown,
    compute_equity_curve,
    compute_sharpe_ratio,
    compute_hourly_breakdown,
    compute_win_streak,
    compute_pnl_distribution,
)


# ── Fixtures ─────────────────────────────────────────────────────────

def _make_trade(pnl: float, hours_ago: int = 0) -> dict:
    """Create a mock trade dict with a given P&L and time offset."""
    ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return {
        "id": 0,
        "timestamp": int(ts.timestamp() * 1000),
        "symbol": "BTC/USDT",
        "side": "buy",
        "price": 70000.0,
        "quantity": 0.001,
        "reason": "test",
        "pnl": pnl,
        "status": "filled",
    }


@pytest.fixture
def sample_trades():
    """10 trades: 7 wins, 3 losses — realistic mix."""
    trades = [
        _make_trade(15.50, hours_ago=240),    # Win
        _make_trade(-8.20, hours_ago=216),     # Loss
        _make_trade(22.30, hours_ago=192),     # Win
        _make_trade(7.10, hours_ago=168),      # Win
        _make_trade(-12.40, hours_ago=144),    # Loss
        _make_trade(18.90, hours_ago=120),     # Win
        _make_trade(5.60, hours_ago=96),       # Win
        _make_trade(-6.50, hours_ago=72),      # Loss
        _make_trade(11.80, hours_ago=48),      # Win
        _make_trade(9.40, hours_ago=24),       # Win
    ]
    return trades


# ── Tests ────────────────────────────────────────────────────────────

def test_empty_trades_returns_zeros():
    """Empty trade list should return all-zero summary."""
    result = compute_summary([])
    assert result["total_trades"] == 0
    assert result["win_rate"] == 0.0
    assert result["total_pnl"] == 0.0
    assert result["max_drawdown"] == 0.0


def test_win_rate_calculation(sample_trades):
    """With 7 wins out of 10 trades, win rate should be 70%."""
    result = compute_summary(sample_trades)
    assert result["total_trades"] == 10
    assert result["winning_trades"] == 7
    assert result["losing_trades"] == 3
    assert result["win_rate"] == 70.0


def test_pnl_calculation(sample_trades):
    """Total P&L should match sum of individual trade P&Ls."""
    result = compute_summary(sample_trades)
    expected = 15.50 - 8.20 + 22.30 + 7.10 - 12.40 + 18.90 + 5.60 - 6.50 + 11.80 + 9.40
    assert abs(result["total_pnl"] - round(expected, 2)) < 0.01


def test_profit_factor(sample_trades):
    """Profit factor = gross_profit / gross_loss."""
    result = compute_summary(sample_trades)
    gross_profit = 15.50 + 22.30 + 7.10 + 18.90 + 5.60 + 11.80 + 9.40
    gross_loss = abs(-8.20 + -12.40 + -6.50)
    expected_pf = round(gross_profit / gross_loss, 2)
    assert result["profit_factor"] == expected_pf


def test_max_drawdown_calculation(sample_trades):
    """Max drawdown should be computed from peak-to-trough equity."""
    result = compute_max_drawdown(sample_trades)
    assert result["max_drawdown_dollars"] >= 0
    assert result["max_drawdown_pct"] >= 0
    # With our data, there should be some drawdown
    assert isinstance(result["max_drawdown_dollars"], float)


def test_max_drawdown_empty():
    """Empty trades should return zero drawdown."""
    result = compute_max_drawdown([])
    assert result["max_drawdown_dollars"] == 0.0
    assert result["max_drawdown_pct"] == 0.0


def test_equity_curve_fills_gaps(sample_trades):
    """Equity curve should fill in days that have no trades."""
    curve = compute_equity_curve(sample_trades)
    assert len(curve) > 0

    # Check all entries have required keys
    for point in curve:
        assert "date" in point
        assert "equity" in point
        assert "daily_pnl" in point
        assert "cumulative_pnl" in point

    # First point equity should be near starting + first day's pnl
    assert curve[0]["equity"] > 0

    # Days should be consecutive (no gaps)
    for i in range(1, len(curve)):
        prev = datetime.strptime(curve[i - 1]["date"], "%Y-%m-%d")
        curr = datetime.strptime(curve[i]["date"], "%Y-%m-%d")
        assert (curr - prev).days == 1, f"Gap between {curve[i-1]['date']} and {curve[i]['date']}"


def test_equity_curve_empty():
    """Empty trades should return empty curve."""
    assert compute_equity_curve([]) == []


def test_sharpe_with_mock_data(sample_trades):
    """Sharpe ratio should return a finite number for sufficient data."""
    sharpe = compute_sharpe_ratio(sample_trades)
    # With 10 trades over 10 days, this meets the minimum
    assert isinstance(sharpe, float)
    # Sharpe can be positive or negative, but should not be NaN
    assert not (sharpe != sharpe)  # NaN check


def test_sharpe_insufficient_data():
    """Sharpe should return 0.0 with fewer than 10 data points."""
    few_trades = [_make_trade(5.0, hours_ago=i * 24) for i in range(3)]
    assert compute_sharpe_ratio(few_trades) == 0.0


def test_hourly_breakdown_all_24_hours(sample_trades):
    """Hourly breakdown should return exactly 24 entries."""
    result = compute_hourly_breakdown(sample_trades)
    assert len(result) == 24

    # All hours 0-23 should be present
    hours = [r["hour"] for r in result]
    assert hours == list(range(24))

    # Each entry should have required keys
    for entry in result:
        assert "hour" in entry
        assert "avg_pnl" in entry
        assert "trade_count" in entry
        assert "win_rate" in entry


def test_hourly_breakdown_empty():
    """Empty trades should return all zeros for 24 hours."""
    result = compute_hourly_breakdown([])
    assert len(result) == 24
    assert all(r["trade_count"] == 0 for r in result)


def test_win_streak(sample_trades):
    """Win streak computation should track consecutive wins/losses."""
    result = compute_win_streak(sample_trades)
    assert "current_streak" in result
    assert "current_type" in result
    assert "max_win_streak" in result
    assert "max_loss_streak" in result
    assert result["current_type"] in ("win", "loss", "none")
    assert result["max_win_streak"] >= 0
    assert result["max_loss_streak"] >= 0


def test_win_streak_empty():
    """Empty trades should return zero streaks."""
    result = compute_win_streak([])
    assert result["current_streak"] == 0
    assert result["max_win_streak"] == 0


def test_win_streak_all_wins():
    """All winning trades should show max streak = total."""
    trades = [_make_trade(10.0, hours_ago=i) for i in range(5)]
    result = compute_win_streak(trades)
    assert result["max_win_streak"] == 5
    assert result["current_streak"] == 5
    assert result["current_type"] == "win"


def test_pnl_distribution(sample_trades):
    """P&L distribution should return non-empty buckets."""
    result = compute_pnl_distribution(sample_trades, bucket_size=5.0)
    assert len(result) > 0

    total_count = sum(b["count"] for b in result)
    assert total_count == len(sample_trades)

    for bucket in result:
        assert "bucket_start" in bucket
        assert "bucket_end" in bucket
        assert "count" in bucket
        assert "is_positive" in bucket


def test_pnl_distribution_empty():
    """Empty trades should return empty distribution."""
    assert compute_pnl_distribution([]) == []
