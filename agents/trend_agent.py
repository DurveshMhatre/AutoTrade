"""
Trend-Following Strategy Agent
===============================
Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic rule-based signal generation.
  • AI mode: Calls Claude API for analysis.

Never crashes — returns a safe HOLD signal on any failure.
"""

import json
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (used only in AI mode)
# ---------------------------------------------------------------------------
TREND_SYSTEM_PROMPT = """You are a disciplined trend-following trading agent for BTC/USDT spot trading on Binance.

You analyze technical indicators and generate a single trade signal. You follow rules strictly. You do not improvise or override rules based on "feelings."

SIGNAL RULES — apply in order, first match wins:

BUY signal conditions (ALL must be true):
- trend = "bullish" (EMA20 > EMA50)
- RSI is between 42 and 62 (not overbought, has upside room)
- macd_hist > 0 (momentum confirming uptrend)
- close price > bb_mid (price above middle band)
- volume_surge = true OR volume > volume_sma20 (buying interest present)

SELL signal conditions (ALL must be true):
- trend = "bearish" (EMA20 < EMA50)
- RSI is between 38 and 58 (not oversold, has downside room)
- macd_hist < 0 (momentum confirming downtrend)
- close price < bb_mid (price below middle band)

HOLD if neither BUY nor SELL conditions are fully met.

CONFIDENCE SCORING:
- Start at 0.5
- +0.15 if all 5 BUY/4 SELL conditions met perfectly
- +0.10 if RSI is in the 48-55 sweet spot
- +0.10 if volume_surge is true
- +0.05 if MACD histogram is diverging (strengthening)
- -0.15 if volatility = "high"
- Cap at 1.0, floor at 0.0

Respond ONLY with valid JSON. Never add explanation outside the JSON object."""

# ---------------------------------------------------------------------------
# Safe default returned on any failure
# ---------------------------------------------------------------------------
SAFE_HOLD = {
    "signal": "HOLD",
    "confidence": 0.0,
    "reason": "Trend agent encountered an error -- defaulting to HOLD",
    "key_indicators": {
        "trend": "unknown",
        "rsi": 0.0,
        "macd_hist": 0.0,
        "price_vs_bb_mid": "unknown",
    },
}

# ---------------------------------------------------------------------------
# Response validation helpers (AI mode)
# ---------------------------------------------------------------------------
VALID_SIGNALS = {"BUY", "SELL", "HOLD"}
VALID_PRICE_VS_BB = {"above", "below"}


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from *text*, tolerating markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(cleaned[start:end])


def _validate_response(parsed: dict) -> dict:
    """Validate and normalise the parsed response into expected schema."""
    signal = str(parsed.get("signal", "HOLD")).upper()
    if signal not in VALID_SIGNALS:
        signal = "HOLD"

    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(parsed.get("reason", "No reason provided"))[:200]

    ki = parsed.get("key_indicators", {})
    if not isinstance(ki, dict):
        ki = {}

    trend = str(ki.get("trend", "unknown"))
    rsi = ki.get("rsi", 0.0)
    try:
        rsi = float(rsi)
    except (TypeError, ValueError):
        rsi = 0.0

    macd_hist = ki.get("macd_hist", 0.0)
    try:
        macd_hist = float(macd_hist)
    except (TypeError, ValueError):
        macd_hist = 0.0

    price_vs_bb_mid = str(ki.get("price_vs_bb_mid", "unknown")).lower()
    if price_vs_bb_mid not in VALID_PRICE_VS_BB:
        price_vs_bb_mid = "unknown"

    return {
        "signal": signal,
        "confidence": round(confidence, 4),
        "reason": reason,
        "key_indicators": {
            "trend": trend,
            "rsi": round(rsi, 4),
            "macd_hist": round(macd_hist, 4),
            "price_vs_bb_mid": price_vs_bb_mid,
        },
    }


