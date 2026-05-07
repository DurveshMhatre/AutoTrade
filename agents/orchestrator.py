"""
Orchestrator Agent — highest-level gate for the automated trading system.
=========================================================================
Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic rule-based evaluation.
  • AI mode: Calls Claude API for market evaluation.

Enhanced in Phase 1 to incorporate:
  • Regime Agent veto (CHOP / DISTRIBUTION → block)
  • MTF Confluence veto (conflicting timeframes → block)
  • Sentiment Agent bias (strong negative → block)

Never crashes — returns a safe default on any failure.
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
ORCHESTRATOR_SYSTEM_PROMPT = """You are the risk-aware orchestrator of an automated crypto trading system trading BTC/USDT on Binance.

Your ONLY job is to decide if market conditions are currently suitable for the trend-following strategy to analyze for a trade. You are NOT deciding whether to buy or sell — that is the trend agent's job.

DECISION CRITERIA — only set analyze=true if ALL of the following are met:
1. Market is not in extreme volatility (ATR is not "high" AND RSI is not above 80 or below 20)
2. There is clear directional bias (trend is "bullish" or "bearish", not "neutral")
3. We are not near a Bollinger Band squeeze (bb_upper - bb_lower > 0.5% of close price)
4. Portfolio has capacity (open_positions < max_positions)
5. We have not hit the daily loss limit (daily_loss_pct < 0.05)

RISK LEVELS:
- low: RSI 35–65, trend clear, volatility normal
- medium: RSI 30–35 or 65–70, or volatility high
- high: RSI < 30 or > 70, extreme volatility, or conflicting signals

