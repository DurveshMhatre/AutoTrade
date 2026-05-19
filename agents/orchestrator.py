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
ORCHESTRATOR_SYSTEM_PROMPT = """You are the chief trading officer of an automated crypto fund with 10+ years of experience. You synthesize all available intelligence to make the final trade decision.

You receive outputs from 7 specialized agents:
1. Regime Agent: what market mode are we in?
2. MTF Confluence Agent: do all timeframes agree?
3. Trend Signal Agent: EMA/RSI/MACD signal
4. S&R Level Agent: entry quality near key levels?
5. Volume/Order Flow Agent: institutional footprint?
6. On-Chain/Sentiment Agent: whale activity + fear/greed
7. News Agent: major catalyst or headwind?

DECISION FRAMEWORK (how an experienced trader weighs these):

VETO CONDITIONS — any single one blocks the trade entirely:
- Regime = CHOP or DISTRIBUTION: NO TRADE
- MTF confluence score = 0 or negative: NO TRADE
- News agent detects major_event with negative sentiment: NO TRADE

CONVICTION SCORING (0 to 100):
Base: 50
+20 if regime is STRONG_TREND (direction matches signal)
+15 if all 3 timeframes aligned (MTF score >= 3)
+10 if entry near strong S/R (entry_quality = "excellent")
+10 if order flow confirms (bid_ask_ratio > 1.4 for buy)
+5  if on-chain sentiment positive (combined_sentiment > 3)
+5  if news tailwind (news action = "boost")
-15 if entering against higher timeframe
-10 if low volume breakout flagged
-5  if news neutral-negative

POSITION SIZE TIER:
- Conviction 80-100: full
- Conviction 60-79: 75%
- Conviction 40-59: 50%
- Below 40: NO TRADE (not enough edge)

Respond ONLY in JSON:
{"trade_approved": bool, "conviction_score": int, "veto_reason": "string or null", "final_signal": "BUY|SELL|FLAT", "position_size_tier": "full|75|50", "summary": "max 30 words of reasoning"}"""

# ---------------------------------------------------------------------------
# Safe default returned on any failure
# ---------------------------------------------------------------------------
SAFE_DEFAULT: dict = {
    "trade_approved": False,
    "conviction_score": 0,
    "veto_reason": "parse_error",
    "final_signal": "FLAT",
    "position_size_tier": "50",
    "summary": "Agent encountered an error.",
}

# ---------------------------------------------------------------------------
# Response validation helpers (AI mode)
# ---------------------------------------------------------------------------


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
    validated["trade_approved"] = bool(decision.get("trade_approved", False))
    validated["conviction_score"] = int(decision.get("conviction_score", 0))
    validated["veto_reason"] = decision.get("veto_reason")
    validated["final_signal"] = decision.get("final_signal", "FLAT")
    validated["position_size_tier"] = str(decision.get("position_size_tier", "50"))
    validated["summary"] = decision.get("summary", "")[:200]
    return validated


# ---------------------------------------------------------------------------
# Squeeze bypass counter — prevents the bot from being permanently stuck
# in low-volatility markets. Every N cycles, allow analysis even if in squeeze.
# ---------------------------------------------------------------------------
_squeeze_block_count = 0
_SQUEEZE_BYPASS_EVERY = 5  # Allow analysis every 5th blocked cycle


