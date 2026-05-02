"""
Agent Router — AI-powered trade analysis via Anthropic Claude.
================================================================
Provides endpoints for the AI Trade Analyst panel:
  POST /api/agent/analyze           — run analysis by type
  POST /api/agent/analyze-trade/ID  — single trade deep-dive
  GET  /api/agent/daily-summary     — cached daily review
"""

import json
import logging
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import ANTHROPIC_API_KEY
from backend.services.db import fetch_all, fetch_one, fetch_scalar
from backend.services.analytics import (
    compute_summary,
    compute_equity_curve,
    compute_hourly_breakdown,
    compute_win_streak,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent"])

# ── In-memory cache (simple dict, 1-hour TTL) ──────────────────────
_daily_cache: dict = {"result": None, "expires_at": 0}

# ── Claude system prompt ───────────────────────────────────────────
ANALYST_SYSTEM_PROMPT = """You are a senior quantitative trading analyst reviewing the performance of an automated BTC/USDT paper trading bot running on Binance. The bot uses a trend-following strategy with EMA crossovers, RSI, and MACD signals, with a 2% risk-per-trade rule.

Your job is to give the bot's operator a clear, honest, and actionable performance review. You are direct — if results are poor, you say so plainly. If they are good, you explain why and what could make them better.

ANALYSIS STRUCTURE (always follow this):
1. HEADLINE: One sentence summarizing the most important finding (positive or negative)
2. PERFORMANCE: What the numbers actually mean (not just restating them)
3. PATTERN ANALYSIS: What patterns you see in winning vs losing trades
4. RISK ASSESSMENT: Is the risk management working? Rate 1–10 (10 = excellent risk control)
5. RECOMMENDATIONS: Exactly 3 specific, actionable changes to improve results. Not vague advice — specific rule changes.
6. WATCH OUT FOR: One specific risk or pattern the operator should monitor closely

Format your response in clean markdown with these exact section headers.
Be concise — the operator needs clarity, not a wall of text."""

TRADE_ANALYSIS_PROMPT = """You are a senior quantitative trading analyst. You are given a single trade from an automated BTC/USDT paper trading bot.

Analyze why this trade resulted in a {outcome}. Examine:
- The entry price and timing
- Market conditions at the time
- What the strategy could have done differently

Be specific and actionable. Provide 2-3 concrete lessons learned."""


# ── Pydantic models ────────────────────────────────────────────────
class AnalyzeRequest(BaseModel):
    analysis_type: str = "daily_review"


class AnalyzeResponse(BaseModel):
    analysis: str
    recommendations: list[str]
    risk_score: int
    generated_at: str


class TradeAnalysisResponse(BaseModel):
    explanation: str
    lessons: list[str]


# ── Helpers ─────────────────────────────────────────────────────────
def _get_client():
    """Create an Anthropic client. Returns None if no API key is configured."""
    if not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as exc:
        logger.error("Failed to create Anthropic client: %s", exc)
        return None


def _fetch_data_for_analysis(analysis_type: str) -> dict:
    """Fetch the relevant trading data from DB based on analysis type."""

    data = {}

    if analysis_type == "daily_review":
        # Today's trades + summary + last 5 decisions
        all_trades = fetch_all(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
        )
        data["summary"] = compute_summary(all_trades)
        data["today_trades"] = fetch_all(
            "SELECT * FROM trades ORDER BY timestamp DESC LIMIT 20"
        )
        data["recent_decisions"] = fetch_all(
            "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 5"
        )
        data["total_trades_count"] = len(all_trades)

    elif analysis_type == "losing_streak":
        # Last N trades to find consecutive losses
        recent = fetch_all(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp DESC LIMIT 20"
        )
        data["recent_trades"] = recent
        data["streak_info"] = compute_win_streak(recent)

    elif analysis_type == "strategy_check":
        # Last 7 days of trades + hourly breakdown
        all_trades = fetch_all(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
        )
        data["summary"] = compute_summary(all_trades)
        data["hourly_breakdown"] = compute_hourly_breakdown(all_trades)
        data["streak_info"] = compute_win_streak(all_trades)
        data["recent_decisions"] = fetch_all(
            "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 20"
        )

    elif analysis_type == "full_audit":
        # Everything
        all_trades = fetch_all(
            "SELECT * FROM trades WHERE pnl IS NOT NULL ORDER BY timestamp ASC"
        )
        data["summary"] = compute_summary(all_trades)
        data["equity_curve"] = compute_equity_curve(all_trades)
        data["hourly_breakdown"] = compute_hourly_breakdown(all_trades)
        data["streak_info"] = compute_win_streak(all_trades)
        data["all_decisions"] = fetch_all(
            "SELECT * FROM agent_decisions ORDER BY timestamp DESC LIMIT 50"
        )
        # Rejection reasons breakdown
        rejections = fetch_all(
            "SELECT reason, COUNT(*) as count FROM agent_decisions "
            "WHERE approved = 0 GROUP BY reason ORDER BY count DESC LIMIT 10"
        )
        data["rejection_reasons"] = rejections

    return data


def _parse_recommendations(text: str) -> list[str]:
    """Extract recommendation lines from the Claude response."""
    recommendations = []
    in_recs = False
    for line in text.split("\n"):
        stripped = line.strip()
        # Detect the recommendations section
        if "RECOMMENDATIONS" in stripped.upper() or "recommendations" in stripped.lower():
            in_recs = True
            continue
        if in_recs:
            # Stop at the next section header
            if stripped.startswith("#") or "WATCH OUT" in stripped.upper():
                break
            # Extract numbered or bulleted items
            if re.match(r"^(\d+[\.\)]\s*|-\s*|\*\s*)", stripped):
                clean = re.sub(r"^(\d+[\.\)]\s*|-\s*|\*\s*)", "", stripped).strip()
                if clean:
                    recommendations.append(clean)
    return recommendations[:5]  # Cap at 5


def _parse_risk_score(text: str) -> int:
    """Extract risk score (1-10) from the Claude response."""
    # Look for patterns like "Rate 7/10", "7/10", "Risk: 7/10"
    patterns = [
        r"[Rr]ate[d]?\s*[:=]?\s*(\d{1,2})\s*/\s*10",
        r"(\d{1,2})\s*/\s*10",
        r"[Rr]isk\s*[Ss]core\s*[:=]?\s*(\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            score = int(match.group(1))
            if 1 <= score <= 10:
                return score
    return 5  # Default middle score


# ── Routes ──────────────────────────────────────────────────────────
@router.post("/agent/analyze", response_model=AnalyzeResponse)
async def analyze_trades(body: AnalyzeRequest):
    """Run an AI-powered analysis of trading performance."""

    valid_types = {"daily_review", "losing_streak", "strategy_check", "full_audit"}
    if body.analysis_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid analysis_type. Must be one of: {', '.join(valid_types)}"
        )

    client = _get_client()
    if client is None:
        return AnalyzeResponse(
            analysis="**API Key Not Configured**\n\nThe Anthropic API key is not set. "
                     "Please add `ANTHROPIC_API_KEY` to your `.env` file on the server.\n\n"
                     "Get your key at: [console.anthropic.com](https://console.anthropic.com)",
            recommendations=["Add ANTHROPIC_API_KEY to your .env file"],
            risk_score=0,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    # Fetch data
    data = _fetch_data_for_analysis(body.analysis_type)

    # Build user prompt
    user_prompt = (
        f"Analysis type: {body.analysis_type}\n\n"
        f"Trading data:\n{json.dumps(data, indent=2, default=str)}"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=ANALYST_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        analysis_text = response.content[0].text

        # Parse structured data from response
        recommendations = _parse_recommendations(analysis_text)
        risk_score = _parse_risk_score(analysis_text)
        generated_at = datetime.now(timezone.utc).isoformat()

        result = AnalyzeResponse(
            analysis=analysis_text,
            recommendations=recommendations,
            risk_score=risk_score,
            generated_at=generated_at,
        )

        # Cache if it's a daily review
        if body.analysis_type == "daily_review":
            _daily_cache["result"] = result.model_dump()
            _daily_cache["expires_at"] = time.time() + 3600  # 1 hour

        return result

    except Exception as exc:
        logger.error("Claude API error: %s", exc)
        return AnalyzeResponse(
            analysis=f"**Analysis Failed**\n\nClaude API returned an error:\n```\n{str(exc)[:300]}\n```\n\n"
                     "Check your API key and account credits at console.anthropic.com",
            recommendations=[],
            risk_score=0,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


@router.post("/agent/analyze-trade/{trade_id}", response_model=TradeAnalysisResponse)
async def analyze_single_trade(trade_id: int):
    """Analyze a specific trade — why it won or lost."""

    client = _get_client()
    if client is None:
        return TradeAnalysisResponse(
            explanation="Anthropic API key not configured. Add ANTHROPIC_API_KEY to .env.",
            lessons=["Configure your API key first"],
        )

    # Fetch the specific trade
    trade = fetch_one("SELECT * FROM trades WHERE id = ?", (trade_id,))
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    pnl = trade.get("pnl", 0) or 0
    outcome = "profit" if pnl > 0 else "loss" if pnl < 0 else "break-even"

    user_prompt = (
        f"Analyze this specific trade:\n{json.dumps(trade, indent=2, default=str)}\n\n"
        f"The trade resulted in a {outcome} of ${pnl:.2f}.\n"
        f"Why did this happen? What could the strategy have done differently?"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=600,
            system=TRADE_ANALYSIS_PROMPT.format(outcome=outcome),
            messages=[{"role": "user", "content": user_prompt}],
        )

        explanation = response.content[0].text

        # Extract lessons (bulleted/numbered items)
        lessons = []
        for line in explanation.split("\n"):
            stripped = line.strip()
            if re.match(r"^(\d+[\.\)]\s*|-\s*|\*\s*)", stripped):
                clean = re.sub(r"^(\d+[\.\)]\s*|-\s*|\*\s*)", "", stripped).strip()
                if clean and len(clean) > 10:
                    lessons.append(clean)

        return TradeAnalysisResponse(
            explanation=explanation,
            lessons=lessons[:3],
        )

    except Exception as exc:
        logger.error("Claude API error (trade analysis): %s", exc)
        return TradeAnalysisResponse(
            explanation=f"Analysis failed: {str(exc)[:200]}",
            lessons=[],
        )


@router.get("/agent/daily-summary")
async def daily_summary():
    """Return cached daily review, or generate a fresh one."""

    # Check cache
    if _daily_cache["result"] and time.time() < _daily_cache["expires_at"]:
        result = dict(_daily_cache["result"])
        result["cached"] = True
        return result

    # No valid cache — generate fresh analysis
    client = _get_client()
    if client is None:
        return {
            "analysis": "API key not configured. Add ANTHROPIC_API_KEY to .env to enable AI analysis.",
            "recommendations": [],
            "risk_score": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        }

    # Trigger a fresh daily review
    req = AnalyzeRequest(analysis_type="daily_review")
    result = await analyze_trades(req)
    response = result.model_dump()
    response["cached"] = False
    return response