Always respond with ONLY valid JSON. No explanation outside the JSON."""

# ---------------------------------------------------------------------------
# Safe default returned on any failure
# ---------------------------------------------------------------------------
SAFE_DEFAULT: dict = {
    "analyze": False,
    "reason": "parse_error",
    "risk_level": "high",
    "confidence": 0.0,
}

# ---------------------------------------------------------------------------
# Response validation helpers (AI mode)
# ---------------------------------------------------------------------------
VALID_RISK_LEVELS = {"low", "medium", "high"}


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from *text*, tolerating markdown fences."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(cleaned[start:end])


def _validate(decision: dict) -> dict:
    """Ensure the response conforms to the expected schema."""
    validated: dict = {}
    validated["analyze"] = bool(decision.get("analyze", False))
    reason = decision.get("reason", "")
    validated["reason"] = str(reason) if reason else "no_reason_given"
    risk = decision.get("risk_level", "high")
    validated["risk_level"] = risk if risk in VALID_RISK_LEVELS else "high"
    try:
        conf = float(decision.get("confidence", 0.0))
        validated["confidence"] = round(max(0.0, min(1.0, conf)), 4)
    except (TypeError, ValueError):
        validated["confidence"] = 0.0
    return validated


# ---------------------------------------------------------------------------
# Squeeze bypass counter — prevents the bot from being permanently stuck
# in low-volatility markets. Every N cycles, allow analysis even if in squeeze.
# ---------------------------------------------------------------------------
_squeeze_block_count = 0
_SQUEEZE_BYPASS_EVERY = 5  # Allow analysis every 5th blocked cycle


# ---------------------------------------------------------------------------
# Phase 1 VETO checks — regime, MTF, sentiment
# ---------------------------------------------------------------------------
def _check_phase1_vetos(
    regime_data: dict | None,
    mtf_data: dict | None,
    sentiment_data: dict | None,
) -> dict | None:
    """Check Phase 1 intelligence agents for veto conditions.

    Returns a rejection dict if any veto fires, or None if all clear.
    """
    # ── Regime veto ─────────────────────────────────────────────────
    if regime_data:
        regime = regime_data.get("regime", "")
        if regime in ("CHOP", "DISTRIBUTION"):
            return {
                "analyze": False,
                "reason": f"regime_veto_{regime.lower()}",
                "risk_level": "high",
                "confidence": 0.1,
            }
        if regime == "STRONG_TREND_DOWN":
            return {
                "analyze": False,
                "reason": "regime_veto_strong_downtrend",
                "risk_level": "high",
                "confidence": 0.1,
            }
        if regime == "CAPITULATION":
            return {
                "analyze": False,
                "reason": "regime_veto_capitulation",
                "risk_level": "high",
                "confidence": 0.15,
            }

    # ── MTF veto ─────────────────────────────────────────────────────
    if mtf_data:
        if not mtf_data.get("trade_approved", False):
            blocking = mtf_data.get("blocking_reason", "timeframes_not_aligned")
            score = mtf_data.get("confluence_score", 0)
            return {
                "analyze": False,
                "reason": f"mtf_veto_score_{score}_{blocking[:40]}",
                "risk_level": "medium",
                "confidence": 0.2,
            }

    # ── Sentiment veto ───────────────────────────────────────────────
    if sentiment_data:
        bias_adj = sentiment_data.get("trade_bias_adjustment", "neutral")
        combined = sentiment_data.get("combined_sentiment", 0)
        if bias_adj == "flat":
            return {
                "analyze": False,
                "reason": f"sentiment_veto_flat_score_{combined}",
                "risk_level": "high",
                "confidence": 0.1,
            }

    return None  # No veto — all clear


# ---------------------------------------------------------------------------
# LOCAL rule-based orchestrator (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_orchestrator(
    market_data: dict,
    portfolio: dict,
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
) -> dict:
    """Evaluate market conditions using the exact same rules from the
    system prompt, implemented as deterministic Python logic.

    Enhanced with Phase 1 intelligence veto checks.
    This is 100% FREE — no API calls needed.
    """
    global _squeeze_block_count

    # ── Phase 1 veto checks (if data provided) ─────────────────────
    veto = _check_phase1_vetos(regime_data, mtf_data, sentiment_data)
    if veto:
        logger.info(
            "Orchestrator: Phase 1 veto fired — reason=%s",
            veto["reason"],
        )
        return veto

    rsi = market_data.get("rsi") or 50.0
    trend = market_data.get("trend", "neutral")
    volatility = market_data.get("volatility", "normal")
    close = market_data.get("close") or 0.0
    bb_upper = market_data.get("bb_upper") or 0.0
    bb_lower = market_data.get("bb_lower") or 0.0
    open_positions = portfolio.get("open_positions", 0)
    max_positions = portfolio.get("max_positions", 3)
    daily_loss_pct = portfolio.get("daily_loss_pct", 0.0)

    # ── Check all 5 conditions ─────────────────────────────────────

    # 1. Not extreme volatility (ATR not high AND RSI not extreme)
    extreme_volatility = volatility == "high" and (rsi > 80 or rsi < 20)
    if extreme_volatility:
        return {
            "analyze": False,
            "reason": "extreme_volatility_with_extreme_rsi",
            "risk_level": "high",
            "confidence": 0.1,
        }

    # RSI extremes alone block
    if rsi > 80 or rsi < 20:
        return {
            "analyze": False,
            "reason": f"rsi_extreme_{rsi:.1f}",
            "risk_level": "high",
            "confidence": 0.15,
        }

    # 2. Clear directional bias
    if trend == "neutral":
        return {
            "analyze": False,
            "reason": "no_clear_trend",
            "risk_level": "medium",
            "confidence": 0.2,
        }

    # 3. Not in a Bollinger Band squeeze
    #    Threshold lowered from 0.5% to 0.2% — BTC at $70k+ means
    #    the old 0.5% ($385 width) was too aggressive for modern
    #    low-volatility BTC regimes. 0.2% ($154 width) is more
    #    realistic and still filters truly flat markets.
    if close > 0:
        bb_width_pct = (bb_upper - bb_lower) / close if close else 0
        logger.debug(
            "BB width: $%.2f (%.3f%% of close) | threshold: 0.200%%",
            bb_upper - bb_lower, bb_width_pct * 100,
        )
        if bb_width_pct < 0.002:  # < 0.2% of close
            _squeeze_block_count += 1
            # Allow analysis every Nth squeeze block so the bot
            # doesn't sit idle for hours in consolidation markets
            if _squeeze_block_count % _SQUEEZE_BYPASS_EVERY == 0:
                logger.info(
                    "Bollinger squeeze detected BUT bypassing (cycle %d) — "
                    "allowing analysis to prevent permanent stall",
                    _squeeze_block_count,
                )
            else:
                return {
                    "analyze": False,
                    "reason": "bollinger_squeeze",
                    "risk_level": "medium",
                    "confidence": 0.25,
                }
        else:
            _squeeze_block_count = 0  # Reset when squeeze ends

    # 4. Portfolio capacity
    if open_positions >= max_positions:
        return {
            "analyze": False,
            "reason": "max_positions_reached",
            "risk_level": "low",
            "confidence": 0.3,
        }

    # 5. Daily loss limit
    if daily_loss_pct >= 0.05:
        return {
            "analyze": False,
            "reason": "daily_loss_limit_hit",
            "risk_level": "high",
            "confidence": 0.1,
        }

    # ── All conditions passed — determine risk level ───────────────

    # Risk level classification
    if 35 <= rsi <= 65 and trend in ("bullish", "bearish") and volatility == "normal":
        risk_level = "low"
    elif (30 <= rsi < 35 or 65 < rsi <= 70) or volatility == "high":
        risk_level = "medium"
    else:
        risk_level = "high"

    # Confidence scoring (base)
    confidence = 0.6
    if risk_level == "low":
        confidence += 0.2
    elif risk_level == "medium":
        confidence += 0.1
    if 45 <= rsi <= 55:
        confidence += 0.1  # RSI in sweet spot
    if volatility == "normal":
        confidence += 0.05

    # ── Phase 1 confidence boosts ──────────────────────────────────
    if regime_data:
        regime = regime_data.get("regime", "")
        if regime in ("STRONG_TREND_UP", "WEAK_TREND_UP"):
            confidence += 0.05  # Favorable regime
        regime_conf = regime_data.get("confidence", 0.0)
        if regime_conf > 0.8:
            confidence += 0.05  # High regime confidence

    if mtf_data:
        score = mtf_data.get("confluence_score", 0)
        if score >= 3:
            confidence += 0.10  # Strong MTF alignment
        elif score >= 2:
            confidence += 0.05
        if mtf_data.get("entry_timeframe_ready", False):
            confidence += 0.05

    if sentiment_data:
        bias_adj = sentiment_data.get("trade_bias_adjustment", "neutral")
        if bias_adj == "boost":
            confidence += 0.05
        elif bias_adj == "reduce":
            confidence -= 0.10

    confidence = round(max(0.0, min(1.0, confidence)), 4)

    reason = f"{trend}_trend_clear_rsi_{rsi:.0f}"

    logger.info(
        "Local orchestrator: analyze=True, risk=%s, conf=%.2f, reason=%s",
        risk_level, confidence, reason,
    )

    return {
        "analyze": True,
        "reason": reason,
        "risk_level": risk_level,
        "confidence": confidence,
    }


# ---------------------------------------------------------------------------
# AI-powered orchestrator (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_orchestrator(
    market_data: dict,
    portfolio: dict,
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
) -> dict:
    """Call Claude API for market evaluation. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic  # lazy import — only loaded when AI mode is on

        # Phase 1 veto checks still run before AI call (save API cost)
        veto = _check_phase1_vetos(regime_data, mtf_data, sentiment_data)
        if veto:
            logger.info(
                "Orchestrator: Phase 1 veto fired (pre-AI) — reason=%s",
                veto["reason"],
            )
            return veto

        user_prompt = (
            f"Market snapshot: {json.dumps(market_data, indent=2)}\n"
            f"Portfolio state: {json.dumps(portfolio, indent=2)}\n"
        )
        if regime_data:
            user_prompt += f"Regime: {json.dumps(regime_data, indent=2)}\n"
        if mtf_data:
            user_prompt += f"MTF Confluence: {json.dumps(mtf_data, indent=2)}\n"
        if sentiment_data:
            user_prompt += f"Sentiment: {json.dumps(sentiment_data, indent=2)}\n"
        user_prompt += "\nDecide now."

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_orchestrator(market_data, portfolio, regime_data, mtf_data, sentiment_data)

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("Calling Claude orchestrator ...")
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=200,
            temperature=0.1,
            system=ORCHESTRATOR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text
        logger.info("Orchestrator raw response: %s", raw_text)

        parsed = _extract_json(raw_text)
        result = _validate(parsed)

        logger.info(
            "Orchestrator decision: analyze=%s, risk=%s, confidence=%.2f",
            result["analyze"], result["risk_level"], result["confidence"],
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("Orchestrator JSON parse error: %s — falling back to local", exc)
        return _local_orchestrator(market_data, portfolio, regime_data, mtf_data, sentiment_data)

    except Exception as exc:  # noqa: BLE001
        logger.error("Orchestrator API error: %s — falling back to local", exc)
        return _local_orchestrator(market_data, portfolio, regime_data, mtf_data, sentiment_data)


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode
# ---------------------------------------------------------------------------
def run_orchestrator(
    market_data: dict,
    portfolio: dict,
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
) -> dict:
    """Decide whether the system should analyze for a trade right now.

    Automatically uses local rules (FREE) or Claude API based on
    ``config.USE_AI_AGENTS``.  Falls back to local on any AI failure.

    Parameters
    ----------
    market_data : dict
        Latest 5M indicator snapshot.
    portfolio : dict
        Current portfolio state.
    regime_data : dict | None
        Output from regime agent (Phase 1).
    mtf_data : dict | None
        Output from MTF confluence agent (Phase 1).
    sentiment_data : dict | None
        Output from sentiment agent (Phase 1).

    Returns
    -------
    dict
        Decision payload: analyze, reason, risk_level, confidence.
        Always returns a valid dict — never raises.
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_orchestrator(market_data, portfolio, regime_data, mtf_data, sentiment_data)
    else:
        return _local_orchestrator(market_data, portfolio, regime_data, mtf_data, sentiment_data)
