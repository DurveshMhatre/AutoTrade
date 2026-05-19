"""
mean_reversion_agent.py
-----------------------
Generates trade signals for RANGING market conditions.
Logic: Buy support, sell resistance, tight TP, tight SL.

Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic rule-based evaluation.
  • AI mode: Calls Claude API for market evaluation.

Never crashes — returns a safe HOLD default on any failure.
"""
import json
import logging
import os

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("mean_reversion_agent")

# ---------------------------------------------------------------------------
# System prompt (used only in AI mode)
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a mean-reversion specialist with 10+ years of crypto trading.
You trade ONLY in ranging markets where price bounces between clear support and resistance.

Your entire edge comes from two rules:
1. BUY only when price is within 0.5% of strong support AND RSI < 40
2. SELL only when price is within 0.5% of strong resistance AND RSI > 60
3. HOLD if price is in the middle of the range (between 0.5%-to-support and 0.5%-to-resistance)

ENTRY QUALITY SCORING:
- Distance from S&R: closer = better (0.1% = excellent, 0.5% = fair, >0.8% = no trade)
- RSI confirmation: RSI 25-35 for BUY = excellent, 35-40 = good, 40-45 = marginal
- Volume: LOW volume at S&R level = better (exhaustion). HIGH volume = breakout risk, avoid.
- Previous bounces: Has price bounced from this level before? More bounces = stronger level.

