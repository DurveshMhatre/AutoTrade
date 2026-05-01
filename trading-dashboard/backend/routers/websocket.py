"""
WebSocket Router — Real-time data feed
========================================
Pushes live BTC price, new trade events, agent decisions,
and bot status to connected dashboard clients.

Message types:
  snapshot      — full dashboard state on connect
  price         — BTC/USDT price update every 5s
  new_trade     — when bot places a new order
  new_decision  — when orchestrator makes a decision
  bot_status    — PM2 process status check
  ping          — keepalive every 30s
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.services.ws_manager import manager
from backend.services.db import fetch_all, fetch_scalar

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────

async def _fetch_btc_price() -> float | None:
    """Fetch current BTC/USDT price from Binance public API."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": "BTCUSDT"},
            )
            if resp.status_code == 200:
                return float(resp.json().get("price", 0))
    except Exception:
        pass
    return None


def _get_bot_status() -> str:
    """Check if the trading bot PM2 process is running.

    Tries to read PM2 process list. Falls back to 'unknown' if
    PM2 is not available (e.g. running locally on Windows).
    """
    try:
        import subprocess
        result = subprocess.run(
            ["pm2", "jlist"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            processes = json.loads(result.stdout)
            for proc in processes:
                name = proc.get("name", "").lower()
                if "trading" in name and "dashboard" not in name:
                    status = proc.get("pm2_env", {}).get("status", "unknown")
                    if status == "online":
                        return "running"
                    elif status == "stopped":
                        return "paused"
                    else:
                        return "error"
            return "unknown"
    except Exception:
        return "unknown"


def _build_snapshot() -> dict:
    """Build a full dashboard snapshot for initial WebSocket connection."""
    from backend.services.analytics import compute_summary

    trades = fetch_all("SELECT * FROM trades ORDER BY timestamp ASC")
    decisions = fetch_all(
        "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 10"
    )

    summary = compute_summary(trades)
    bot_status = _get_bot_status()

    return {
        "summary": summary,
        "recent_trades": trades[-10:] if trades else [],
        "recent_decisions": [
            {
                "id": d.get("id", 0),
                "timestamp": str(d.get("timestamp", "")),
                "signal": d.get("signal", "HOLD"),
                "confidence": d.get("confidence", 0.0),
                "reason": d.get("reason", ""),
                "approved": bool(d.get("approved", 0)),
            }
            for d in decisions
        ],
        "bot_status": bot_status,
        "current_btc_price": None,  # Will be populated by price broadcaster
    }


# ── Background Tasks ────────────────────────────────────────────────

async def _price_broadcaster():
    """Broadcast BTC price to all connected clients every 5 seconds."""
    while True:
        try:
            price = await _fetch_btc_price()
            if price and manager.active_connections:
                await manager.broadcast({
                    "type": "price",
                    "symbol": "BTC/USDT",
                    "price": price,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            logger.debug("Price broadcaster error: %s", exc)

        await asyncio.sleep(5)


async def _trade_watcher(last_trade_id: dict):
    """Watch for new trades in the DB and broadcast them."""
    while True:
        try:
            if manager.active_connections:
                latest = fetch_scalar(
                    "SELECT MAX(id) FROM trades"
                )
                if latest and latest > last_trade_id.get("id", 0):
                    # Fetch new trades
                    new_trades = fetch_all(
                        "SELECT * FROM trades WHERE id > ? ORDER BY id ASC",
                        (last_trade_id["id"],),
                    )
                    for trade in new_trades:
                        await manager.broadcast({
                            "type": "new_trade",
                            "trade": trade,
                        })
                    last_trade_id["id"] = latest

                # Also check for new decisions
                latest_dec = fetch_scalar(
                    "SELECT MAX(id) FROM agent_decisions"
                )
                if latest_dec and latest_dec > last_trade_id.get("dec_id", 0):
                    new_decisions = fetch_all(
                        "SELECT * FROM agent_decisions WHERE id > ? ORDER BY id ASC",
                        (last_trade_id["dec_id"],),
                    )
                    for dec in new_decisions:
                        await manager.broadcast({
                            "type": "new_decision",
                            "decision": {
                                "id": dec.get("id", 0),
                                "timestamp": str(dec.get("timestamp", "")),
                                "signal": dec.get("signal", "HOLD"),
                                "confidence": dec.get("confidence", 0.0),
                                "reason": dec.get("reason", ""),
                                "approved": bool(dec.get("approved", 0)),
                            },
                        })
                    last_trade_id["dec_id"] = latest_dec

        except Exception as exc:
            logger.debug("Trade watcher error: %s", exc)

        await asyncio.sleep(10)


async def _bot_status_broadcaster():
    """Broadcast bot PM2 status every 30 seconds."""
    while True:
        try:
            if manager.active_connections:
                status = _get_bot_status()
                await manager.broadcast({
                    "type": "bot_status",
                    "status": status,
                    "last_seen": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as exc:
            logger.debug("Bot status broadcaster error: %s", exc)

        await asyncio.sleep(30)


async def _ping_keepalive():
    """Send ping to keep WebSocket connections alive."""
    while True:
        try:
            if manager.active_connections:
                await manager.broadcast({"type": "ping"})
        except Exception:
            pass
        await asyncio.sleep(30)


# ── WebSocket Endpoint ──────────────────────────────────────────────

@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint for real-time dashboard data."""
    await manager.connect(websocket)

    # Initialize tracking state
    last_ids = {
        "id": fetch_scalar("SELECT MAX(id) FROM trades") or 0,
        "dec_id": fetch_scalar("SELECT MAX(id) FROM agent_decisions") or 0,
    }

    # Send initial snapshot
    try:
        snapshot = _build_snapshot()
        price = await _fetch_btc_price()
        snapshot["current_btc_price"] = price
        await manager.send_to(websocket, {
            "type": "snapshot",
            "data": snapshot,
        })
    except Exception as exc:
        logger.error("Failed to send snapshot: %s", exc)

    # Start background tasks
    tasks = [
        asyncio.create_task(_price_broadcaster()),
        asyncio.create_task(_trade_watcher(last_ids)),
        asyncio.create_task(_bot_status_broadcaster()),
        asyncio.create_task(_ping_keepalive()),
    ]

    try:
        # Keep connection alive — listen for client messages
        while True:
            try:
                data = await websocket.receive_text()
                # Client can send commands in the future
                logger.debug("Received from client: %s", data)
            except (WebSocketDisconnect, RuntimeError):
                break
            except Exception:
                break
    finally:
        # Cleanup
        for task in tasks:
            task.cancel()
        manager.disconnect(websocket)
