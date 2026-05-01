"""
Performance Router — Equity curve, metrics, hourly breakdown, Sharpe, streaks.
"""

from fastapi import APIRouter, Query
from backend.services.db import fetch_all, fetch_scalar
from backend.services.analytics import (
    compute_summary,
    compute_max_drawdown,
    compute_equity_curve,
    compute_sharpe_ratio,
    compute_hourly_breakdown,
    compute_win_streak,
    compute_pnl_distribution,
)

router = APIRouter(tags=["performance"])


@router.get("/performance/equity-curve")
async def get_equity_curve(days: int = Query(30, ge=1, le=365)):
    """Daily equity curve with cumulative P&L (computed via analytics service)."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
    )
    curve = compute_equity_curve(trades, starting_equity=1000.0)

    # Limit to the last N days
    return curve[-days:] if len(curve) > days else curve


@router.get("/performance/metrics")
async def get_dashboard_metrics():
    """Full dashboard metrics — single endpoint for the homepage."""
    from backend.routers.orders import get_order_summary
    from backend.routers.positions import get_open_positions, get_recent_decisions

    summary = await get_order_summary()
    equity_curve = await get_equity_curve(days=30)

    recent_trades = fetch_all(
        "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 10"
    )

    positions = await get_open_positions()
    decisions = await get_recent_decisions(limit=1)
    last_decision = decisions[0] if decisions else None

    # Try fetching live BTC price
    btc_price = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
            )
            if resp.status_code == 200:
                btc_price = float(resp.json().get("price", 0))
    except Exception:
        pass

    return {
        "summary": summary,
        "recent_trades": recent_trades,
        "open_positions": positions,
        "equity_curve": equity_curve,
        "last_agent_decision": last_decision,
        "bot_status": "running",
        "current_btc_price": btc_price,
    }


@router.get("/performance/by-hour")
async def get_hourly_breakdown():
    """Average P&L and trade count by hour of day (via analytics service)."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL"
    )
    return compute_hourly_breakdown(trades)


@router.get("/performance/win-rate-trend")
async def get_win_rate_trend():
    """Daily win rate for last 30 days."""
    rows = fetch_all(
        """
        SELECT date(timestamp/1000, 'unixepoch') as date,
               COUNT(*) as trades,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins
        FROM trades
        WHERE pnl IS NOT NULL
        GROUP BY date
        ORDER BY date DESC
        LIMIT 30
        """
    )

    return [
        {
            "date": r["date"],
            "win_rate": round(r["wins"] / r["trades"] * 100, 2) if r["trades"] > 0 else 0.0,
            "trades": r["trades"],
        }
        for r in rows
    ]


@router.get("/performance/sharpe")
async def get_sharpe_ratio():
    """Annualised Sharpe ratio from trade history."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
    )
    sharpe = compute_sharpe_ratio(trades)
    return {"sharpe_ratio": sharpe}


@router.get("/performance/drawdown")
async def get_max_drawdown():
    """Maximum drawdown in dollars and percentage."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
    )
    return compute_max_drawdown(trades)


@router.get("/performance/streaks")
async def get_win_streaks():
    """Current and maximum win/loss streaks."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
    )
    return compute_win_streak(trades)


@router.get("/performance/pnl-distribution")
async def get_pnl_distribution():
    """P&L distribution histogram in $5 buckets."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL"
    )
    return compute_pnl_distribution(trades, bucket_size=5.0)