# ---------------------------------------------------------------------------
# LOCAL rule-based trend agent (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_trend_signal(market_data: dict) -> dict:
    """Generate a BUY/SELL/HOLD signal using the exact same rules
    from the trend agent's system prompt, implemented as Python logic.

    This is 100% FREE — no API calls needed.
    """
    trend = market_data.get("trend", "neutral")
    rsi = market_data.get("rsi") or 50.0
    macd_hist = market_data.get("macd_hist") or 0.0
    close = market_data.get("close") or 0.0
    bb_mid = market_data.get("bb_mid") or 0.0
    volume = market_data.get("volume") or 0.0
    volume_sma20 = market_data.get("volume_sma20") or 0.0
    volume_surge = market_data.get("volume_surge", False)
    volatility = market_data.get("volatility", "normal")

    signal = "HOLD"
    reason = "No clear setup — conditions not fully met"
    price_vs_bb = "above" if close > bb_mid else "below"

    # ── BUY conditions (ALL must be true) ─────────────────────────
    buy_conds = [
        trend == "bullish",
        42 <= rsi <= 62,
        macd_hist > 0,
        close > bb_mid,
        volume_surge or (volume_sma20 > 0 and volume > volume_sma20),
    ]

    # ── SELL conditions (ALL must be true) ────────────────────────
    sell_conds = [
        trend == "bearish",
        38 <= rsi <= 58,
        macd_hist < 0,
        close < bb_mid,
    ]

    if all(buy_conds):
        signal = "BUY"
        reason = "Bullish trend confirmed: EMA crossover + RSI sweet spot + MACD positive + volume"
    elif all(sell_conds):
        signal = "SELL"
        reason = "Bearish trend confirmed: EMA crossover + RSI range + MACD negative"

    # ── Confidence scoring ────────────────────────────────────────
    confidence = 0.5

    if signal == "BUY" and all(buy_conds):
        confidence += 0.15
    elif signal == "SELL" and all(sell_conds):
        confidence += 0.15

    if 48 <= rsi <= 55:
        confidence += 0.10

    if volume_surge:
        confidence += 0.10

    # MACD diverging (strengthening)
    if abs(macd_hist) > 0:
        confidence += 0.05

    if volatility == "high":
        confidence -= 0.15

    confidence = round(max(0.0, min(1.0, confidence)), 4)

    result = {
        "signal": signal,
        "confidence": confidence,
        "reason": reason,
        "key_indicators": {
            "trend": trend,
            "rsi": round(rsi, 4),
            "macd_hist": round(macd_hist, 4),
            "price_vs_bb_mid": price_vs_bb,
        },
    }

    logger.info(
        "Local trend signal: %s (confidence %.2f) — %s",
        signal, confidence, reason,
    )
    return result


