"""
Multi-Timeframe Confluence Agent
==================================
Evaluates whether all timeframes agree on direction before allowing
a trade.  Professional "top-down analysis" — the daily sets the bias,
4H sets the setup, 1H/5M give the entry.

Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic scoring.
  • AI mode: Calls Claude API for analysis.

Never crashes — returns a safe trade_approved=False on any failure.
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
MTF_SYSTEM_PROMPT = """You are a multi-timeframe technical analyst. Evaluate whether timeframes are aligned for a trade.

TIMEFRAME HIERARCHY (in order of authority):
1. Daily (1D): Sets the MASTER BIAS — this overrides everything.
2. 4-Hour (4H): Sets the TRADE SETUP — defines the swing structure.
3. 1-Hour (1H): Sets the ENTRY TRIGGER — when to actually enter.
4. 5-Minute (5M): Fine-tunes the ENTRY — reduces slippage.

CONFLUENCE SCORING:
- Score each timeframe: +1 bullish, -1 bearish, 0 neutral
- Total score: -4 to +4
- Score >= +2: BUY bias approved
- Score <= -2: SELL bias approved
- Score -1 to +1: FLAT — no trade (conflicting timeframes)

WEIGHT the daily 2x (it counts as two votes).

For each timeframe analyze: EMA alignment, trend structure (HH/HL or LH/LL), RSI momentum, volume confirmation.

