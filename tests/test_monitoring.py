"""
Test suite for Telegram Alerts + Performance Analytics
=======================================================
1. Sends test Telegram messages (forces stdout fallback via env mocking)
2. Creates an in-memory SQLite database with 10 sample trades
3. Generates a performance report and pretty-prints it
4. Validates all report fields with assertions

Run with:
    python tests/test_monitoring.py        (integration smoke-test)
    pytest tests/test_monitoring.py -v     (unit tests via pytest)
"""

import asyncio
import sqlite3
import sys
import os
import time
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest

from monitoring.telegram_alerts import (
    send_alert,
    send_telegram_alert,
    trade_alert,
    error_alert,
    daily_report_alert,
)
from monitoring.analytics import (
    generate_performance_report,
    print_daily_summary,
)


# ---------------------------------------------------------------------------
# Helper: create in-memory DB with sample trades
# ---------------------------------------------------------------------------
SAMPLE_TRADES = [
    # (timestamp, symbol, side, price, quantity, reason, pnl, status)
    (1713500100, "BTC/USDT", "buy",  65000.0, 0.2050, "Bullish trend",        45.30,  "closed"),
    (1713500400, "BTC/USDT", "buy",  65200.0, 0.1800, "MACD crossover",       -22.10, "closed"),
    (1713500700, "BTC/USDT", "sell", 64800.0, 0.1950, "Bearish reversal",     38.75,  "closed"),
    (1713501000, "BTC/USDT", "buy",  65500.0, 0.2100, "Volume surge",         -15.40, "closed"),
    (1713501300, "BTC/USDT", "buy",  65300.0, 0.1900, "EMA bounce",           62.00,  "closed"),
    (1713501600, "BTC/USDT", "sell", 64600.0, 0.2000, "Breakdown signal",     -31.25, "closed"),
    (1713501900, "BTC/USDT", "buy",  65100.0, 0.2050, "RSI oversold bounce",  55.80,  "closed"),
    (1713502200, "BTC/USDT", "sell", 64900.0, 0.1850, "BB squeeze breakout",  -8.50,  "closed"),
    (1713502500, "BTC/USDT", "buy",  65400.0, 0.2200, "Strong momentum",      72.15,  "closed"),
    (1713502800, "BTC/USDT", "buy",  65600.0, 0.1750, "Trend continuation",   28.40,  "closed"),
]


