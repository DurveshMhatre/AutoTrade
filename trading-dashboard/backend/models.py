"""
Pydantic Response Models
=========================
All models match the trading bot's SQLite schema.
Used for API response serialisation and validation.
"""

from __future__ import annotations

from pydantic import BaseModel


class Trade(BaseModel):
    """A single executed trade record."""
    id: int
    timestamp: str
    symbol: str
    side: str           # "buy" or "sell"
    price: float
    quantity: float
    reason: str
    pnl: float | None
    status: str         # "filled", "cancelled", "open"


class OrderSummary(BaseModel):
    """Aggregated trading performance summary."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    max_drawdown: float
    best_trade: float
    worst_trade: float
    today_pnl: float
    today_trades: int


class Position(BaseModel):
    """An open/active trading position."""
    symbol: str
    side: str
    entry_price: float
    current_price: float
    quantity: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    entry_time: str
    stop_loss: float | None
    take_profit: float | None


class AgentDecision(BaseModel):
    """A logged AI agent decision."""
    id: int
    timestamp: str
    signal: str         # "BUY", "SELL", "HOLD"
    confidence: float
    reason: str
    approved: bool
    market_data: dict | None = None


class PerformancePoint(BaseModel):
    """A single point on the equity curve."""
    timestamp: str
    equity: float
    pnl_cumulative: float


class DashboardMetrics(BaseModel):
    """Complete dashboard snapshot — used by the main overview page."""
    summary: OrderSummary
    recent_trades: list[Trade]
    open_positions: list[Position]
    equity_curve: list[PerformancePoint]
    last_agent_decision: AgentDecision | None
    bot_status: str         # "running", "paused", "error"
    current_btc_price: float | None