TARGET SETTING for ranging markets:
- TP: set at the OPPOSITE S&R level (buy support → TP at resistance)
- SL: set BELOW support for BUY (not a fixed %, use the actual level)
- R:R must be >= 1.5:1 (if it's not, do not trade)

OUTPUT ONLY valid JSON:
{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0-1.0,
  "distance_to_sr_pct": float,
  "rr_ratio": float,
  "suggested_entry": float,
  "suggested_sl": float,
  "suggested_tp": float,
  "reason": "max 20 words"
}"""


# ---------------------------------------------------------------------------
# Safe default
# ---------------------------------------------------------------------------
def _safe_default(market_data: dict) -> dict:
    return {
        "signal": "HOLD",
        "confidence": 0.0,
        "distance_to_sr_pct": 999.0,
        "rr_ratio": 0.0,
        "suggested_entry": market_data.get("close", 0),
        "suggested_sl": 0.0,
        "suggested_tp": 0.0,
        "reason": "safe_default",
    }


# ---------------------------------------------------------------------------
# LOCAL rule-based mean reversion (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_mean_reversion(market_data: dict, sr_data: dict, regime: str = "RANGING") -> dict:
    """Generate mean-reversion signal using deterministic rules.
    Only call this when regime is RANGING.
    """
    try:
        close = market_data.get("close", 0)
        rsi = market_data.get("rsi", 50)
        support = sr_data.get("nearest_support", 0)
        resistance = sr_data.get("nearest_resistance", 0)
        support_strength = sr_data.get("support_strength", 0)
        resistance_strength = sr_data.get("resistance_strength", 0)

        if not close or not support or not resistance:
            logger.warning("Mean reversion agent: missing S&R data, returning HOLD")
            return _safe_default(market_data)

        dist_to_support_pct = abs(close - support) / close * 100
        dist_to_resistance_pct = abs(resistance - close) / close * 100
        range_size = resistance - support

        if range_size <= 0:
            logger.warning("Mean reversion agent: invalid range (resistance <= support)")
            return _safe_default(market_data)

        rr_buy = dist_to_resistance_pct / dist_to_support_pct if dist_to_support_pct > 0 else 0
        rr_sell = dist_to_support_pct / dist_to_resistance_pct if dist_to_resistance_pct > 0 else 0

        # ── Evaluate BUY signal (near support) ──────────────────
        buy_score = 0
        if dist_to_support_pct < 0.2:
            buy_score += 40
        elif dist_to_support_pct < 0.5:
            buy_score += 25
        elif dist_to_support_pct < 0.8:
            buy_score += 10

        if rsi < 30:
            buy_score += 30
        elif rsi < 35:
            buy_score += 20
        elif rsi < 40:
            buy_score += 10
        elif rsi < 45:
            buy_score += 5

        if support_strength >= 7:
            buy_score += 15
        elif support_strength >= 5:
            buy_score += 10
        elif support_strength >= 3:
            buy_score += 5

        if not market_data.get("volume_surge", False):
            buy_score += 10  # Low volume = exhaustion = good for mean reversion

        # ── Evaluate SELL signal (near resistance) ──────────────
        sell_score = 0
        if dist_to_resistance_pct < 0.2:
            sell_score += 40
        elif dist_to_resistance_pct < 0.5:
            sell_score += 25
        elif dist_to_resistance_pct < 0.8:
            sell_score += 10

        if rsi > 70:
            sell_score += 30
        elif rsi > 65:
            sell_score += 20
        elif rsi > 60:
            sell_score += 10
        elif rsi > 55:
            sell_score += 5

        if resistance_strength >= 7:
            sell_score += 15
        elif resistance_strength >= 5:
            sell_score += 10
        elif resistance_strength >= 3:
            sell_score += 5

        if not market_data.get("volume_surge", False):
            sell_score += 10

        # ── Decide signal ───────────────────────────────────────
        signal = "HOLD"
        confidence = 0.0
        distance_to_sr = 999.0
        rr_ratio = 0.0
        suggested_entry = close
        suggested_sl = 0.0
        suggested_tp = 0.0
        reason = "No clear mean-reversion setup"

        import config as _cfg
        sl_buffer = getattr(_cfg, "MEAN_REVERSION_SL_BUFFER_PCT", 0.003)
        tp_buffer = getattr(_cfg, "MEAN_REVERSION_TP_BUFFER_PCT", 0.002)

        if buy_score > sell_score and buy_score >= 50:
            # BUY near support
            if dist_to_support_pct > 0.8:
                reason = f"Price {dist_to_support_pct:.2f}% from support — wait for touch"
            elif rr_buy < 1.5:
                reason = f"R:R {rr_buy:.2f} below 1.5 minimum"
            else:
                signal = "BUY"
                confidence = min(buy_score / 100, 0.95)
                distance_to_sr = dist_to_support_pct
                rr_ratio = rr_buy
                suggested_entry = close
                suggested_sl = support * (1 - sl_buffer)   # Below support
                suggested_tp = resistance * (1 - tp_buffer) # Just before resistance
                reason = f"BUY near support ${support:,.0f}, RSI={rsi:.0f}"

        elif sell_score > buy_score and sell_score >= 50:
            # SELL near resistance (spot-only → this becomes a flatten signal)
            if dist_to_resistance_pct > 0.8:
                reason = f"Price {dist_to_resistance_pct:.2f}% from resistance — wait"
            elif rr_sell < 1.5:
                reason = f"R:R {rr_sell:.2f} below 1.5 minimum"
            else:
                signal = "SELL"
                confidence = min(sell_score / 100, 0.95)
                distance_to_sr = dist_to_resistance_pct
                rr_ratio = rr_sell
                suggested_entry = close
                suggested_sl = resistance * (1 + sl_buffer)  # Above resistance
                suggested_tp = support * (1 + tp_buffer)      # Just above support
                reason = f"SELL near resistance ${resistance:,.0f}, RSI={rsi:.0f}"

        logger.info(
            "Mean reversion (local): %s conf=%.2f dist_sr=%.2f%% R:R=%.2f | "
            "BUY_score=%d SELL_score=%d | reason=%s",
            signal, confidence, distance_to_sr, rr_ratio,
            buy_score, sell_score, reason,
        )

        return {
            "signal": signal,
            "confidence": confidence,
            "distance_to_sr_pct": distance_to_sr,
            "rr_ratio": rr_ratio,
            "suggested_entry": suggested_entry,
            "suggested_sl": suggested_sl,
            "suggested_tp": suggested_tp,
            "reason": reason,
        }

    except Exception as e:
        logger.error("Mean reversion agent (local) failed: %s", e)
        return _safe_default(market_data)


# ---------------------------------------------------------------------------
# AI-powered mean reversion (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_mean_reversion(market_data: dict, sr_data: dict, regime: str = "RANGING") -> dict:
    """Generate mean-reversion signal using Claude API."""
    try:
        import anthropic

        close = market_data.get("close", 0)
        rsi = market_data.get("rsi", 50)
        support = sr_data.get("nearest_support", 0)
        resistance = sr_data.get("nearest_resistance", 0)
        support_strength = sr_data.get("support_strength", 0)
        resistance_strength = sr_data.get("resistance_strength", 0)

        if not close or not support or not resistance:
            logger.warning("Mean reversion agent: missing S&R data, returning HOLD")
            return _safe_default(market_data)

        dist_to_support_pct = abs(close - support) / close * 100
        dist_to_resistance_pct = abs(resistance - close) / close * 100
        range_size = resistance - support
        rr_buy = dist_to_resistance_pct / dist_to_support_pct if dist_to_support_pct > 0 else 0
        rr_sell = dist_to_support_pct / dist_to_resistance_pct if dist_to_resistance_pct > 0 else 0

        user_prompt = f"""
Market data:
- Current price: ${close:,.2f}
- RSI: {rsi:.1f}
- Volume surge: {market_data.get('volume_surge', False)}
- Volatility: {market_data.get('volatility', 'normal')}
- Trend: {market_data.get('trend', 'neutral')}

S&R levels:
- Support: ${support:,.2f} (strength: {support_strength}/10) — distance: {dist_to_support_pct:.2f}%
- Resistance: ${resistance:,.2f} (strength: {resistance_strength}/10) — distance: {dist_to_resistance_pct:.2f}%
- Range size: ${range_size:,.2f} ({range_size/close*100:.2f}% of price)

R:R if BUY at support: {rr_buy:.2f}:1
R:R if SELL at resistance: {rr_sell:.2f}:1

Generate a mean-reversion trading signal.
"""
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY not set — falling back to local mode")
            return _local_mean_reversion(market_data, sr_data, regime)

        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw = response.content[0].text.strip()
        result = json.loads(raw)

        # Hard validation: never signal if R:R < 1.5
        if result.get("rr_ratio", 0) < 1.5 and result.get("signal") != "HOLD":
            logger.info("Mean reversion: R:R %.2f < 1.5 minimum, forcing HOLD", result.get("rr_ratio", 0))
            result["signal"] = "HOLD"
            result["reason"] = f"R:R {result.get('rr_ratio', 0):.2f} below 1.5 minimum"

        # Hard validation: never buy if price is more than 0.8% from support
        if result.get("signal") == "BUY" and dist_to_support_pct > 0.8:
            logger.info("Mean reversion: price %.2f%% from support, too far for BUY", dist_to_support_pct)
            result["signal"] = "HOLD"
            result["reason"] = f"Price {dist_to_support_pct:.2f}% from support — wait for touch"

        logger.info(
            "Mean reversion signal: %s conf=%.2f dist_sr=%.2f%% R:R=%.2f",
            result.get("signal"), result.get("confidence", 0),
            result.get("distance_to_sr_pct", 0), result.get("rr_ratio", 0),
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("Mean reversion agent JSON parse error: %s", e)
        return _safe_default(market_data)
    except Exception as e:
        logger.error("Mean reversion agent (AI) failed: %s — falling back to local", e)
        return _local_mean_reversion(market_data, sr_data, regime)


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode
# ---------------------------------------------------------------------------
def run_mean_reversion_agent(market_data: dict, sr_data: dict, regime: str = "RANGING") -> dict:
    """Main entry point for the Mean Reversion Agent.

    Parameters
    ----------
    market_data : dict
        Current 5M indicators (close, rsi, volume_surge, volatility, trend, etc.)
    sr_data : dict
        Support/Resistance data (nearest_support, nearest_resistance, support_strength, etc.)
    regime : str
        Current market regime (should be "RANGING" when this agent is called)
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_mean_reversion(market_data, sr_data, regime)
    else:
        return _local_mean_reversion(market_data, sr_data, regime)