Respond ONLY in JSON:
{"confluence_score": int(-4 to 4), "daily_bias": "bull|bear|neutral", "4h_structure": "uptrend|downtrend|ranging", "trade_approved": bool, "entry_timeframe_ready": bool, "blocking_reason": "..." or null}"""

# ---------------------------------------------------------------------------
# Safe default
# ---------------------------------------------------------------------------
SAFE_DEFAULT = {
    "confluence_score": 0,
    "daily_bias": "neutral",
    "4h_structure": "ranging",
    "trade_approved": False,
    "entry_timeframe_ready": False,
    "blocking_reason": "MTF agent encountered an error -- defaulting to FLAT",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VALID_BIASES = {"bull", "bear", "neutral"}
VALID_STRUCTURES = {"uptrend", "downtrend", "ranging"}


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
    score = parsed.get("confluence_score", 0)
    try:
        score = int(score)
    except (TypeError, ValueError):
        score = 0
    score = max(-4, min(4, score))

    daily_bias = str(parsed.get("daily_bias", "neutral")).lower()
    if daily_bias not in VALID_BIASES:
        daily_bias = "neutral"

    structure = str(parsed.get("4h_structure", "ranging")).lower()
    if structure not in VALID_STRUCTURES:
        structure = "ranging"

    trade_approved = bool(parsed.get("trade_approved", False))
    entry_ready = bool(parsed.get("entry_timeframe_ready", False))
    blocking = parsed.get("blocking_reason")
    if blocking is not None:
        blocking = str(blocking)[:150]

    return {
        "confluence_score": score,
        "daily_bias": daily_bias,
        "4h_structure": structure,
        "trade_approved": trade_approved,
        "entry_timeframe_ready": entry_ready,
        "blocking_reason": blocking,
    }


# ---------------------------------------------------------------------------
# Timeframe scoring logic
# ---------------------------------------------------------------------------
def _score_timeframe(indicators: dict) -> int:
    """Score a single timeframe: +1 bullish, -1 bearish, 0 neutral.

    Uses EMA trend, RSI momentum, and MACD histogram.
    Requires at least 2 of 3 signals to agree for a directional score.
    """
    if not indicators or indicators.get("close") is None:
        return 0

    bullish_votes = 0
    bearish_votes = 0

    # 1. EMA trend
    trend = indicators.get("trend", "neutral")
    if trend == "bullish":
        bullish_votes += 1
    elif trend == "bearish":
        bearish_votes += 1

    # 2. RSI momentum
    rsi = indicators.get("rsi") or 50.0
    if rsi > 55:
        bullish_votes += 1
    elif rsi < 45:
        bearish_votes += 1

    # 3. MACD histogram
    macd_hist = indicators.get("macd_hist") or 0.0
    if macd_hist > 0:
        bullish_votes += 1
    elif macd_hist < 0:
        bearish_votes += 1

    # Need 2+ votes for a directional score
    if bullish_votes >= 2:
        return 1
    elif bearish_votes >= 2:
        return -1
    return 0


# ---------------------------------------------------------------------------
# LOCAL rule-based MTF confluence (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_mtf_signal(mtf_indicators: dict) -> dict:
    """Score all timeframes and determine if they're aligned for a trade.

    Parameters
    ----------
    mtf_indicators : dict
        ``{"5m": {...}, "1h": {...}, "4h": {...}, "1d": {...}}``
    """
    d5m = mtf_indicators.get("5m", {})
    d1h = mtf_indicators.get("1h", {})
    d4h = mtf_indicators.get("4h", {})
    d1d = mtf_indicators.get("1d", {})

    # Score each timeframe
    score_1d = _score_timeframe(d1d)
    score_4h = _score_timeframe(d4h)
    score_1h = _score_timeframe(d1h)
    score_5m = _score_timeframe(d5m)

    # Daily is weighted 2x (counts as two votes)
    confluence_score = (score_1d * 2) + score_4h + score_1h + score_5m

    logger.debug(
        "MTF scores: 1D=%+d(×2), 4H=%+d, 1H=%+d, 5M=%+d → total=%+d",
        score_1d, score_4h, score_1h, score_5m, confluence_score,
    )

    # Determine daily bias
    daily_trend = d1d.get("trend", "neutral")
    if daily_trend == "bullish":
        daily_bias = "bull"
    elif daily_trend == "bearish":
        daily_bias = "bear"
    else:
        daily_bias = "neutral"

    # 4H structure
    h4_trend = d4h.get("trend", "neutral")
    h4_adx = d4h.get("adx") or 20.0
    if h4_trend == "bullish" and h4_adx > 20:
        h4_structure = "uptrend"
    elif h4_trend == "bearish" and h4_adx > 20:
        h4_structure = "downtrend"
    else:
        h4_structure = "ranging"

    # Trade approval logic
    trade_approved = False
    entry_ready = False
    blocking_reason = None

    if confluence_score >= 2:
        trade_approved = True
        # Entry timeframe ready if 1H and 5M also agree
        entry_ready = score_1h >= 0 and score_5m >= 0
        if not entry_ready:
            blocking_reason = "Higher timeframes aligned but entry TF not ready yet"
    elif confluence_score <= -2:
        # For a spot bot, bearish confluence means FLAT (can't short)
        trade_approved = False
        blocking_reason = f"Bearish confluence (score={confluence_score}) — spot bot stays flat"
    else:
        blocking_reason = f"Conflicting timeframes (score={confluence_score}) — no clear direction"

    result = {
        "confluence_score": confluence_score,
        "daily_bias": daily_bias,
        "4h_structure": h4_structure,
        "trade_approved": trade_approved,
        "entry_timeframe_ready": entry_ready,
        "blocking_reason": blocking_reason,
    }

    logger.info(
        "MTF confluence: score=%+d | daily=%s | 4H=%s | approved=%s",
        confluence_score, daily_bias, h4_structure, trade_approved,
    )
    return result


# ---------------------------------------------------------------------------
# AI-powered MTF agent (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_mtf_signal(mtf_indicators: dict) -> dict:
    """Call Claude API for MTF analysis. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic

        user_prompt = (
            "Evaluate multi-timeframe confluence for this market data:\n"
            f"{json.dumps(mtf_indicators, indent=2)}"
        )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_mtf_signal(mtf_indicators)

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("Calling Claude MTF agent ...")
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=300,
            temperature=0.1,
            system=MTF_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text
        logger.info("MTF agent raw response: %s", raw_text)

        parsed = _extract_json(raw_text)
        result = _validate(parsed)

        logger.info(
            "MTF confluence (AI): score=%+d, approved=%s",
            result["confluence_score"], result["trade_approved"],
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("MTF agent JSON parse error: %s — falling back to local", exc)
        return _local_mtf_signal(mtf_indicators)

    except Exception as exc:  # noqa: BLE001
        logger.error("MTF agent API error: %s — falling back to local", exc)
        return _local_mtf_signal(mtf_indicators)


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode
# ---------------------------------------------------------------------------
def run_mtf_agent(mtf_indicators: dict) -> dict:
    """Evaluate multi-timeframe confluence for the given data.

    Automatically uses local rules (FREE) or Claude API based on
    ``config.USE_AI_AGENTS``.  Falls back to local on any AI failure.

    Parameters
    ----------
    mtf_indicators : dict
        ``{"5m": {...}, "1h": {...}, "4h": {...}, "1d": {...}}``

    Returns
    -------
    dict
        ``{"confluence_score", "daily_bias", "4h_structure",
          "trade_approved", "entry_timeframe_ready", "blocking_reason"}``
        Always returns a valid dict — never raises.
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_mtf_signal(mtf_indicators)
    else:
        return _local_mtf_signal(mtf_indicators)
