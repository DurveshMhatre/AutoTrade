"""
News & Social Sentiment NLP Agent
===================================
Analyzes crypto news headlines to gauge market sentiment and identify major catalysts.
Weighs sources by credibility and categories by impact.

Supports TWO modes:
  • LOCAL mode (default, FREE): Keyword-based heuristic scoring.
  • AI mode: Calls Claude API for deep NLP and historical pattern matching.
"""

import json
import logging
import os
import re

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
NEWS_SYSTEM_PROMPT = """You are a professional crypto news analyst. You have traded through every major crypto cycle.

Given recent crypto news headlines, assess the market impact:

HEADLINE IMPACT SCORING:
- Score each headline -5 (very bearish) to +5 (very bullish).
- Weight by SOURCE: Bloomberg/Reuters (1.5x), CoinDesk (1.0x), Twitter (0.7x).
- Weight by CATEGORY: Regulatory (2.0x), Institutional (1.5x), Hack/exploit (2.5x).

TRADING ACTION:
- Weighted average > +2: boost
- Weighted average < -2: reduce
- Any single headline scores +/-5 (major event): pause

Respond ONLY in JSON:
{"headline_count": int, "weighted_sentiment_score": float, "sentiment_label": "very_bullish|bullish|neutral|bearish|very_bearish", "major_event_detected": bool, "major_event_detail": "str or null", "historical_pattern_match": "str or null", "trading_action": "boost|neutral|reduce|pause", "key_headline": "most impactful headline"}"""

SAFE_DEFAULT = {
    "headline_count": 0,
    "weighted_sentiment_score": 0.0,
    "sentiment_label": "neutral",
    "major_event_detected": False,
    "major_event_detail": None,
    "historical_pattern_match": None,
    "trading_action": "neutral",
    "key_headline": "No news available",
}

VALID_LABELS = {"very_bullish", "bullish", "neutral", "bearish", "very_bearish"}
VALID_ACTIONS = {"boost", "neutral", "reduce", "pause"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found")
    return json.loads(cleaned[start:end])

def _validate(parsed: dict) -> dict:
    score = float(parsed.get("weighted_sentiment_score") or 0.0)
    score = max(-5.0, min(5.0, score))
    
    label = str(parsed.get("sentiment_label", "neutral")).lower()
    if label not in VALID_LABELS:
        label = "neutral"
        
    action = str(parsed.get("trading_action", "neutral")).lower()
    if action not in VALID_ACTIONS:
        action = "neutral"
        
    return {
        "headline_count": int(parsed.get("headline_count") or 0),
        "weighted_sentiment_score": round(score, 2),
        "sentiment_label": label,
        "major_event_detected": bool(parsed.get("major_event_detected")),
        "major_event_detail": parsed.get("major_event_detail"),
        "historical_pattern_match": parsed.get("historical_pattern_match"),
        "trading_action": action,
        "key_headline": str(parsed.get("key_headline", ""))[:150],
    }

# ---------------------------------------------------------------------------
# LOCAL Heuristic News Scoring (FREE)
# ---------------------------------------------------------------------------
def _local_news_signal(headlines: list[dict]) -> dict:
    if not headlines:
        return SAFE_DEFAULT
        
    bullish_keywords = {"etf", "approval", "institutional", "adoption", "rally", "surge", "bullish", "milestone"}
    bearish_keywords = {"hack", "exploit", "ban", "regulation", "sec", "crash", "scam", "fraud", "bearish"}
    major_keywords = {"hack", "ban", "sec", "approval"}
    
    total_score = 0.0
    major_event = False
    key_headline = headlines[0].get("title", "") if headlines else ""
    max_impact = 0.0
    
    for hl in headlines:
        title = hl.get("title", "").lower()
        score = 0.0
        
        # Base scoring
        for kw in bullish_keywords:
            if kw in title: score += 1.0
        for kw in bearish_keywords:
            if kw in title: score -= 1.0
            
        # Source weighting
        source = hl.get("source", "").lower()
        weight = 1.0
        if source in ["bloomberg", "reuters", "wsj"]: weight = 1.5
        elif source in ["twitter", "x"]: weight = 0.7
        
        # Category weighting (Major events)
        for kw in major_keywords:
            if kw in title:
                weight *= 2.0
                if abs(score) > 0:
                    major_event = True
                
        final_score = score * weight
        total_score += final_score
        
        if abs(final_score) > abs(max_impact):
            max_impact = final_score
            key_headline = hl.get("title", "")

    avg_score = total_score / len(headlines) if headlines else 0.0
    avg_score = max(-5.0, min(5.0, avg_score))
    
    label = "neutral"
    action = "neutral"
    
    if avg_score >= 2.0:
        label = "bullish"
        action = "boost"
    elif avg_score <= -2.0:
        label = "bearish"
        action = "reduce"
        
    if major_event and avg_score < -2.0:
        action = "pause"
        label = "very_bearish"

    return _validate({
        "headline_count": len(headlines),
        "weighted_sentiment_score": avg_score,
        "sentiment_label": label,
        "major_event_detected": major_event,
        "trading_action": action,
        "key_headline": key_headline,
    })

# ---------------------------------------------------------------------------
# AI-Powered News Analysis
# ---------------------------------------------------------------------------
def _ai_news_signal(headlines: list[dict]) -> dict:
    if not headlines:
        return SAFE_DEFAULT
        
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return _local_news_signal(headlines)
            
        payload = {"headlines": headlines}
        
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=250,
            temperature=0.1,
            system=NEWS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}]
        )
        
        return _validate(_extract_json(response.content[0].text))
    except Exception as exc:
        logger.error(f"News AI agent error: {exc}")
        return _local_news_signal(headlines)

# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------
def run_news_agent(headlines: list[dict]) -> dict:
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_news_signal(headlines)
    return _local_news_signal(headlines)