# ---------------------------------------------------------------------------
# Phase 1 & 2 VETO checks
# ---------------------------------------------------------------------------
def _check_vetoes(
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
    news_data: dict | None = None,
    active_strategy: dict | None = None,
) -> dict | None:
    """Run all veto checks from the intelligence layer.
    If ANY check fails, return an early rejection payload.
    If ALL pass, return None.
    """
    strategy_type = active_strategy.get("type", "TREND_FOLLOWING") if active_strategy else "TREND_FOLLOWING"

    # ── Regime veto ─────────────────────────────────────────────────
    if regime_data:
        regime = regime_data.get("regime", "")
        if regime in ("CHOP", "DISTRIBUTION"):
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": f"regime_veto_{regime.lower()}",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": f"Trade vetoed: regime is {regime}."
            }
        if regime == "STRONG_TREND_DOWN":
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": "regime_veto_strong_downtrend",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": "Trade vetoed: strong downtrend."
            }
        if regime == "CAPITULATION":
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": "regime_veto_capitulation",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": "Trade vetoed: capitulation detected."
            }

    # ── MTF veto — STRATEGY-AWARE (Bug 1 fix) ────────────────────────
    if mtf_data:
        if strategy_type == "MEAN_REVERSION":
            # For mean-reversion (RANGING), MTF divergence is EXPECTED and ACCEPTABLE
            logger.info("MTF veto: skipped (MEAN_REVERSION strategy)")
        elif not mtf_data.get("trade_approved", False):
            blocking = mtf_data.get("blocking_reason", "timeframes_not_aligned")
            score = mtf_data.get("confluence_score", 0)
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": f"mtf_veto_score_{score}_{blocking[:40]}",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": f"Trade vetoed: MTF score {score}."
            }

    # ── Sentiment veto ───────────────────────────────────────────────
    if sentiment_data:
        bias_adj = sentiment_data.get("trade_bias_adjustment", "neutral")
        combined = sentiment_data.get("combined_sentiment", 0)
        if bias_adj == "flat":
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": f"sentiment_veto_flat_score_{combined}",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": f"Trade vetoed: sentiment flat ({combined})."
            }

    # ── News veto ────────────────────────────────────────────────────
    if news_data:
        if news_data.get("trading_action") == "pause" or news_data.get("major_event_detected"):
            return {
                "trade_approved": False,
                "conviction_score": 0,
                "veto_reason": f"news_veto_major_event_{news_data.get('sentiment_label', '')}",
                "final_signal": "FLAT",
                "position_size_tier": "50",
                "summary": "Trade vetoed due to major news event."
            }

    return None  # No veto — all clear


