"""Quick Stats Checker for AutoTrade Bot.
Prints 12-hour summary of balance, trades, PnL, and agent decisions.
"""
import sqlite3
import time
from pathlib import Path

DB_PATH = Path("trading_bot_paper.db")
if not DB_PATH.exists():
    DB_PATH = Path("trading_bot_live.db")

if not DB_PATH.exists():
    print("❌ No database file found (trading_bot_paper.db or trading_bot_live.db).")
    exit(1)

conn = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

print("=" * 60)
print(f"📊 AutoTrade Bot Stats — Database: {DB_PATH.name}")
print("=" * 60)

# 1. Balance & Realized PnL
init_row = conn.execute("SELECT value FROM runtime_state WHERE key='initial_balance'").fetchone()
initial_balance = float(init_row[0]) if init_row else 10000.0

pnl_row = conn.execute("SELECT COALESCE(SUM(cash_delta), 0) FROM cash_events").fetchone()
realized_pnl = float(pnl_row[0]) if pnl_row else 0.0

print(f"💰 Starting Balance: ${initial_balance:,.2f}")
print(f"📈 Realized P&L:     {realized_pnl:+,.2f} USD")
print(f"💵 Current Equity:   ${(initial_balance + realized_pnl):,.2f}")

# 2. Heartbeat & Mode
hb_row = conn.execute("SELECT value FROM runtime_state WHERE key='heartbeat'").fetchone()
if hb_row and hb_row[0]:
    last_hb = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(hb_row[0])))
    diff = int(time.time() - float(hb_row[0]))
    print(f"💓 Last Heartbeat:   {last_hb} ({diff}s ago)")
else:
    print("💓 Last Heartbeat:   None recorded yet")

print("-" * 60)

# 3. Open Positions
open_trades = conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
print(f"📂 Open Positions ({len(open_trades)}):")
if not open_trades:
    print("   No positions currently open.")
else:
    for t in open_trades:
        print(f"   • {t['symbol']} | Side: {t['side'].upper()} | Qty: {t['quantity']} | Entry: ${t['price']:,.2f} | Stop: ${t['stop_loss']:,.2f}")

print("-" * 60)

# 4. Closed Trades in the last 12 hours
twelve_hours_ago = int(time.time() - 12 * 3600)
closed_trades = conn.execute(
    "SELECT * FROM trades WHERE status='closed' AND exit_time >= ? ORDER BY exit_time DESC",
    (twelve_hours_ago,)
).fetchall()

print(f"📋 Closed Trades in Last 12h ({len(closed_trades)}):")
if not closed_trades:
    print("   No closed trades in the last 12 hours.")
else:
    wins = sum(1 for t in closed_trades if (t.get('pnl') or 0) > 0)
    print(f"   Win Rate: {wins}/{len(closed_trades)} ({wins/len(closed_trades)*100:.1f}%)")
    for t in closed_trades:
        pnl = t.get('pnl', 0) or 0
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        print(f"   • {t['symbol']} | PnL: {pnl_str} | Entry: ${t['price']:,.2f} | Exit: ${t.get('exit_price', 0):,.2f}")

print("-" * 60)

# 5. Last 5 Agent Decisions
decisions = conn.execute(
    "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 5"
).fetchall()
print("🧠 Last 5 Agent Decisions:")
if not decisions:
    print("   No decisions recorded yet.")
else:
    for d in decisions:
        ts = time.strftime("%H:%M:%S", time.gmtime(d['timestamp']))
        verdict = "APPROVED" if d['approved'] else "VETOED"
        print(f"   [{ts}] {d['signal']} ({d['confidence']*100:.0f}%) -> {verdict}: {d['reason'][:60]}")

print("=" * 60)
conn.close()
