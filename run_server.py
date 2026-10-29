"""Unified Runner for Render: runs AutoTrade bot + FastAPI backend on a single port."""
import os
import sys
import threading
import subprocess
import uvicorn
from pathlib import Path

# Add trading-dashboard to sys.path so 'backend' package is importable
dashboard_dir = Path(__file__).resolve().parent / "trading-dashboard"
if str(dashboard_dir) not in sys.path:
    sys.path.insert(0, str(dashboard_dir))

def run_trading_bot():
    print("[Render] Starting AutoTrade Bot in background...", flush=True)
    subprocess.run([sys.executable, "main.py", "--paper"])

if __name__ == "__main__":
    # Start the bot worker in a daemon thread
    t = threading.Thread(target=run_trading_bot, daemon=True)
    t.start()

    # Start FastAPI backend on the port assigned by Render (default 10000)
    port = int(os.environ.get("PORT", 10000))
    print(f"[Render] Starting FastAPI Backend on port {port}...", flush=True)
    from backend.main import app
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

