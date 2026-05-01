"""
Orders Router — Trade history and summary endpoints.
"""

from fastapi import APIRouter, Query
from backend.services.db import fetch_all, fetch_one, fetch_scalar

router = APIRouter(tags=["orders"])


@router.get("/orders")
async def get_orders(
    limit: int = Query(50, ge=1, le=500),
    page: int = Query(1, ge=1),
    side: str | None = None,
    symbol: str | None = None,
):
    """Paginated trade history with optional side/symbol filters."""
    offset = (page - 1) * limit
    where_clauses = []
    params = []

    if side:
        where_clauses.append("side = ?")
        params.append(side.lower())
    if symbol:
        where_clauses.append("symbol = ?")
        params.append(symbol)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    total = fetch_scalar(
        f"SELECT COUNT(*) FROM trades {where_sql}", tuple(params)
    ) or 0

    trades = fetch_all(
        f"SELECT * FROM trades {where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )

    return {
        "trades": trades,
        "total": total,
        "page": page,
        "pages": max(1, (total + limit - 1) // limit),
    }


@router.get("/orders/summary")
async def get_order_summary():
    """Aggregated performance summary computed from all trades."""
    trades = fetch_all(
        "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
    )

    if not trades:
        return {
            "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
            "win_rate": 0.0, "total_pnl": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0, "best_trade": 0.0,
            "worst_trade": 0.0, "today_pnl": 0.0, "today_trades": 0,
        }

    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    # Max drawdown from cumulative P&L
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cumulative += pnl
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    # Today's P&L
    today_pnl = fetch_scalar(
        "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE date(timestamp/1000, 'unixepoch') = date('now')"
    ) or 0.0
    today_trades = fetch_scalar(
        "SELECT COUNT(*) FROM trades WHERE date(timestamp/1000, 'unixepoch') = date('now')"
    ) or 0

    return {
        "total_trades": len(trades),
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
        "total_pnl": round(sum(pnls), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0.0,
        "max_drawdown": round(max_dd, 2),
        "best_trade": round(max(pnls), 2) if pnls else 0.0,
        "worst_trade": round(min(pnls), 2) if pnls else 0.0,
        "today_pnl": round(today_pnl, 2),
        "today_trades": today_trades,
    }


@router.get("/orders/{trade_id}")
async def get_trade(trade_id: int):
    """Fetch a single trade by ID."""
    trade = fetch_one("SELECT * FROM trades WHERE id = ?", (trade_id,))
    if not trade:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade
