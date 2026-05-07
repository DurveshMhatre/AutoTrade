"""
Market Regime Detection Agent
==============================
Classifies the current market into one of 7 regimes BEFORE any trading
decision is made.  Every downstream agent reads this output.

Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic rule-based classification.
  • AI mode: Calls Claude API for analysis.

Regimes
-------
STRONG_TREND_UP · WEAK_TREND_UP · RANGING · STRONG_TREND_DOWN
DISTRIBUTION · CAPITULATION · CHOP

Never crashes — returns a safe RANGING regime on any failure.
"""

import json
import logging
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (used only in AI mode)
# ---------------------------------------------------------------------------
REGIME_SYSTEM_PROMPT = """You are a market regime classifier for BTC/USDT with 10+ years of crypto trading experience.

Analyze the provided multi-timeframe data and classify the current regime:

REGIMES:
- STRONG_TREND_UP: Price making higher highs/lows, EMA stack aligned bullish (20>50>200), volume expanding on green candles. Strategy: trend-follow aggressively, wider stops, let winners run.
- WEAK_TREND_UP: Grinding up, EMAs mixed, volume declining. Strategy: smaller size, tighter TP, exit fast.
- RANGING: Price bouncing between clear S/R, ADX < 25. Strategy: mean reversion only, buy support sell resistance.
- STRONG_TREND_DOWN: Lower highs/lows, EMA stack bearish. Strategy: short-only or flat (spot = CASH).
- DISTRIBUTION: High volume at top, multiple failed breakouts, smart money selling. Strategy: reduce size 50%, no new longs.
- CAPITULATION: Extreme volume spike down, RSI < 20, fear/greed < 15. Strategy: small contra-trend long only, tight stop.
- CHOP: No clear direction, conflicting signals. Strategy: FLAT — do not trade.

Required inputs: 1H OHLCV (200 candles), 4H OHLCV (100 candles), 1D OHLCV (60 candles), ADX, ATR, volume trend.

Respond ONLY in JSON:
{"regime": "REGIME_NAME", "confidence": 0.0-1.0, "strategy_bias": "trend_follow|mean_revert|flat|short_only", "position_size_multiplier": 0.0-1.5, "reasoning": "max 30 words"}"""

# ---------------------------------------------------------------------------
# Safe default returned on any failure
# ---------------------------------------------------------------------------
SAFE_DEFAULT = {
    "regime": "RANGING",
    "confidence": 0.0,
    "strategy_bias": "flat",
    "position_size_multiplier": 0.5,
    "reasoning": "Regime agent encountered an error -- defaulting to RANGING",
}

# ---------------------------------------------------------------------------
# Valid values
# ---------------------------------------------------------------------------
VALID_REGIMES = {
    "STRONG_TREND_UP", "WEAK_TREND_UP", "RANGING",
    "STRONG_TREND_DOWN", "DISTRIBUTION", "CAPITULATION", "CHOP",
}
VALID_BIASES = {"trend_follow", "mean_revert", "flat", "short_only"}

# ---------------------------------------------------------------------------
# Regime caching (avoids reclassifying every 5 min cycle)
# ---------------------------------------------------------------------------
_cached_regime: dict | None = None
_cached_regime_ts: float = 0.0


def _is_cache_valid() -> bool:
    """Return True if the cached regime is still fresh."""
    try:
        import config
        cache_ttl = getattr(config, "REGIME_CACHE_SECONDS", 900)
    except Exception:
        cache_ttl = 900
    return _cached_regime is not None and (time.time() - _cached_regime_ts) < cache_ttl


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict:
    """Extract the first JSON object from *text*, tolerating markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(cleaned[start:end])


def _validate(parsed: dict) -> dict:
    """Normalise raw parsed dict into the expected schema."""
    regime = str(parsed.get("regime", "RANGING")).upper()
    if regime not in VALID_REGIMES:
        regime = "RANGING"

    confidence = parsed.get("confidence", 0.0)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    bias = str(parsed.get("strategy_bias", "flat")).lower()
    if bias not in VALID_BIASES:
        bias = "flat"

    multiplier = parsed.get("position_size_multiplier", 0.5)
    try:
        multiplier = float(multiplier)
    except (TypeError, ValueError):
        multiplier = 0.5
    multiplier = max(0.0, min(1.5, multiplier))

    reasoning = str(parsed.get("reasoning", ""))[:100]

    return {
        "regime": regime,
        "confidence": round(confidence, 4),
        "strategy_bias": bias,
        "position_size_multiplier": round(multiplier, 2),
        "reasoning": reasoning,
    }


# ---------------------------------------------------------------------------
# LOCAL rule-based regime classifier (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_regime_signal(mtf_indicators: dict) -> dict:
    """Classify market regime using deterministic rules on multi-timeframe data.

    Parameters
    ----------
    mtf_indicators : dict
        Must contain keys: "5m", "1h", "4h", "1d" — each a dict of indicators
        from compute_indicators / compute_mtf_indicators.
    """
    # Extract per-timeframe data (with safe defaults)
    d5m = mtf_indicators.get("5m", {})
    d1h = mtf_indicators.get("1h", {})
    d4h = mtf_indicators.get("4h", {})
    d1d = mtf_indicators.get("1d", {})

    # Key inputs from the highest-authority timeframe (daily)
    daily_trend = d1d.get("trend", "neutral")
    daily_rsi = d1d.get("rsi") or 50.0
    daily_adx = d1d.get("adx") or 20.0

    h4_trend = d4h.get("trend", "neutral")
    h4_adx = d4h.get("adx") or 20.0

    h1_trend = d1h.get("trend", "neutral")

    # EMA stack check (using 1D data if available, else 4H)
    ref = d1d if d1d.get("ema_20") else d4h
    ema_20 = ref.get("ema_20") or 0
    ema_50 = ref.get("ema_50") or 0
    ema_200 = ref.get("ema_200") or 0

    bullish_stack = ema_20 > ema_50 > ema_200 > 0
    bearish_stack = 0 < ema_20 < ema_50 < ema_200 if ema_200 else False

    # Volume trend from 1H (most responsive)
    vol_trend = d1h.get("volume_trend", "neutral")

    # 5M data for entry-level signals
    rsi_5m = d5m.get("rsi") or 50.0
    vol_surge_5m = d5m.get("volume_surge", False)
    adx_5m = d5m.get("adx") or 20.0

    # Get config thresholds
    try:
        import config
        adx_trend_thresh = getattr(config, "REGIME_ADX_TREND_THRESHOLD", 25)
        adx_chop_thresh = getattr(config, "REGIME_ADX_CHOP_THRESHOLD", 20)
    except Exception:
        adx_trend_thresh = 25
        adx_chop_thresh = 20

    # ── Classification logic (priority order) ─────────────────────

    # 1. CAPITULATION: Extreme fear conditions
    if daily_rsi < 22 and vol_surge_5m and daily_trend == "bearish":
        return _validate({
            "regime": "CAPITULATION",
            "confidence": 0.80,
            "strategy_bias": "flat",  # spot bot = stay flat in capitulation
            "position_size_multiplier": 0.3,
            "reasoning": f"Extreme RSI {daily_rsi:.0f} + volume spike + bearish structure",
        })

    # 2. STRONG_TREND_DOWN: Full bearish alignment
    if bearish_stack and daily_adx > adx_trend_thresh and daily_trend == "bearish":
        return _validate({
            "regime": "STRONG_TREND_DOWN",
            "confidence": 0.85,
            "strategy_bias": "flat",  # spot bot can't short
            "position_size_multiplier": 0.0,
            "reasoning": f"Bearish EMA stack + ADX {daily_adx:.0f} + daily downtrend",
        })

    # 3. DISTRIBUTION: Bullish structure but weakening
    if (
        daily_trend == "bullish"
        and daily_rsi > 65
        and vol_trend == "decreasing"
        and h4_adx < adx_trend_thresh
    ):
        return _validate({
            "regime": "DISTRIBUTION",
            "confidence": 0.70,
            "strategy_bias": "flat",
            "position_size_multiplier": 0.3,
            "reasoning": f"Bullish but RSI {daily_rsi:.0f} high + volume declining + ADX fading",
        })

    # 4. STRONG_TREND_UP: Full bullish alignment
    if bullish_stack and daily_adx > adx_trend_thresh and daily_trend == "bullish":
        conf = 0.85
        if h4_trend == "bullish" and h1_trend == "bullish":
            conf = 0.92  # All timeframes aligned
        return _validate({
            "regime": "STRONG_TREND_UP",
            "confidence": conf,
            "strategy_bias": "trend_follow",
            "position_size_multiplier": 1.3,
            "reasoning": f"Bullish EMA stack + ADX {daily_adx:.0f} + aligned timeframes",
        })

    # 5. WEAK_TREND_UP: Bullish but not fully aligned
    if daily_trend == "bullish" and daily_adx > adx_chop_thresh:
        return _validate({
            "regime": "WEAK_TREND_UP",
            "confidence": 0.65,
            "strategy_bias": "trend_follow",
            "position_size_multiplier": 0.8,
            "reasoning": f"Bullish daily + ADX {daily_adx:.0f} but EMA stack not fully aligned",
        })

    # 6. CHOP: ADX too low across timeframes = no direction
    if daily_adx < adx_chop_thresh and h4_adx < adx_chop_thresh:
        return _validate({
            "regime": "CHOP",
            "confidence": 0.75,
            "strategy_bias": "flat",
            "position_size_multiplier": 0.0,
            "reasoning": f"ADX {daily_adx:.0f}/{h4_adx:.0f} below {adx_chop_thresh} — no direction",
        })

    # 7. RANGING: Default when conditions are moderate
    return _validate({
        "regime": "RANGING",
        "confidence": 0.60,
        "strategy_bias": "mean_revert",
        "position_size_multiplier": 0.6,
        "reasoning": f"Moderate ADX {daily_adx:.0f} + mixed signals — treating as range",
    })


# ---------------------------------------------------------------------------
# AI-powered regime classifier (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_regime_signal(mtf_indicators: dict) -> dict:
    """Call Claude API for regime classification. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic

        user_prompt = (
            "Classify the current market regime based on this multi-timeframe data:\n"
            f"{json.dumps(mtf_indicators, indent=2)}"
        )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_regime_signal(mtf_indicators)

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("Calling Claude regime agent ...")
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=300,
            temperature=0.1,
            system=REGIME_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text
        logger.info("Regime agent raw response: %s", raw_text)

        parsed = _extract_json(raw_text)
        result = _validate(parsed)

        logger.info(
            "Regime: %s (confidence %.2f, bias=%s)",
            result["regime"], result["confidence"], result["strategy_bias"],
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("Regime agent JSON parse error: %s — falling back to local", exc)
        return _local_regime_signal(mtf_indicators)

    except Exception as exc:  # noqa: BLE001
        logger.error("Regime agent API error: %s — falling back to local", exc)
        return _local_regime_signal(mtf_indicators)


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode (with caching)
# ---------------------------------------------------------------------------
def run_regime_agent(mtf_indicators: dict, force: bool = False) -> dict:
    """Classify the current market regime.

    Automatically uses local rules (FREE) or Claude API based on
    ``config.USE_AI_AGENTS``.  Results are cached for
    ``config.REGIME_CACHE_SECONDS`` (default 15 min).

    Parameters
    ----------
    mtf_indicators : dict
        ``{"5m": {...}, "1h": {...}, "4h": {...}, "1d": {...}}``
    force : bool
        If True, bypass the cache and reclassify.

    Returns
    -------
    dict
        ``{"regime", "confidence", "strategy_bias",
          "position_size_multiplier", "reasoning"}``
        Always returns a valid dict — never raises.
    """
    global _cached_regime, _cached_regime_ts

    # Return cached result if still fresh
    if not force and _is_cache_valid():
        logger.info(
            "Regime (cached): %s (age %.0fs)",
            _cached_regime["regime"],
            time.time() - _cached_regime_ts,
        )
        return _cached_regime

    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        result = _ai_regime_signal(mtf_indicators)
    else:
        result = _local_regime_signal(mtf_indicators)

    # Cache the result
    _cached_regime = result
    _cached_regime_ts = time.time()

    logger.info(
        "Regime (fresh): %s | confidence=%.2f | bias=%s | multiplier=%.2f",
        result["regime"], result["confidence"],
        result["strategy_bias"], result["position_size_multiplier"],
    )
    return result
"""
Description: Market Regime Detection Agent with 7 regimes, EMA stack analysis,
ADX-based classification, caching, and dual local/AI modes.
"""
