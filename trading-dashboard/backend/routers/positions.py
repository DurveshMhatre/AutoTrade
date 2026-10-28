"""
Positions Router — Open positions and agent decision log.
"""

from fastapi import APIRouter, Query
from backend.services.db import fetch_all

router = APIRouter(tags=["positions"])


@router.get("/positions/open")
async def get_open_positions():
    """Currently open trading positions with live unrealized P&L."""
    rows = fetch_all(
        "SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp DESC"
    )

    if not rows:
        return []

    prices = {}
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            for symbol in {row["symbol"] for row in rows}:
                resp = await client.get("https://api.binance.com/api/v3/ticker/price",
                                       params={"symbol":symbol.replace("/", "")})
                if resp.status_code == 200:
                    prices[symbol] = float(resp.json()["price"])
    except Exception:
        pass

    positions = []
    for row in rows:
        entry_price = row.get("price", 0) or 0
        qty = row.get("quantity", 0) or 0
        side = row.get("side", "buy")
        cp = prices.get(row["symbol"])
        stale = cp is None
        cp = cp if cp is not None else entry_price

        if side.lower() == "buy":
            unrealized = (cp - entry_price) * qty
        else:
            unrealized = (entry_price - cp) * qty

        unrealized_pct = (unrealized / (entry_price * qty) * 100) if (entry_price * qty) > 0 else 0.0

        positions.append({
            "symbol": row.get("symbol", "BTC/USDT"),
            "side": side,
            "entry_price": entry_price,
            "current_price": None if stale else cp,
            "price_unavailable": stale,
            "quantity": qty,
            "unrealized_pnl": None if stale else round(unrealized, 2),
            "unrealized_pnl_pct": None if stale else round(unrealized_pct, 2),
            "entry_time": str(row.get("timestamp", "")),
            "stop_loss": row.get("stop_loss"),
            "take_profit": row.get("take_profit"),
        })

    return positions


@router.get("/decisions/recent")
async def get_recent_decisions(limit: int = Query(20, ge=1, le=100)):
    """Recent AI agent decisions ordered by timestamp DESC."""
    rows = fetch_all(
        "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )

    decisions = []
    for row in rows:
        decisions.append({
            "id": row.get("id", 0),
            "timestamp": str(row.get("timestamp", "")),
            "signal": row.get("signal", "HOLD"),
            "confidence": row.get("confidence", 0.0),
            "reason": row.get("reason", ""),
            "approved": bool(row.get("approved", 0)),
            "market_data": None,
        })

    return decisions
