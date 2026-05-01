"""
Trading Dashboard API — FastAPI Application
=============================================
Serves REST API and WebSocket endpoints for the
trading dashboard frontend. Reads data from the
trading bot's SQLite database in read-only mode.

Start: uvicorn backend.main:app --host 0.0.0.0 --port 8001 --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.config import CORS_ORIGINS
from backend.routers import orders, performance, positions, agent
from backend.routers import websocket as ws_router

app = FastAPI(
    title="Trading Dashboard API",
    description="REST API for the BTC/USDT automated trading bot dashboard",
    version="1.0.0",
)

# ── CORS ────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ─────────────────────────────────────────────────────────
app.include_router(orders.router, prefix="/api")
app.include_router(performance.router, prefix="/api")
app.include_router(positions.router, prefix="/api")
app.include_router(agent.router, prefix="/api")
app.include_router(ws_router.router)  # WebSocket at /ws (no prefix)


# ── Health Check ────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    """Simple health check endpoint."""
    return {"status": "ok", "service": "trading-dashboard"}
