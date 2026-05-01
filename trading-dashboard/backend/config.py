"""
Dashboard Backend Configuration
================================
Centralises all configuration: DB path, CORS, API keys.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Database ────────────────────────────────────────────────────────
# Points to the trading bot's SQLite database (read-only access)
DB_PATH = os.getenv(
    "DB_PATH",
    str(Path(__file__).resolve().parent.parent.parent / "trading_bot.db"),
)

# ── CORS ────────────────────────────────────────────────────────────
CORS_ORIGINS = [
    "http://localhost:5173",       # Vite dev server
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    os.getenv("DASHBOARD_ORIGIN", "http://localhost:5173"),
]

# ── API Keys ────────────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# ── Server ──────────────────────────────────────────────────────────
API_HOST = os.getenv("API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("API_PORT", "8001"))
