SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
CANDLE_LIMIT = 200
MAX_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.02
DAILY_LOSS_LIMIT_PCT = 0.05
MIN_CONFIDENCE = 0.60
STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.03

# ── Agent Mode ──────────────────────────────────────────────────
# False = FREE local rules (no API needed)
# True  = Claude AI (needs ANTHROPIC_API_KEY in .env)
USE_AI_AGENTS = False
