"""
Risk Management Module — Pre-Trade Safety Layer
=================================================
Evaluates every trade signal BEFORE an order is placed.
Can block any trade that violates risk rules.

Hard blocks are checked first (any single violation → rejected).
If all blocks pass, position sizing and price levels are computed.
"""

import logging

logger = logging.getLogger(__name__)

# Binance minimum order size for BTC
MIN_BTC_QUANTITY = 0.0001

# ── Phase 3: Dynamic Sizing & Heat Monitoring ───────────────────────

def compute_portfolio_heat(open_positions: list[dict], portfolio_balance: float, current_regime: str) -> dict:
    """
    Portfolio heat = sum of all open risk as % of portfolio.
    Risk per position = abs(entry - stop_loss) * quantity
    """
    if portfolio_balance <= 0:
        return {"current_heat_pct": 0, "max_heat_pct": 0, "new_trade_blocked": True, "heat_status": "high", "slots_available": 0}

    total_risk = sum(
        abs(p.get("entry_price", 0) - p.get("stop_loss", 0)) * p.get("quantity", 0)
        for p in open_positions
    )
    heat_pct = (total_risk / portfolio_balance) * 100

    # Heat limits by regime
    heat_limits = {
        "STRONG_TREND_UP": 8.0,
        "STRONG_TREND_DOWN": 8.0,
        "WEAK_TREND_UP": 5.0,
        "WEAK_TREND_DOWN": 3.0,
        "RANGING": 4.0,
        "CAPITULATION": 2.0,
        "CHOP": 0.0,
        "DISTRIBUTION": 0.0,
        "default": 6.0
    }
    
    max_heat = heat_limits.get(current_regime, heat_limits["default"])

    return {
        "current_heat_pct": round(heat_pct, 2),
        "max_heat_pct": max_heat,
        "new_trade_blocked": heat_pct >= max_heat,
        "heat_status": "safe" if heat_pct < 4 else "moderate" if heat_pct < 6 else "high",
        "slots_available": max(0, 3 - len(open_positions))
    }

def compute_kelly_position_size(
    portfolio_balance: float,
    win_rate: float,
    avg_win_pct: float,
    avg_loss_pct: float,
    signal_confidence: float,
    regime_multiplier: float,
    sentiment_adj: float,
) -> dict:
    """
    Compute dynamic position size using the Kelly Criterion.
    """
    if portfolio_balance <= 0 or avg_loss_pct <= 0:
        return {"position_usd": 0.0, "size_pct": 0.0, "kelly_raw": 0.0, "quarter_kelly": 0.0, "reasoning": "invalid inputs"}
        
    b = avg_win_pct / avg_loss_pct
    p = win_rate
    q = 1.0 - p
    
    # Kelly formula: f* = (p*b - q) / b
    kelly_fraction = (p * b - q) / b
    
    # Floor to zero (no edge = no trade)
    if kelly_fraction <= 0:
        kelly_fraction = 0.0
        
    # Quarter-Kelly for safety
    quarter_kelly = kelly_fraction * 0.25
    
    # Adjust for signal quality and market conditions
    size_pct = quarter_kelly * signal_confidence * regime_multiplier
    
    # Add sentiment adjustment safely
    size_pct = size_pct * (1.0 + sentiment_adj)
    
    # Hard caps: never risk more than 3% even with perfect signals, min 0.5% if approved
    if size_pct > 0:
        size_pct = max(0.005, min(0.03, size_pct))
        
    position_usd = portfolio_balance * size_pct
    
    return {
        "position_usd": round(position_usd, 2),
        "size_pct": round(size_pct * 100, 2),
        "kelly_raw": round(kelly_fraction, 4),
        "quarter_kelly": round(quarter_kelly, 4),
        "reasoning": f"Kelly={kelly_fraction:.1%}, QKelly={quarter_kelly:.1%}, final={size_pct:.1%}"
    }


def _rejected(reason: str) -> dict:
    """Return a standardised rejection payload."""
    return {
        "approved": False,
        "reason": reason,
        "quantity": None,
        "stop_loss_price": None,
        "take_profit_price": None,
        "risk_dollars": None,
    }


def evaluate_trade(
    signal: dict,
    portfolio: dict,
    market_data: dict,
    config: dict,
) -> dict:
    """Evaluate whether a trade signal should be executed.

    Parameters
    ----------
    signal : dict
        Output from the trend agent: signal, confidence, reason, key_indicators.
    portfolio : dict
        Current portfolio state: usdt_balance, open_positions, daily_loss_pct.
    market_data : dict
        Latest indicator snapshot: close, volatility, etc.
    config : dict
        Trading parameters: MIN_CONFIDENCE, MAX_POSITIONS, DAILY_LOSS_LIMIT_PCT,
        RISK_PER_TRADE_PCT, STOP_LOSS_PCT, TAKE_PROFIT_PCT.

    Returns
    -------
    dict
        approved, reason, quantity, stop_loss_price, take_profit_price, risk_dollars.
    """

    # ── HARD BLOCKS (order matters — first match wins) ─────────────────────

    # 1. HOLD signal — nothing to do
    if signal.get("signal") == "HOLD":
        logger.info("Risk block: HOLD signal received")
        return _rejected("hold_signal")

    # 2. Confidence too low
    if signal.get("confidence", 0.0) < config.get("MIN_CONFIDENCE", 0.60):
        logger.info(
            "Risk block: confidence %.2f < minimum %.2f",
            signal.get("confidence", 0.0),
            config.get("MIN_CONFIDENCE", 0.60),
        )
        return _rejected("low_confidence")

    # 3. Max positions reached
    if portfolio.get("open_positions", 0) >= config.get("MAX_POSITIONS", 3):
        logger.info(
            "Risk block: open_positions %d >= max %d",
            portfolio.get("open_positions", 0),
            config.get("MAX_POSITIONS", 3),
        )
        return _rejected("max_positions")

    # 4. Daily loss limit hit
    if portfolio.get("daily_loss_pct", 0.0) >= config.get("DAILY_LOSS_LIMIT_PCT", 0.05):
        logger.info(
            "Risk block: daily_loss_pct %.4f >= limit %.4f",
            portfolio.get("daily_loss_pct", 0.0),
            config.get("DAILY_LOSS_LIMIT_PCT", 0.05),
        )
        return _rejected("daily_loss_limit")

    # 5. Insufficient balance
    if portfolio.get("usdt_balance", 0.0) < 50:
        logger.info(
            "Risk block: usdt_balance %.2f < $50 minimum",
            portfolio.get("usdt_balance", 0.0),
        )
        return _rejected("insufficient_balance")

    # 6. High volatility with mediocre confidence
    if (
        market_data.get("volatility") == "high"
        and signal.get("confidence", 0.0) < 0.75
    ):
        logger.info(
            "Risk block: high volatility + confidence %.2f < 0.75",
            signal.get("confidence", 0.0),
        )
        return _rejected("high_volatility")

    # ── POSITION SIZING ───────────────────────────────────────────────────

    close_price = market_data.get("close", 0.0)
    if close_price <= 0:
        logger.error("Risk block: invalid close price %.4f", close_price)
        return _rejected("invalid_price")

    risk_per_trade_pct = config.get("RISK_PER_TRADE_PCT", 0.02)
    stop_loss_pct = config.get("STOP_LOSS_PCT", 0.015)
    take_profit_pct = config.get("TAKE_PROFIT_PCT", 0.03)

    max_risk_dollars = portfolio.get("usdt_balance", 0.0) * risk_per_trade_pct
    stop_loss_distance = close_price * stop_loss_pct

    if stop_loss_distance <= 0:
        logger.error("Risk block: stop_loss_distance is zero or negative")
        return _rejected("invalid_price")

    quantity = round(max_risk_dollars / stop_loss_distance, 6)

    # Binance minimum check
    if quantity < MIN_BTC_QUANTITY:
        logger.info(
            "Risk block: quantity %.6f < minimum %.4f BTC",
            quantity, MIN_BTC_QUANTITY,
        )
        return _rejected("position_too_small")

    # ── PRICE LEVELS ──────────────────────────────────────────────────────

    direction = signal.get("signal", "BUY")

    if direction == "BUY":
        stop_loss_price = round(close_price * (1 - stop_loss_pct), 2)
        take_profit_price = round(close_price * (1 + take_profit_pct), 2)
    else:  # SELL
        stop_loss_price = round(close_price * (1 + stop_loss_pct), 2)
        take_profit_price = round(close_price * (1 - take_profit_pct), 2)

    risk_dollars = round(max_risk_dollars, 2)

    logger.info(
        "Trade APPROVED: %s %.6f BTC | SL=%.2f | TP=%.2f | risk=$%.2f",
        direction, quantity, stop_loss_price, take_profit_price, risk_dollars,
    )

    return {
        "approved": True,
        "reason": "approved",
        "quantity": quantity,
        "stop_loss_price": stop_loss_price,
        "take_profit_price": take_profit_price,
        "risk_dollars": risk_dollars,
    }