def _create_mock_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with 10 sample trades."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE trades (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp INTEGER NOT NULL,
            symbol    TEXT    NOT NULL,
            side      TEXT    NOT NULL,
            price     REAL    NOT NULL,
            quantity  REAL    NOT NULL,
            reason    TEXT    DEFAULT '',
            pnl       REAL    DEFAULT 0.0,
            status    TEXT    DEFAULT 'open'
        )
    """)
    conn.executemany(
        """INSERT INTO trades (timestamp, symbol, side, price, quantity, reason, pnl, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        SAMPLE_TRADES,
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Env patch: force stdout fallback by clearing Telegram tokens
# ---------------------------------------------------------------------------
_NO_TELEGRAM = {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}


# ---------------------------------------------------------------------------
# PYTEST: Telegram alerts (stdout fallback — no real API calls)
# ---------------------------------------------------------------------------
class TestTelegramAlerts:
    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_send_alert_info(self, capsys):
        """send_alert with info type prints to stdout when no token set."""
        result = await send_alert("System started successfully", "info")
        assert result is True
        captured = capsys.readouterr()
        assert "INFO" in captured.out
        assert "System started successfully" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_send_alert_trade(self, capsys):
        """send_alert with trade type uses TRADE prefix."""
        result = await send_alert("BUY 0.2 BTC @ $65000", "trade")
        assert result is True
        captured = capsys.readouterr()
        assert "TRADE" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_send_alert_error(self, capsys):
        """send_alert with error type uses ERROR prefix."""
        result = await send_alert("Connection timeout", "error")
        assert result is True
        captured = capsys.readouterr()
        assert "ERROR" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_send_alert_daily(self, capsys):
        """send_alert with daily type uses DAILY REPORT prefix."""
        result = await send_alert("5 trades, +$127.50", "daily")
        assert result is True
        captured = capsys.readouterr()
        assert "DAILY REPORT" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_trade_alert_template(self, capsys):
        """trade_alert formats correctly."""
        result = await trade_alert(
            "BUY", "BTC/USDT", 0.205128, 65000.0, 64025.0, 66950.0,
            "Strong bullish trend"
        )
        assert result is True
        captured = capsys.readouterr()
        assert "BUY" in captured.out
        assert "65,000.00" in captured.out
        assert "Strong bullish trend" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_error_alert_template(self, capsys):
        """error_alert formats correctly."""
        result = await error_alert("API rate limit exceeded")
        assert result is True
        captured = capsys.readouterr()
        assert "Bot encountered:" in captured.out
        assert "API rate limit exceeded" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_daily_report_alert_template(self, capsys):
        """daily_report_alert formats correctly."""
        result = await daily_report_alert(5, 127.50, 0.60, 10127.50)
        assert result is True
        captured = capsys.readouterr()
        assert "Trades: 5" in captured.out
        assert "60.0%" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_backward_compat_alias(self, capsys):
        """send_telegram_alert still works as an alias."""
        result = await send_telegram_alert("Legacy message test")
        assert result is True
        captured = capsys.readouterr()
        assert "Legacy message test" in captured.out

    @pytest.mark.asyncio
    @patch.dict(os.environ, _NO_TELEGRAM)
    async def test_unknown_alert_type_defaults_to_info(self, capsys):
        """Unknown alert_type falls back to INFO prefix."""
        result = await send_alert("Test unknown type", "unknown_type")
        assert result is True
        captured = capsys.readouterr()
        assert "INFO" in captured.out


# ---------------------------------------------------------------------------
# PYTEST: Performance analytics
# ---------------------------------------------------------------------------
class TestAnalytics:
    def test_report_total_trades(self):
        """Should count all 10 trades."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["total_trades"] == 10
        db.close()

    def test_report_win_loss_split(self):
        """6 winning, 4 losing trades in sample data."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["winning_trades"] == 6
        assert report["losing_trades"] == 4
        db.close()

    def test_report_win_rate(self):
        """Win rate = 6/10 = 0.60."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["win_rate"] == 0.6
        db.close()

    def test_report_total_pnl(self):
        """Total P&L should match sum of sample PnLs."""
        expected_pnl = sum(t[6] for t in SAMPLE_TRADES)
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["total_pnl"] == round(expected_pnl, 2)
        db.close()

    def test_report_avg_win(self):
        """Average win = sum(wins) / count(wins)."""
        wins = [t[6] for t in SAMPLE_TRADES if t[6] > 0]
        expected = round(sum(wins) / len(wins), 2)
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["avg_win"] == expected
        db.close()

    def test_report_avg_loss(self):
        """Average loss = abs(sum(losses) / count(losses))."""
        losses = [t[6] for t in SAMPLE_TRADES if t[6] < 0]
        expected = round(abs(sum(losses) / len(losses)), 2)
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["avg_loss"] == expected
        db.close()

    def test_report_profit_factor(self):
        """Profit factor = gross_wins / gross_losses."""
        wins = [t[6] for t in SAMPLE_TRADES if t[6] > 0]
        losses = [t[6] for t in SAMPLE_TRADES if t[6] < 0]
        expected = round(sum(wins) / abs(sum(losses)), 4)
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["profit_factor"] == expected
        db.close()

    def test_report_max_drawdown_positive(self):
        """Max drawdown should be >= 0."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert report["max_drawdown"] >= 0
        db.close()

    def test_report_sharpe_is_number(self):
        """Sharpe estimate should be a finite number."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        assert isinstance(report["sharpe_estimate"], float)
        assert not (report["sharpe_estimate"] != report["sharpe_estimate"])  # not NaN
        db.close()

    def test_empty_database(self):
        """Empty trades table should return a zeroed report."""
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER, symbol TEXT, side TEXT,
                price REAL, quantity REAL, reason TEXT,
                pnl REAL DEFAULT 0.0, status TEXT
            )
        """)
        report = generate_performance_report(conn)
        assert report["total_trades"] == 0
        assert report["win_rate"] == 0.0
        assert report["total_pnl"] == 0.0
        conn.close()

    def test_print_daily_summary_runs(self, capsys):
        """print_daily_summary should output to stdout without error."""
        db = _create_mock_db()
        report = generate_performance_report(db)
        print_daily_summary(report)
        captured = capsys.readouterr()
        assert "PERFORMANCE REPORT" in captured.out
        assert "Win Rate" in captured.out
        assert "Profit Factor" in captured.out
        db.close()


# ---------------------------------------------------------------------------
# Smoke test (run directly: python tests/test_monitoring.py)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Force stdout fallback for standalone run
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["TELEGRAM_CHAT_ID"] = ""

    print("=" * 60)
    print("  MONITORING MODULES -- INTEGRATION TEST")
    print("=" * 60)

    # -- 1. Telegram alerts (stdout mode) --
    print("\n--- Telegram Alert Tests ---\n")

    async def _run_telegram_tests():
        await send_alert("Bot started successfully", "info")
        await trade_alert("BUY", "BTC/USDT", 0.205128, 65000.0, 64025.0, 66950.0, "Strong bullish trend")
        await error_alert("Connection timeout after 10s")
        await daily_report_alert(5, 127.50, 0.60, 10127.50)
        await send_telegram_alert("Backward-compatible alias test")

    asyncio.run(_run_telegram_tests())

    # -- 2. Analytics report --
    print("\n--- Analytics Report Test ---\n")
    db = _create_mock_db()
    report = generate_performance_report(db)
    print_daily_summary(report)

    # -- Assertions --
    assert report["total_trades"] == 10
    assert report["winning_trades"] == 6
    assert report["losing_trades"] == 4
    assert 0.0 <= report["win_rate"] <= 1.0
    assert report["profit_factor"] > 0
    assert report["max_drawdown"] >= 0
    print("[PASS] All assertions passed")

    db.close()
    print("\n" + "=" * 60)
    print("  ALL MONITORING TESTS PASSED")
    print("=" * 60)
