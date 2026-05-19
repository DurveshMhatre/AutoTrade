"""Quick diagnostic: check what Kelly is computing on the server."""
from core.database import init_db, get_recent_trades
from core.risk_manager import compute_kelly_position_size

db = init_db()
closed = [t for t in get_recent_trades(db, limit=50) if t.get("status") == "closed"]
print(f"Closed trades: {len(closed)}")

if len(closed) >= 10:
    wins = [t for t in closed if t.get("pnl", 0) > 0]
    losses = [t for t in closed if t.get("pnl", 0) <= 0]
    wr = len(wins) / len(closed)
    avg_w = (sum(t["pnl"] / (t["price"] * t["quantity"]) for t in wins) / len(wins)) if wins else 0.03
    avg_l = (abs(sum(t["pnl"] / (t["price"] * t["quantity"]) for t in losses) / len(losses))) if losses else 0.015
    avg_l = max(avg_l, 0.005)
    print(f"Win rate: {wr:.2f}  Avg win: {avg_w:.4f}  Avg loss: {avg_l:.4f}")
else:
    wr = 0.50
    avg_w = 0.025
    avg_l = 0.015
    print(f"Not enough trades, using defaults: wr={wr} avg_w={avg_w} avg_l={avg_l}")

# Simulate the Kelly call
result = compute_kelly_position_size(
    portfolio_balance=9961.86,
    win_rate=wr,
    avg_win_pct=avg_w,
    avg_loss_pct=avg_l,
    signal_confidence=0.65,
    regime_multiplier=0.60,
    sentiment_adj=0.0,
)
print(f"Kelly result: {result}")

# Show trades
for t in closed[:8]:
    print(f"  {t.get('side'):4s} @ ${t.get('price'):>10,.2f} qty={t.get('quantity'):.6f} pnl=${t.get('pnl'):>8.2f}")