# ---------------------------------------------------------------------------
# LOCAL rule-based orchestrator (FREE — no API calls)
# ---------------------------------------------------------------------------
def _local_orchestrator(
    market_data: dict,
    portfolio: dict,
    trend_data: dict | None = None,
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
    sr_data: dict | None = None,
    order_flow_data: dict | None = None,
    news_data: dict | None = None,
    active_strategy: dict | None = None,
) -> dict:
    """Evaluate market conditions using the exact same rules from the
    system prompt, implemented as deterministic Python logic.

    Enhanced with Phase 1 & 2 intelligence veto checks and confidence scoring.
    Strategy-aware conviction thresholds and MTF veto bypass (Bug 1 + Bug 4 fix).
    This is 100% FREE — no API calls needed.
    """
    global _squeeze_block_count

    strategy_type = active_strategy.get("type", "TREND_FOLLOWING") if active_strategy else "TREND_FOLLOWING"

    # ── Strategy-aware conviction thresholds (Bug 4 fix) ──────────────
    CONVICTION_THRESHOLDS = {
        "TREND_FOLLOWING": 65,    # High bar — need strong alignment
        "MEAN_REVERSION": 55,    # Lower bar — fewer confirmations needed
        "CAPITULATION_BOUNCE": 60,
        "FLAT": 999,             # Never trade
    }
    min_conviction = CONVICTION_THRESHOLDS.get(strategy_type, 65)

    # ── Phase 1 & 2 veto checks (strategy-aware) ─────────────────────
    veto = _check_vetoes(regime_data, mtf_data, sentiment_data, news_data, active_strategy)
    if veto:
        logger.info(
            "Orchestrator: Phase 1 veto fired — reason=%s",
            veto.get("veto_reason", "unknown"),
        )
        return veto

    trend = trend_data.get("signal", "FLAT") if trend_data else market_data.get("trend", "FLAT").upper()

    # Spot-only bot: SELL signal = flatten existing longs (can't open shorts)
    if trend == "SELL":
        return {
            "trade_approved": True,
            "conviction_score": 60,
            "veto_reason": None,
            "final_signal": "FLATTEN",
            "position_size_tier": "full",
            "summary": "SELL signal: flattening existing longs (spot-only, no short).",
        }

    if trend not in ("BUY",):
        return {
            "trade_approved": False,
            "conviction_score": 0,
            "veto_reason": "no_clear_trend_signal",
            "final_signal": "FLAT",
            "position_size_tier": "50",
            "summary": "No directional trend signal to trade."
        }
        
    conviction = 50

    # ── Conviction boosts/penalties ──────────────────
    regime = regime_data.get("regime") if regime_data else None

    if regime_data:
        if regime == "STRONG_TREND_UP" and trend == "BUY":
            conviction += 20
        elif regime == "STRONG_TREND_DOWN" and trend == "SELL":
            conviction += 20
        elif regime == "WEAK_TREND_UP" and trend == "BUY":
            conviction += 10
        elif regime == "RANGING" and trend == "BUY":
            # Mean reversion BUY in ranging market — give a regime-appropriate boost
            conviction += 15

    # ── MTF scoring — STRATEGY-AWARE (Bug 4 fix) ───────────────────
    if mtf_data:
        if strategy_type != "MEAN_REVERSION":
            # Standard MTF scoring for trend following
            score = mtf_data.get("confluence_score", 0)
            if score >= 3:
                conviction += 15
            elif score >= 1:
                conviction += 5
            elif score < 0:
                conviction -= 15  # Trading against higher timeframe
        else:
            # For mean reversion: MTF divergence = EXPECTED, do NOT penalize
            # Instead, check that daily is NOT strongly trending in opposite direction
            daily_bias = mtf_data.get("daily_bias", "neutral")
            final_signal_type = trend_data.get("signal", "HOLD") if trend_data else "HOLD"

            if final_signal_type == "BUY" and daily_bias == "bear":
                conviction -= 10  # Slight penalty — mean reversion against daily trend
            elif final_signal_type == "SELL" and daily_bias == "bull":
                conviction -= 10
            else:
                conviction += 5   # Neutral daily = fine for mean reversion

    # ── Mean-reversion specific boosts (Bug 4 fix) ─────────────────
    if strategy_type == "MEAN_REVERSION":
        # Use the signal from mean_reversion_agent if available
        mr_signal = trend_data  # In ranging mode, trend_data IS the mean_reversion signal

        # S&R distance boost (the closer to S&R, the better)
        dist_to_sr = mr_signal.get("distance_to_sr_pct", 999) if mr_signal else 999
        if dist_to_sr < 0.2:
            conviction += 15  # Excellent — right at the level
        elif dist_to_sr < 0.4:
            conviction += 10  # Good
        elif dist_to_sr < 0.8:
            conviction += 5   # Fair
        else:
            conviction -= 20  # Too far from S&R — not a mean reversion setup

        # R:R quality boost
        rr = mr_signal.get("rr_ratio", 0) if mr_signal else 0
        if rr >= 2.5:
            conviction += 10
        elif rr >= 1.5:
            conviction += 5
        else:
            conviction -= 15  # Bad R:R — never trade mean reversion with R:R < 1.5

    if sr_data:
        entry_qual = sr_data.get("entry_quality")
        if entry_qual == "excellent":
            conviction += 10
        elif entry_qual == "poor":
            conviction -= 10
            
    if order_flow_data:
        ratio = order_flow_data.get("bid_ask_ratio", 1.0)
        if trend == "BUY" and ratio > 1.4:
            conviction += 10
        elif trend == "SELL" and ratio < 0.7:
            conviction += 10
            
    if sentiment_data:
        comb = sentiment_data.get("combined_sentiment", 0)
        if comb > 3 and trend == "BUY":
            conviction += 5
        elif comb < -3 and trend == "SELL":
            conviction += 5
            
    if news_data:
        trading_action = news_data.get("trading_action")
        if trading_action == "boost":
            conviction += 5
        elif trading_action == "reduce":
            conviction -= 5

    conviction = max(0, min(100, int(conviction)))

    if conviction >= 80:
        tier = "full"
    elif conviction >= 60:
        tier = "75"
    elif conviction >= 40:
        tier = "50"
    else:
        return {
            "trade_approved": False,
            "conviction_score": conviction,
            "veto_reason": "low_conviction",
            "final_signal": "FLAT",
            "position_size_tier": "50",
            "summary": f"Conviction too low ({conviction})."
        }

    # ── Apply strategy-aware minimum conviction (Bug 4 fix) ────────
    trade_approved = conviction >= min_conviction
    if not trade_approved:
        logger.info(
            "Conviction: %d (min=%d for %s) → approved=%s",
            conviction, min_conviction, strategy_type, trade_approved
        )
        return {
            "trade_approved": False,
            "conviction_score": conviction,
            "veto_reason": f"conviction_below_{strategy_type}_minimum",
            "final_signal": "FLAT",
            "position_size_tier": "50",
            "summary": f"Conviction {conviction} below {min_conviction} for {strategy_type}."
        }

    summary = f"Approved {trend} with {conviction} conviction."
    logger.info(
        "Conviction: %d (min=%d for %s) → approved=%s",
        conviction, min_conviction, strategy_type, trade_approved
    )

    return {
        "trade_approved": True,
        "conviction_score": conviction,
        "veto_reason": None,
        "final_signal": trend,
        "position_size_tier": tier,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# AI-powered orchestrator (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_orchestrator(
    market_data: dict,
    portfolio: dict,
    trend_data: dict | None = None,
    regime_data: dict | None = None,
    mtf_data: dict | None = None,
    sentiment_data: dict | None = None,
    sr_data: dict | None = None,
    order_flow_data: dict | None = None,
    news_data: dict | None = None,
    active_strategy: dict | None = None,
) -> dict:
    """Evaluate market conditions using Claude 3 Haiku."""
    try:
        import anthropic

        # ── Phase 1 & 2 veto checks — strategy-aware (Bug 1 fix) ──────────
        veto = _check_vetoes(regime_data, mtf_data, sentiment_data, news_data, active_strategy)
        if veto:
            return veto

        payload = {
            "market_data": market_data,
            "portfolio": portfolio,
            "active_strategy": active_strategy or {},
        }
        
        # Add phase 1 intel
        if regime_data:
            payload["regime_intelligence"] = regime_data
        if mtf_data:
            payload["mtf_intelligence"] = mtf_data
        if sentiment_data:
            payload["sentiment_intelligence"] = sentiment_data
            
        # Add phase 2 intel
        if sr_data:
            payload["sr_intelligence"] = sr_data
        if order_flow_data:
            payload["order_flow_intelligence"] = order_flow_data
        if news_data:
            payload["news_intelligence"] = news_data

        user_prompt = (
            f"Analyze the following market context and determine if we should trade:\n"
            f"{json.dumps(payload, indent=2)}"
        )
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_orchestrator(market_data, portfolio, trend_data, regime_data, mtf_data, sentiment_data, sr_data, order_flow_data, news_data, active_strategy)

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
            result["trade_approved"], result.get("veto_reason"), result.get("conviction_score"),
        )
        return result

    except Exception as exc:
        logger.error("Orchestrator AI agent error: %s", exc)
        return _local_orchestrator(
            market_data, portfolio, trend_data, regime_data, mtf_data, sentiment_data,
            sr_data, order_flow_data, news_data, active_strategy
        )


