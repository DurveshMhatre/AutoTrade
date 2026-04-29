"""
monitoring/analytics.py
========================
Performance analytics module for the Automated Trading Bot.

Functions
---------
generate_performance_report(db_conn)  — query trades table, return stats dict
print_daily_summary(report)           — pretty-print the report to stdout
"""

import logging
import math
import sqlite3
from typing import Optional

logger = logging.getLogger(__name__)


def generate_performance_report(db_conn: sqlite3.Connection) -> dict:
    """Query the trades table and compute performance statistics.

    Parameters
    ----------
    db_conn : sqlite3.Connection
        Open database connection with a ``trades`` table containing
        columns: id, timestamp, symbol, side, price, quantity, reason,
        pnl, status.

    Returns
    -------
    dict
        Performance metrics including win_rate, profit_factor,
        max_drawdown, and sharpe_estimate.
    """
    try:
        cursor = db_conn.execute(
            "SELECT pnl FROM trades ORDER BY id ASC"
        )
        rows = cursor.fetchall()
    except sqlite3.OperationalError as exc:
        logger.error("Analytics query failed: %s", exc)
        return _empty_report()

    if not rows:
        return _empty_report()

    # ── Separate wins and losses ──────────────────────────────────────
    pnl_list: list[float] = []
    wins: list[float] = []
    losses: list[float] = []

    for row in rows:
        pnl = float(row[0]) if row[0] is not None else 0.0
        pnl_list.append(pnl)
        if pnl > 0:
            wins.append(pnl)
        elif pnl < 0:
            losses.append(pnl)

    total_trades = len(pnl_list)
    winning_trades = len(wins)
    losing_trades = len(losses)

    # ── Win rate ──────────────────────────────────────────────────────
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    # ── Total P&L ─────────────────────────────────────────────────────
    total_pnl = sum(pnl_list)

    # ── Average win / loss ────────────────────────────────────────────
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0

    # ── Profit factor (gross wins / gross losses) ─────────────────────
    gross_wins = sum(wins)
    gross_losses = abs(sum(losses))
    if gross_losses > 0:
        profit_factor = gross_wins / gross_losses
    else:
        profit_factor = float("inf") if gross_wins > 0 else 0.0

    # ── Max drawdown ──────────────────────────────────────────────────
    max_drawdown = _compute_max_drawdown(pnl_list)

    # ── Sharpe estimate (simplified: mean / stdev of returns) ─────────
    sharpe_estimate = _compute_sharpe(pnl_list)

    report = {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": round(win_rate, 4),
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else float("inf"),
        "max_drawdown": round(max_drawdown, 2),
        "sharpe_estimate": round(sharpe_estimate, 4),
    }

    logger.info("Performance report generated: %d trades", total_trades)
    return report


def print_daily_summary(report: dict) -> None:
    """Pretty-print a performance report to stdout.

    Parameters
    ----------
    report : dict
        Output from ``generate_performance_report``.
    """
    pnl = report.get("total_pnl", 0.0)
    pnl_sign = "+" if pnl >= 0 else ""
    pf = report.get("profit_factor", 0.0)
    pf_str = f"{pf:.2f}" if pf != float("inf") else "inf"

    print()
    print("=" * 50)
    print("       PERFORMANCE REPORT")
    print("=" * 50)
    print(f"  Total Trades    : {report.get('total_trades', 0)}")
    print(f"  Winning Trades  : {report.get('winning_trades', 0)}")
    print(f"  Losing Trades   : {report.get('losing_trades', 0)}")
    print(f"  Win Rate        : {report.get('win_rate', 0) * 100:.1f}%")
    print("-" * 50)
    print(f"  Total P&L       : ${pnl_sign}{pnl:,.2f}")
    print(f"  Avg Win         : ${report.get('avg_win', 0):,.2f}")
    print(f"  Avg Loss        : ${report.get('avg_loss', 0):,.2f}")
    print(f"  Profit Factor   : {pf_str}")
    print("-" * 50)
    print(f"  Max Drawdown    : ${report.get('max_drawdown', 0):,.2f}")
    print(f"  Sharpe Estimate : {report.get('sharpe_estimate', 0):.4f}")
    print("=" * 50)
    print()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _empty_report() -> dict:
    """Return a zeroed-out report for an empty trade set."""
    return {
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "win_rate": 0.0,
        "total_pnl": 0.0,
        "avg_win": 0.0,
        "avg_loss": 0.0,
        "profit_factor": 0.0,
        "max_drawdown": 0.0,
        "sharpe_estimate": 0.0,
    }


def _compute_max_drawdown(pnl_list: list[float]) -> float:
    """Compute the maximum peak-to-trough drawdown from a P&L series.

    Walks through cumulative P&L, tracking the running peak and the
    largest drop from any peak to a subsequent trough.
    """
    if not pnl_list:
        return 0.0

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0

    for pnl in pnl_list:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        drawdown = peak - cumulative
        if drawdown > max_dd:
            max_dd = drawdown

    return max_dd


def _compute_sharpe(pnl_list: list[float]) -> float:
    """Compute a simplified Sharpe-like ratio: mean(returns) / stdev(returns).

    This is an *estimate* — a proper Sharpe requires a risk-free rate and
    annualisation, but for intraday bot monitoring this gives a useful
    signal-to-noise measure.
    """
    if len(pnl_list) < 2:
        return 0.0

    n = len(pnl_list)
    mean = sum(pnl_list) / n
    variance = sum((x - mean) ** 2 for x in pnl_list) / (n - 1)  # sample variance
    stdev = math.sqrt(variance)

    if stdev == 0:
        return 0.0

    return mean / stdev
