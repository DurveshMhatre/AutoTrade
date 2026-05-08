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

# ── Multi-Timeframe Settings ───────────────────────────────────
MTF_TIMEFRAMES = {"1h": 200, "4h": 100, "1d": 60}

# ── Regime Agent Settings ──────────────────────────────────────
REGIME_ADX_TREND_THRESHOLD = 25       # ADX above this = trending
REGIME_ADX_CHOP_THRESHOLD = 20        # ADX below this = chop/no direction
REGIME_CACHE_SECONDS = 300            # Cache regime result for 5 minutes (matches candle cycle)

# ── Sentiment Agent Settings ──────────────────────────────────
SENTIMENT_FEAR_EXTREME = 20           # Below = extreme fear
SENTIMENT_GREED_EXTREME = 80          # Above = extreme greed
FUNDING_OVERLEVERAGED_THRESHOLD = 0.001  # 0.1% per 8h = overleveraged

# ── Phase 2: Strategy Agent Settings ───────────────────────────
SR_PROXIMITY_PCT = 0.015              # 1.5% distance defines "near" S/R
ORDER_BOOK_DEPTH = 50                 # Depth of order book to fetch
NEWS_LOOKBACK_HOURS = 12              # Lookback for news API

# ── Phase 4: Strategy Library ────────────────────────────────────
STRATEGIES = {
    "TREND_FOLLOWING": {
        "applicable_regimes": ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"],
        "action": "TRADE",
        "max_positions": 3,
        "size_multiplier": 1.0,
    },
    "MEAN_REVERSION": {
        "applicable_regimes": ["RANGING"],
        "action": "TRADE",
        "max_positions": 2,
        "size_multiplier": 0.8,
    },
    "CAPITULATION_BOUNCE": {
        "applicable_regimes": ["CAPITULATION"],
        "action": "TRADE",
        "max_positions": 1,
        "size_multiplier": 0.5,
    },
    "FLAT": {
        "applicable_regimes": ["CHOP", "DISTRIBUTION", "UNKNOWN"],
        "action": "NO_TRADE",
        "max_positions": 0,
        "size_multiplier": 0.0,
    }
}
