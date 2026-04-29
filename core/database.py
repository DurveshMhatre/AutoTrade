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
    status      TEXT    DEFAULT 'open'
);

CREATE TABLE IF NOT EXISTS agent_decisions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,
    signal      TEXT    NOT NULL,    -- 'long' | 'short' | 'hold'
    confidence  REAL    NOT NULL,
    reason      TEXT    DEFAULT '',
    approved    INTEGER DEFAULT 0   -- 0 = pending, 1 = approved
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
    conn.execute(
        """
        INSERT INTO trades (timestamp, symbol, side, price, quantity, reason, pnl, status)
        VALUES (:timestamp, :symbol, :side, :price, :quantity, :reason, :pnl, :status)
        """,
        trade,
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
