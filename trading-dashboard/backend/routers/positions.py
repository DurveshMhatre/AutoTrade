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

    # Fetch current BTC price for unrealized P&L calculation
    current_price = None
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
            )
            if resp.status_code == 200:
                current_price = float(resp.json().get("price", 0))
    except Exception:
        pass

    positions = []
    for row in rows:
        entry_price = row.get("price", 0) or 0
        qty = row.get("quantity", 0) or 0
        side = row.get("side", "buy")
        cp = current_price or entry_price

        if side.lower() == "buy":
            unrealized = (cp - entry_price) * qty
        else:
            unrealized = (entry_price - cp) * qty

        unrealized_pct = (unrealized / (entry_price * qty) * 100) if (entry_price * qty) > 0 else 0.0

        positions.append({
            "symbol": row.get("symbol", "BTC/USDT"),
            "side": side,
            "entry_price": entry_price,
            "current_price": cp,
            "quantity": qty,
            "unrealized_pnl": round(unrealized, 2),
            "unrealized_pnl_pct": round(unrealized_pct, 2),
            "entry_time": str(row.get("timestamp", "")),
            "stop_loss": None,    # Will be populated from trade metadata later
            "take_profit": None,
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
