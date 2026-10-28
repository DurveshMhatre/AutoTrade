"""Hugging Face Gradio App & Dashboard for AutoTrade Bot.
Runs the trading bot in the background and serves a live web UI on port 7860.
"""
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
import gradio as gr

# Database path for paper trading
DB_PATH = Path(__file__).resolve().parent / "trading_bot_paper.db"

# Background trading bot runner
_bot_process = None

def start_bot_worker():
    global _bot_process
    print("[HF-Worker] Starting AutoTrade bot in paper mode...", flush=True)
    # Ensure DRY_RUN is enforced
    env = os.environ.copy()
    env["DRY_RUN"] = "true"
    _bot_process = subprocess.Popen(
        [sys.executable, "main.py", "--paper"],
        env=env,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    _bot_process.wait()

# Start bot in a background daemon thread
threading.Thread(target=start_bot_worker, daemon=True).start()

def get_db_connection():
    if not DB_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception:
        return None

def fetch_overview():
    conn = get_db_connection()
    if not conn:
        return (
            "⏳ Initializing...",
            "$10,000.00",
            "$0.00",
            "Waiting for first closed candle cycle...",
        )
    try:
        # Initial balance and cash delta
        init_bal_row = conn.execute("SELECT value FROM runtime_state WHERE key='initial_balance'").fetchone()
        initial_balance = float(init_bal_row[0]) if init_bal_row else 10000.0

        delta_row = conn.execute("SELECT COALESCE(SUM(cash_delta), 0) FROM cash_events").fetchone()
        cash_delta = float(delta_row[0]) if delta_row else 0.0

        current_balance = initial_balance + cash_delta

        # Open trades count
        open_count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]

        # Last heartbeat
        hb_row = conn.execute("SELECT value FROM runtime_state WHERE key='heartbeat'").fetchone()
        if hb_row and hb_row[0]:
            last_hb = time.strftime("%H:%M:%S UTC", time.gmtime(float(hb_row[0])))
            status = f"🟢 Online (Heartbeat: {last_hb}) | Open Positions: {open_count}"
        else:
            status = f"🟡 Starting up | Open Positions: {open_count}"

        return (
            status,
            f"${current_balance:,.2f}",
            f"${cash_delta:+,.2f}",
            f"Trading {os.getenv('SYMBOLS', 'BTC/USDT, ETH/USDT, SOL/USDT')}",
        )
    except Exception as e:
        return (f"⚠️ Reading DB: {e}", "$10,000.00", "$0.00", "Error")
    finally:
        conn.close()

def fetch_open_positions():
    conn = get_db_connection()
    if not conn:
        return [["No active database yet", "-", "-", "-", "-"]]
    try:
        rows = conn.execute(
            "SELECT symbol, side, quantity, price, stop_loss, target FROM trades WHERE status='open'"
        ).fetchall()
        if not rows:
            return [["None", "-", "-", "-", "-"]]
        return [
            [
                r["symbol"],
                r["side"].upper(),
                f"{r['quantity']:.4f}",
                f"${r['price']:,.2f}",
                f"${r['stop_loss']:,.2f}" if r["stop_loss"] else "-",
            ]
            for r in rows
        ]
    except Exception:
        return [["Error reading positions", "-", "-", "-", "-"]]
    finally:
        conn.close()

def fetch_recent_decisions():
    conn = get_db_connection()
    if not conn:
        return [["No decisions recorded yet", "-", "-", "-"]]
    try:
        rows = conn.execute(
            "SELECT timestamp, signal, confidence, reason, approved FROM agent_decisions ORDER BY timestamp DESC LIMIT 8"
        ).fetchall()
        if not rows:
            return [["None recorded yet", "-", "-", "-"]]
        results = []
        for r in rows:
            ts = time.strftime("%H:%M:%S", time.gmtime(r["timestamp"]))
            verdict = "✅ Approved" if r["approved"] else "🛑 Vetoed"
            conf = f"{r['confidence']*100:.0f}%" if r["confidence"] else "-"
            results.append([ts, r["signal"], conf, verdict, r["reason"][:80]])
        return results
    except Exception:
        return [["Error reading decisions", "-", "-", "-"]]
    finally:
        conn.close()

# Gradio Interface Blocks
with gr.Blocks(title="AutoTrade Bot — 24/7 Live Demo") as demo:
    gr.Markdown("# 📈 AutoTrade Multi-Agent Bot (Paper Mode)")
    gr.Markdown("Real-time autonomous trading bot running on Binance 5m candles with AI trade review and durable risk safeguards.")

    with gr.Row():
        status_box = gr.Textbox(label="Runtime Status", value="Initializing...", interactive=False)
        equity_box = gr.Textbox(label="Estimated Paper Cash", value="$10,000.00", interactive=False)
        pnl_box = gr.Textbox(label="Realized PnL", value="$0.00", interactive=False)
        market_box = gr.Textbox(label="Active Pairs", value="BTC/USDT, ETH/USDT, SOL/USDT", interactive=False)

    refresh_btn = gr.Button("🔄 Refresh Dashboard", variant="primary")

    gr.Markdown("### 📂 Open Positions")
    positions_table = gr.Dataframe(
        headers=["Symbol", "Side", "Quantity", "Entry Price", "Stop Loss"],
        value=[["Checking...", "-", "-", "-", "-"]],
        interactive=False,
    )

    gr.Markdown("### 🧠 Recent Agent Decisions (Orchestrator & Groq Review)")
    decisions_table = gr.Dataframe(
        headers=["Time (UTC)", "Signal", "Conviction", "Verdict", "Summary / Reason"],
        value=[["Checking...", "-", "-", "-", "-"]],
        interactive=False,
    )

    def update_all():
        s, eq, pnl, m = fetch_overview()
        pos = fetch_open_positions()
        dec = fetch_recent_decisions()
        return s, eq, pnl, m, pos, dec

    refresh_btn.click(
        fn=update_all,
        outputs=[status_box, equity_box, pnl_box, market_box, positions_table, decisions_table],
    )
    demo.load(
        fn=update_all,
        outputs=[status_box, equity_box, pnl_box, market_box, positions_table, decisions_table],
    )

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
