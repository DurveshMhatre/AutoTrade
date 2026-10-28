import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# â”€â”€ Dynamic Parameter Loader â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Hyperopt and the Autopsy Evolution Agent write optimised values to
# config/live_params.json.  At import time we read that file and let
# its values OVERRIDE the static defaults below.
_PARAMS_PATH = Path(__file__).resolve().parent / "config" / "live_params.json"
_dynamic_params: dict = {}
if _PARAMS_PATH.exists():
    try:
        with open(_PARAMS_PATH, "r", encoding="utf-8") as _fp:
            _dynamic_params = json.load(_fp)
    except Exception:
        pass  # Corrupt file â€” silently fall back to defaults

def _p(key: str, default):
    """Return the dynamic override for *key* if present, else *default*."""
    return _dynamic_params.get(key, default)


# â”€â”€ Dry-Run (Paper Trading) Mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# True  = simulate trades against live prices â€” NO real orders placed
# False = real live/testnet execution (existing behaviour)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# â”€â”€ Multi-Pair Support â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SYMBOLS = _p("SYMBOLS", ["BTC/USDT", "ETH/USDT", "SOL/USDT"])
SYMBOL  = SYMBOLS[0]  # backward-compatible alias for single-pair code paths

TIMEFRAME = "5m"
CANDLE_LIMIT = 200
MAX_POSITIONS = _p("MAX_POSITIONS", 3)
RISK_PER_TRADE_PCT = _p("RISK_PER_TRADE_PCT", 0.02)
DAILY_LOSS_LIMIT_PCT = _p("DAILY_LOSS_LIMIT_PCT", 0.05)
MIN_CONFIDENCE = _p("MIN_CONFIDENCE", 0.50)
STOP_LOSS_PCT = _p("STOP_LOSS_PCT", 0.015)
TAKE_PROFIT_PCT = _p("TAKE_PROFIT_PCT", 0.03)

# â”€â”€ Agent Mode â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# False = FREE local rules (no API needed)
# True  = Claude AI (needs ANTHROPIC_API_KEY in .env)
USE_AI_AGENTS = False

# â”€â”€ Multi-Timeframe Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
MTF_TIMEFRAMES = {"1h": 200, "4h": 100, "1d": 60}

# â”€â”€ Regime Agent Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
REGIME_ADX_TREND_THRESHOLD = 25       # ADX above this = trending
REGIME_ADX_CHOP_THRESHOLD = 20        # ADX below this = chop/no direction
REGIME_CACHE_SECONDS = 300            # Cache regime result for 5 minutes (matches candle cycle)

# â”€â”€ Sentiment Agent Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SENTIMENT_FEAR_EXTREME = 20           # Below = extreme fear
SENTIMENT_GREED_EXTREME = 80          # Above = extreme greed
FUNDING_OVERLEVERAGED_THRESHOLD = 0.001  # 0.1% per 8h = overleveraged

# â”€â”€ Phase 2: Strategy Agent Settings â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
SR_PROXIMITY_PCT = 0.015              # 1.5% distance defines "near" S/R
ORDER_BOOK_DEPTH = 50                 # Depth of order book to fetch
NEWS_LOOKBACK_HOURS = 12              # Lookback for news API

# â”€â”€ Position Sizing Floors (Bug 2 fix) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Never go below these â€” fees destroy micro-positions
MIN_POSITION_BTC = 0.001              # $76 at $76k BTC â€” absolute floor
MIN_POSITION_USD = 75.0               # Dollar floor
TARGET_RISK_PER_TRADE_USD = 20.0      # $20 risk per trade minimum

# â”€â”€ Mean Reversion Stop Distances (tighter than trend following) â”€â”€
MEAN_REVERSION_SL_BUFFER_PCT = 0.003  # 0.3% below support (tight)
MEAN_REVERSION_TP_BUFFER_PCT = 0.002  # 0.2% before resistance (don't wait for exact touch)

# â”€â”€ Agent Refresh Intervals (in bot cycles, each cycle = 5 minutes) â”€â”€
# Reduces Claude API calls from 7/cycle to ~2-3/cycle = 60-70% cost savings
AGENT_REFRESH_CYCLES = {
    "regime": 3,         # Every 15 minutes (regime doesn't change every 5 min)
    "mtf": 3,            # Every 15 minutes (HTF candles change slowly)
    "sentiment": 6,      # Every 30 minutes (fear/greed and funding refresh slowly)
    "sr": 6,             # Every 30 minutes (S&R levels are stable)
    "news": 12,          # Every 60 minutes (news sentiment changes slowly)
    "order_flow": 1,     # Every cycle (order book changes fast â€” keep fresh)
    "orchestrator": 1,   # Every cycle (final decision must be current)
    "trend": 1,          # Every cycle (5M signal needs to be current)
}

# â”€â”€ Phase 4: Strategy Library â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

# Runtime safety settings. Separate ledgers prevent paper/live mixing.
PAPER_BALANCE = float(os.getenv("PAPER_BALANCE", "10000"))
FEE_RATE = 0.001
SLIPPAGE_PCT = 0.001
MAX_ALLOCATION_PCT = 0.10
MAX_PORTFOLIO_RISK_PCT = 0.05
MONITOR_SECONDS = 5
LLM_REVIEW_PROVIDER = os.getenv("LLM_REVIEW_PROVIDER", "none").lower()
LLM_REVIEW_MODEL = os.getenv("LLM_REVIEW_MODEL", "")
