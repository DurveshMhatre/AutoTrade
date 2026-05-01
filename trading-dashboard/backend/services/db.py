"""
Database Service — Read-Only SQLite Access
============================================
Connects to the trading bot's SQLite database in READ-ONLY mode.
The dashboard NEVER writes to the bot's database.

Uses PRAGMA query_only=ON as an extra safety layer.
"""

import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

from backend.config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Open a read-only connection to the bot's SQLite database.

    Returns
    -------
    sqlite3.Connection
        A connection with row_factory=sqlite3.Row and query_only=ON.
        Returns None if the database is unavailable.
    """
    try:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        return conn
    except sqlite3.Error as exc:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.error("[%s] DB connection error: %s", timestamp, exc)
        return None


@contextmanager
def get_db():
    """Context manager that yields a read-only DB connection.

    Usage::

        with get_db() as conn:
            if conn is None:
                return []  # DB unavailable
            cursor = conn.execute("SELECT * FROM trades")
            rows = [dict(r) for r in cursor.fetchall()]
    """
    conn = get_connection()
    try:
        yield conn
    finally:
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass


def fetch_all(query: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT query and return all rows as dicts.

    Never crashes — returns an empty list on any error.
    """
    with get_db() as conn:
        if conn is None:
            return []
        try:
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.Error as exc:
            logger.error("DB query error: %s | query: %s", exc, query[:100])
            return []


def fetch_one(query: str, params: tuple = ()) -> dict | None:
    """Execute a SELECT query and return a single row as dict, or None."""
    with get_db() as conn:
        if conn is None:
            return None
        try:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        except sqlite3.Error as exc:
            logger.error("DB query error: %s | query: %s", exc, query[:100])
            return None


def fetch_scalar(query: str, params: tuple = ()):
    """Execute a query and return the first column of the first row."""
    with get_db() as conn:
        if conn is None:
            return None
        try:
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            return row[0] if row else None
        except sqlite3.Error as exc:
            logger.error("DB scalar error: %s | query: %s", exc, query[:100])
            return None
