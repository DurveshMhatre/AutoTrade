"""
Analytics Computation Service
==============================
Pure computation functions for trading performance metrics.
No DB calls inside this file — keeps it testable and decoupled.

All functions accept a list of trade dicts (from SQLite rows)
and return computed metrics.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from collections import defaultdict


def compute_summary(trades: list[dict]) -> dict:
    """Compute full OrderSummary fields from a list of trade dicts.

    Parameters
    ----------
    trades : list[dict]
        Each dict should have at least: pnl (float), timestamp (int).

    Returns
    -------
    dict
        All fields needed for the OrderSummary Pydantic model.
    """
    if not trades:
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
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "today_pnl": 0.0,
            "today_trades": 0,
        }

    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    total = len(trades)
    total_pnl = sum(pnls)
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    # Max drawdown
    max_dd = compute_max_drawdown(trades)["max_drawdown_dollars"]

    # Today's trades
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    today_ts = int(today_start.timestamp() * 1000)
    today_trades = [t for t in trades if (t.get("timestamp", 0) or 0) >= today_ts]
    today_pnl = sum(t.get("pnl", 0) or 0 for t in today_trades)

    return {
        "total_trades": total,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / total * 100, 2) if total else 0.0,
        "total_pnl": round(total_pnl, 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "max_drawdown": round(max_dd, 2),
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "today_pnl": round(today_pnl, 2),
        "today_trades": len(today_trades),
    }


def compute_max_drawdown(
    trades: list[dict], starting_equity: float = 1000.0
) -> dict:
    """Compute peak-to-trough maximum drawdown.

    Parameters
    ----------
    trades : list[dict]
        Sorted by timestamp ascending. Each must have ``pnl``.
    starting_equity : float
        Initial portfolio value.

    Returns
    -------
    dict
        max_drawdown_dollars, max_drawdown_pct
    """
    if not trades:
        return {"max_drawdown_dollars": 0.0, "max_drawdown_pct": 0.0}

    # Sort by timestamp to ensure correct order
    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0) or 0)

    equity = starting_equity
    peak = equity
    max_dd = 0.0

    for t in sorted_trades:
        pnl = t.get("pnl", 0) or 0
        equity += pnl
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    max_dd_pct = (max_dd / starting_equity * 100) if starting_equity > 0 else 0.0

    return {
        "max_drawdown_dollars": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
    }


def compute_equity_curve(
    trades: list[dict], starting_equity: float = 1000.0
) -> list[dict]:
    """Build a daily equity curve from trade history.

    Groups trades by day, computes cumulative equity,
    and fills in days with no trades (equity stays flat).

    Returns
    -------
    list[dict]
        [{date, equity, daily_pnl, cumulative_pnl}]
    """
    if not trades:
        return []

    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0) or 0)

    # Group P&L by day
    daily_pnl: dict[str, float] = defaultdict(float)
    for t in sorted_trades:
        ts = t.get("timestamp", 0) or 0
        if ts > 1e12:  # milliseconds
            ts = ts / 1000
        try:
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            continue
        daily_pnl[day] += t.get("pnl", 0) or 0

    if not daily_pnl:
        return []

    # Fill gaps between first and last day
    days = sorted(daily_pnl.keys())
    first_day = datetime.strptime(days[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    last_day = datetime.strptime(days[-1], "%Y-%m-%d").replace(tzinfo=timezone.utc)

    curve = []
    cumulative = 0.0
    current = first_day

    while current <= last_day:
        day_str = current.strftime("%Y-%m-%d")
        pnl = daily_pnl.get(day_str, 0.0)
        cumulative += pnl

        curve.append({
            "date": day_str,
            "equity": round(starting_equity + cumulative, 2),
            "daily_pnl": round(pnl, 2),
            "cumulative_pnl": round(cumulative, 2),
        })

        current += timedelta(days=1)

    return curve


def compute_sharpe_ratio(
    trades: list[dict], starting_equity: float = 1000.0
) -> float:
    """Compute annualised Sharpe ratio from trade history.

    Uses daily returns derived from the equity curve.
    Returns 0.0 if fewer than 10 trading days.

    Formula: (mean_daily_return / std_daily_return) * sqrt(252)
    """
    curve = compute_equity_curve(trades, starting_equity)
    if len(curve) < 10:
        return 0.0

    # Daily returns as fractions
    daily_returns = []
    for i in range(1, len(curve)):
        prev_eq = curve[i - 1]["equity"]
        curr_eq = curve[i]["equity"]
        if prev_eq > 0:
            daily_returns.append((curr_eq - prev_eq) / prev_eq)

    if not daily_returns:
        return 0.0

    mean_ret = sum(daily_returns) / len(daily_returns)
    variance = sum((r - mean_ret) ** 2 for r in daily_returns) / len(daily_returns)
    std_ret = math.sqrt(variance)

    if std_ret == 0:
        return 0.0

    sharpe = (mean_ret / std_ret) * math.sqrt(252)
    return round(sharpe, 4)


def compute_hourly_breakdown(trades: list[dict]) -> list[dict]:
    """Group trades by hour of day (UTC) and compute stats.

    Returns all 24 hours, even those with no trades.

    Returns
    -------
    list[dict]
        [{hour, avg_pnl, trade_count, win_rate}] for hours 0-23
    """
    hourly: dict[int, list[float]] = defaultdict(list)

    for t in trades:
        ts = t.get("timestamp", 0) or 0
        if ts > 1e12:
            ts = ts / 1000
        try:
            hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        except (OSError, ValueError):
            continue
        hourly[hour].append(t.get("pnl", 0) or 0)

    result = []
    for h in range(24):
        pnls = hourly.get(h, [])
        if pnls:
            wins = sum(1 for p in pnls if p > 0)
            result.append({
                "hour": h,
                "avg_pnl": round(sum(pnls) / len(pnls), 2),
                "trade_count": len(pnls),
                "win_rate": round(wins / len(pnls) * 100, 2),
            })
        else:
            result.append({
                "hour": h,
                "avg_pnl": 0.0,
                "trade_count": 0,
                "win_rate": 0.0,
            })

    return result


def compute_win_streak(trades: list[dict]) -> dict:
    """Compute current and maximum win/loss streaks.

    Returns
    -------
    dict
        current_streak, current_type ("win"/"loss"), max_win_streak, max_loss_streak
    """
    if not trades:
        return {
            "current_streak": 0,
            "current_type": "none",
            "max_win_streak": 0,
            "max_loss_streak": 0,
        }

    sorted_trades = sorted(trades, key=lambda t: t.get("timestamp", 0) or 0)

    max_win = 0
    max_loss = 0
    current = 0
    current_type = "none"

    win_run = 0
    loss_run = 0

    for t in sorted_trades:
        pnl = t.get("pnl", 0) or 0
        if pnl > 0:
            win_run += 1
            loss_run = 0
            if win_run > max_win:
                max_win = win_run
            current = win_run
            current_type = "win"
        elif pnl < 0:
            loss_run += 1
            win_run = 0
            if loss_run > max_loss:
                max_loss = loss_run
            current = loss_run
            current_type = "loss"
        else:
            # Zero P&L — reset both
            win_run = 0
            loss_run = 0
            current = 0
            current_type = "none"

    return {
        "current_streak": current,
        "current_type": current_type,
        "max_win_streak": max_win,
        "max_loss_streak": max_loss,
    }


def compute_pnl_distribution(trades: list[dict], bucket_size: float = 5.0) -> list[dict]:
    """Create a P&L distribution histogram.

    Groups trade P&L values into buckets of ``bucket_size`` dollars.

    Returns
    -------
    list[dict]
        [{bucket_start, bucket_end, count, is_positive}]
    """
    if not trades:
        return []

    pnls = [t.get("pnl", 0) or 0 for t in trades]
    min_pnl = min(pnls)
    max_pnl = max(pnls)

    # Compute bucket boundaries
    start = math.floor(min_pnl / bucket_size) * bucket_size
    end = math.ceil(max_pnl / bucket_size) * bucket_size

    buckets = []
    b = start
    while b < end:
        count = sum(1 for p in pnls if b <= p < b + bucket_size)
        buckets.append({
            "bucket_start": round(b, 2),
            "bucket_end": round(b + bucket_size, 2),
            "count": count,
            "is_positive": b >= 0,
        })
        b += bucket_size

    return buckets