# ---------------------------------------------------------------------------
# MEAN REVERSION signal generator (for RANGING regime)
# ---------------------------------------------------------------------------
def _local_mean_reversion_signal(market_data: dict) -> dict:
    """Generate a BUY/SELL/HOLD signal using mean reversion logic.

    In ranging markets, trend-following fails because there IS no trend.
    Instead we:
      • BUY when price touches the lower Bollinger Band AND RSI is oversold
      • FLATTEN (sell existing longs) when price touches upper BB AND RSI overbought
      • HOLD otherwise

    This is the mirror-image of the trend agent — it profits from the range,
    not from a directional move.
    """
    close = market_data.get("close") or 0.0
    rsi = market_data.get("rsi") or 50.0
    bb_lower = market_data.get("bb_lower") or 0.0
    bb_upper = market_data.get("bb_upper") or 0.0
    bb_mid = market_data.get("bb_mid") or 0.0
    macd_hist = market_data.get("macd_hist") or 0.0
    atr = market_data.get("atr") or 0.0
    volume = market_data.get("volume") or 0.0
    volume_sma20 = market_data.get("volume_sma20") or 0.0
    volume_surge = market_data.get("volume_surge", False)

    signal = "HOLD"
    reason = "No mean reversion setup — price in mid-range"
    confidence = 0.5

    if bb_lower == 0 or bb_upper == 0 or close == 0:
        return {
            "signal": "HOLD",
            "confidence": 0.3,
            "reason": "Insufficient data for mean reversion.",
            "key_indicators": {"trend": "neutral", "rsi": rsi, "macd_hist": 0, "price_vs_bb_mid": "unknown"},
        }

    bb_range = bb_upper - bb_lower
    if bb_range <= 0:
        bb_range = 1.0  # prevent division by zero

    # How close is price to the lower/upper band? (0 = at lower, 1 = at upper)
    bb_position = (close - bb_lower) / bb_range

    # ── BUY conditions (bounce from support) ──────────────────────
    # Two entry paths:
    #   A) Price in lower 40% of BB AND RSI shows weakness (≤ 45)
    #   B) Price below BB midline AND RSI between 40-50 with MACD turning up
    buy_near_lower_bb = bb_position <= 0.40
    buy_rsi_weak = rsi <= 45
    buy_below_mid_momentum = bb_position < 0.50 and 40 <= rsi <= 50 and macd_hist > 0

    if (buy_near_lower_bb and buy_rsi_weak) or buy_below_mid_momentum:
        signal = "BUY"
        reason = f"Mean reversion BUY: price in lower BB zone (pos={bb_position:.0%}), RSI={rsi:.0f}"
        confidence = 0.62

        # Confidence boosts
        if rsi <= 35:
            confidence += 0.10  # Deep oversold = stronger bounce expected
        if rsi <= 40 and macd_hist > 0:
            confidence += 0.05  # Confirmed momentum turn
        if volume_surge:
            confidence += 0.05  # Volume confirms the reversal
        if bb_position <= 0.15:
            confidence += 0.05  # Very close to/below lower BB

    # ── SELL/FLATTEN conditions (hit resistance ceiling) ──────────
    # Price in upper 30% of BB range AND RSI elevated
    sell_near_upper_bb = bb_position >= 0.70
    sell_rsi_overbought = rsi >= 60

    if sell_near_upper_bb and sell_rsi_overbought:
        signal = "SELL"  # Orchestrator will convert to FLATTEN for spot
        reason = f"Mean reversion SELL: price near upper BB (pos={bb_position:.0%}), RSI={rsi:.0f}"
        confidence = 0.62
        if rsi >= 70:
            confidence += 0.10
        if bb_position >= 0.90:
            confidence += 0.05

    # ── Penalty for high volatility (range might be breaking) ─────
    volatility = market_data.get("volatility", "normal")
    if volatility == "high":
        confidence -= 0.10

    confidence = round(max(0.0, min(1.0, confidence)), 4)

    price_vs_bb = "above" if close > bb_mid else "below"

    result = {
        "signal": signal,
        "confidence": confidence,
        "reason": reason,
        "key_indicators": {
            "trend": "ranging",
            "rsi": round(rsi, 4),
            "macd_hist": round(macd_hist, 4),
            "price_vs_bb_mid": price_vs_bb,
            "bb_position": round(bb_position, 4),
        },
    }

    logger.info(
        "Mean reversion signal: %s (confidence %.2f) — %s [BB pos=%.0f%%]",
        signal, confidence, reason, bb_position * 100,
    )
    return result


# ---------------------------------------------------------------------------
# AI-powered trend agent (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_trend_signal(market_data: dict) -> dict:
    """Call Claude API for trend analysis. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic  # lazy import — only loaded when AI mode is on

        user_prompt = (
            "Analyze this market data and generate a trading signal:\n"
            f"{json.dumps(market_data, indent=2)}"
        )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_trend_signal(market_data)

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("Calling Claude trend agent ...")
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=300,
            temperature=0.1,
            system=TREND_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text
        logger.info("Trend agent raw response: %s", raw_text)

        parsed = _extract_json(raw_text)
        result = _validate_response(parsed)

        logger.info(
            "Trend signal: %s (confidence %.2f)",
            result["signal"], result["confidence"],
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("Trend agent JSON parse error: %s — falling back to local", exc)
        return _local_trend_signal(market_data)

    except Exception as exc:  # noqa: BLE001
        logger.error("Trend agent API error: %s — falling back to local", exc)
        return _local_trend_signal(market_data)


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode
# ---------------------------------------------------------------------------
def run_trend_agent(market_data: dict, regime: str = "") -> dict:
    """Analyse *market_data* indicators and return a trade signal.

    Automatically selects the correct strategy:
      • RANGING regime  → mean reversion rules (buy support, sell resistance)
      • All other regimes → trend-following rules (EMA crossover + momentum)

    Falls back to local rules on any AI failure.

    Parameters
    ----------
    market_data : dict
        Latest indicator snapshot from ``compute_indicators()``.
    regime : str
        Current market regime (e.g. "RANGING", "STRONG_TREND_UP").
        Determines which signal generator is used.

    Returns
    -------
    dict
        ``{"signal", "confidence", "reason", "key_indicators"}``
        Always returns a valid dict — never raises.
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    # Route to the correct strategy based on regime
    if regime == "RANGING":
        logger.info("Regime is RANGING — using mean reversion signal generator")
        return _local_mean_reversion_signal(market_data)

    if use_ai:
        return _ai_trend_signal(market_data)
    else:
        return _local_trend_signal(market_data)
