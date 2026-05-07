"""
core/database.py
────────────────
SQLite persistence layer for the Automated Trading Bot.

Tables
------
candles          – historical OHLCV data
trades           – executed trade records with P&L
agent_decisions  – AI agent signal log
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "trading_bot.db"


# ─── Schema ─────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    symbol      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    symbol      TEXT    NOT NULL,
    side        TEXT    NOT NULL,    -- 'buy' | 'sell'
    price       REAL    NOT NULL,
    quantity    REAL    NOT NULL,
    reason      TEXT    DEFAULT '',
    pnl         REAL    DEFAULT 0.0,
    status      TEXT    DEFAULT 'open',
    stop_loss   REAL    DEFAULT 0.0,
    take_profit REAL    DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    signal      TEXT    NOT NULL,    -- 'long' | 'short' | 'hold'
    confidence  REAL    NOT NULL,
    reason      TEXT    DEFAULT '',
    approved    INTEGER DEFAULT 0   -- 0 = pending, 1 = approved
);

CREATE TABLE IF NOT EXISTS regime_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp        INTEGER NOT NULL,
    regime           TEXT    NOT NULL,
    confidence       REAL    NOT NULL,
    strategy_bias    TEXT    DEFAULT '',
    adx              REAL    DEFAULT 0.0,
    confluence_score INTEGER DEFAULT 0,
    sentiment_score  INTEGER DEFAULT 0,
    fear_greed       INTEGER DEFAULT 50,
    funding_signal   TEXT    DEFAULT 'neutral'
);
"""


# ─── Init ────────────────────────────────────────────────────────────

def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """
    Create (or open) the SQLite database and ensure all tables exist.

    Returns the open ``sqlite3.Connection``.
    """
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # dict-like access
    conn.executescript(_SCHEMA)
    
    # Simple migration: add stop_loss and take_profit if they don't exist
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL DEFAULT 0.0;")
        conn.execute("ALTER TABLE trades ADD COLUMN take_profit REAL DEFAULT 0.0;")
    except sqlite3.OperationalError:
        pass  # Columns already exist

    conn.commit()
    print(f"[database] initialised at {path}")
    return conn


# ─── CRUD helpers ────────────────────────────────────────────────────

def save_candle(conn: sqlite3.Connection, candle: dict) -> None:
    """Insert a single candle dict into the *candles* table."""
    conn.execute(
        """
        INSERT INTO candles (timestamp, open, high, low, close, volume, symbol)
        VALUES (:timestamp, :open, :high, :low, :close, :volume, :symbol)
        """,
        candle,
    )
    conn.commit()


def save_trade(conn: sqlite3.Connection, trade: dict) -> None:
    """Insert a single trade dict into the *trades* table."""
    # Handle missing keys for backward compatibility
    trade_data = dict(trade)
    if "stop_loss" not in trade_data:
        trade_data["stop_loss"] = 0.0
    if "take_profit" not in trade_data:
        trade_data["take_profit"] = 0.0

    conn.execute(
        """
        INSERT INTO trades (timestamp, symbol, side, price, quantity, reason, pnl, status, stop_loss, take_profit)
        VALUES (:timestamp, :symbol, :side, :price, :quantity, :reason, :pnl, :status, :stop_loss, :take_profit)
        """,
        trade_data,
    )
    conn.commit()


def save_decision(conn: sqlite3.Connection, decision: dict) -> None:
    """Insert a single decision dict into the *agent_decisions* table."""
    conn.execute(
        """
        INSERT INTO agent_decisions (timestamp, signal, confidence, reason, approved)
        VALUES (:timestamp, :signal, :confidence, :reason, :approved)
        """,
        decision,
    )
    conn.commit()


def get_recent_trades(
    conn: sqlite3.Connection,
    limit: int = 50,
) -> list[dict]:
    """Return the most recent *limit* trades as a list of dicts."""
    cursor = conn.execute(
        "SELECT * FROM trades ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def get_open_trades(conn: sqlite3.Connection) -> list[dict]:
    """Return all trades that are currently open."""
    cursor = conn.execute("SELECT * FROM trades WHERE status = 'open' ORDER BY id ASC")
    return [dict(row) for row in cursor.fetchall()]


def update_trade_status(
    conn: sqlite3.Connection, trade_id: int, status: str, close_price: float, pnl: float
) -> None:
    """Mark a trade as closed and record its PnL."""
    conn.execute(
        """
        UPDATE trades 
        SET status = ?, pnl = ? 
        WHERE id = ?
        """,
        (status, pnl, trade_id)
    )
    conn.commit()


def save_regime(conn: sqlite3.Connection, regime_data: dict) -> None:
    """Insert a regime classification snapshot into *regime_history*."""
    conn.execute(
        """
        INSERT INTO regime_history
            (timestamp, regime, confidence, strategy_bias, adx,
             confluence_score, sentiment_score, fear_greed, funding_signal)
        VALUES
            (:timestamp, :regime, :confidence, :strategy_bias, :adx,
             :confluence_score, :sentiment_score, :fear_greed, :funding_signal)
        """,
        regime_data,
    )
    conn.commit()

