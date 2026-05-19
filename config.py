SYMBOL = "BTC/USDT"
TIMEFRAME = "5m"
CANDLE_LIMIT = 200
MAX_POSITIONS = 3
RISK_PER_TRADE_PCT = 0.02
DAILY_LOSS_LIMIT_PCT = 0.05
MIN_CONFIDENCE = 0.50
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

# ── Position Sizing Floors (Bug 2 fix) ────────────────────────
# Never go below these — fees destroy micro-positions
MIN_POSITION_BTC = 0.001              # $76 at $76k BTC — absolute floor
MIN_POSITION_USD = 75.0               # Dollar floor
TARGET_RISK_PER_TRADE_USD = 20.0      # $20 risk per trade minimum

# ── Mean Reversion Stop Distances (tighter than trend following) ──
MEAN_REVERSION_SL_BUFFER_PCT = 0.003  # 0.3% below support (tight)
MEAN_REVERSION_TP_BUFFER_PCT = 0.002  # 0.2% before resistance (don't wait for exact touch)

# ── Agent Refresh Intervals (in bot cycles, each cycle = 5 minutes) ──
# Reduces Claude API calls from 7/cycle to ~2-3/cycle = 60-70% cost savings
AGENT_REFRESH_CYCLES = {
    "regime": 3,         # Every 15 minutes (regime doesn't change every 5 min)
    "mtf": 3,            # Every 15 minutes (HTF candles change slowly)
    "sentiment": 6,      # Every 30 minutes (fear/greed and funding refresh slowly)
    "sr": 6,             # Every 30 minutes (S&R levels are stable)
    "news": 12,          # Every 60 minutes (news sentiment changes slowly)
    "order_flow": 1,     # Every cycle (order book changes fast — keep fresh)
    "orchestrator": 1,   # Every cycle (final decision must be current)
    "trend": 1,          # Every cycle (5M signal needs to be current)
}

# ── Phase 4: Strategy Library ────────────────────────────────────
STRATEGIES = {
    "TREND_FOLLOWING": {
        "applicable_regimes": ["STRONG_TREND_UP", "STRONG_TREND_DOWN", "WEAK_TREND_UP", "WEAK_TREND_DOWN"],
        "action": "TRADE",
        "type": "TREND_FOLLOWING",
        "max_positions": 3,
        "size_multiplier": 1.0,
    },
    "MEAN_REVERSION": {
        "applicable_regimes": ["RANGING"],
        "action": "TRADE",
        "type": "MEAN_REVERSION",
        "min_conviction": 55,
        "max_positions": 2,
        "size_multiplier": 0.8,
        "tp_style": "fixed_opposite_sr",       # TP = opposite S&R level
        "sl_style": "below_sr_level",          # SL = below support / above resistance
        "require_sr_zone": True,
        "require_rsi_extreme": True,
        "min_sr_strength": 4,
        "max_distance_from_sr_pct": 0.8,       # Never enter if > 0.8% from S&R
        "min_rr_ratio": 1.5,                   # Never trade if R:R < 1.5
    },
    "CAPITULATION_BOUNCE": {
        "applicable_regimes": ["CAPITULATION"],
        "action": "TRADE",
        "type": "CAPITULATION_BOUNCE",
        "max_positions": 1,
        "size_multiplier": 0.5,
    },
    "FLAT": {
        "applicable_regimes": ["CHOP", "DISTRIBUTION", "UNKNOWN"],
        "action": "NO_TRADE",
        "type": "FLAT",
        "max_positions": 0,
        "size_multiplier": 0.0,
    }
}