# ---------------------------------------------------------------------------
# Public entry point — auto-selects local or AI mode
# ---------------------------------------------------------------------------
def run_orchestrator(
    market_data: dict,
    portfolio: dict,
    trend_data: dict = None,
    regime_data: dict = None,
    mtf_data: dict = None,
    sentiment_data: dict = None,
    sr_data: dict = None,
    order_flow_data: dict = None,
    news_data: dict = None,
    active_strategy: dict = None,
) -> dict:
    """Main entry point for the Orchestrator Agent.

    Parameters
    ----------
    market_data : dict
        Current state (bb_position, rsi, trend, macd, etc.)
    portfolio : dict
        Currently open positions, total value, etc.
    trend_data : dict, optional
        Output from Trend Agent
    regime_data : dict, optional
        Output from Regime Agent (from Phase 1)
    mtf_data : dict, optional
        Output from MTF Agent (from Phase 1)
    sentiment_data : dict, optional
        Output from Sentiment Agent (from Phase 1)
    sr_data : dict, optional
        Output from Support/Resistance Agent (from Phase 2)
    order_flow_data : dict, optional
        Output from Order Flow Agent (from Phase 2)
    news_data : dict, optional
        Output from News Agent (from Phase 2)
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_orchestrator(
            market_data, portfolio, trend_data, regime_data, mtf_data, sentiment_data,
            sr_data, order_flow_data, news_data, active_strategy
        )
    else:
        return _local_orchestrator(
            market_data, portfolio, trend_data, regime_data, mtf_data, sentiment_data,
            sr_data, order_flow_data, news_data, active_strategy
        )
