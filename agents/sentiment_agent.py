"""
On-Chain + Sentiment Intelligence Agent
=========================================
Fetches real-time on-chain data and news sentiment to detect smart money
movements and market-wide risk shifts.

Data sources (all FREE):
  • Fear & Greed Index — api.alternative.me (no key needed)
  • Binance funding rate — public endpoint (no key needed)
  • CryptoPanic news — free API key OR fallback heuristic

Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Deterministic scoring rules.
  • AI mode: Calls Claude API for news analysis.

Never crashes — returns neutral defaults on any failure.
"""

import json
import logging
import os
import re
import asyncio

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (used only in AI mode — for news analysis)
# ---------------------------------------------------------------------------
SENTIMENT_SYSTEM_PROMPT = """You are a crypto on-chain analyst with expertise in reading smart money behavior.

Analyze the following on-chain data and news headlines and assess market conditions:

ON-CHAIN SIGNALS:
- Fear & Greed Index: Below 20 = extreme fear (often buy opportunity), Above 80 = extreme greed (distribution zone)
- Binance funding rate: Positive > 0.1% per 8h = overleveraged long (squeeze risk), Negative < -0.05% = over-shorted
- Open interest trend: rising OI + rising price = strong trend

NEWS SENTIMENT:
- Score each headline -5 (very bearish) to +5 (very bullish)
- Weight by source: Bloomberg/Reuters (1.5x), CoinDesk (1.0x), Twitter (0.7x)
- Weight by category: Regulatory 2.0x, Institutional 1.5x, Hack/exploit 2.5x (always bearish), Macro 1.5x

COMBINED SENTIMENT SCORE: -10 to +10

Respond ONLY in JSON:
{"fear_greed_score": int, "fear_greed_label": str, "funding_rate": float, "funding_signal": "overleveraged_long|overleveraged_short|neutral", "news_sentiment": str, "combined_sentiment": int(-10 to 10), "trade_bias_adjustment": "boost|neutral|reduce|flat", "risk_note": "..." }"""

# ---------------------------------------------------------------------------
# Safe default
# ---------------------------------------------------------------------------
SAFE_DEFAULT = {
    "fear_greed_score": 50,
    "fear_greed_label": "neutral",
    "funding_rate": 0.0,
    "funding_signal": "neutral",
    "news_sentiment": "neutral",
    "combined_sentiment": 0,
    "trade_bias_adjustment": "neutral",
    "risk_note": "Sentiment agent encountered an error -- defaulting to neutral",
}

# ---------------------------------------------------------------------------
# Valid values
# ---------------------------------------------------------------------------
VALID_FG_LABELS = {"extreme_fear", "fear", "neutral", "greed", "extreme_greed"}
VALID_FUNDING_SIGNALS = {"overleveraged_long", "overleveraged_short", "neutral"}
VALID_NEWS_SENTIMENTS = {"very_bullish", "bullish", "neutral", "bearish", "very_bearish"}
VALID_BIAS_ADJUSTMENTS = {"boost", "neutral", "reduce", "flat"}


# ---------------------------------------------------------------------------
# Data fetchers (all FREE APIs)
# ---------------------------------------------------------------------------
async def _fetch_fear_greed() -> dict:
    """Fetch the current Fear & Greed Index from api.alternative.me (free, no key)."""
    url = "https://api.alternative.me/fng/?limit=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    entry = data.get("data", [{}])[0]
                    score = int(entry.get("value", 50))
                    label = entry.get("value_classification", "Neutral").lower().replace(" ", "_")
                    logger.info("Fear & Greed: %d (%s)", score, label)
                    return {"score": score, "label": label}
    except Exception as exc:
        logger.warning("Fear & Greed fetch failed: %s", exc)
    return {"score": 50, "label": "neutral"}


async def _fetch_funding_rate(symbol: str = "BTCUSDT") -> float:
    """Fetch the current Binance USDT-M funding rate (public, no key)."""
    url = f"https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=1"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data:
                        rate = float(data[-1].get("fundingRate", 0.0))
                        logger.info("Funding rate: %.6f", rate)
                        return rate
    except Exception as exc:
        logger.warning("Funding rate fetch failed: %s", exc)
    return 0.0


async def _fetch_crypto_news() -> list[dict]:
    """Fetch recent crypto news headlines.

    Tries CryptoPanic API first (requires free key in .env).
    Falls back to a neutral empty list if no key or API fails.
    """
    api_key = os.getenv("CRYPTOPANIC_API_KEY", "")

    if api_key:
        url = (
            f"https://cryptopanic.com/api/v1/posts/"
            f"?auth_token={api_key}&public=true&filter=hot&currencies=BTC"
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        headlines = [
                            {
                                "title": r.get("title", ""),
                                "source": r.get("source", {}).get("title", "unknown"),
                                "kind": r.get("kind", "news"),
                            }
                            for r in results[:10]
                        ]
                        logger.info("CryptoPanic: fetched %d headlines", len(headlines))
                        return headlines
        except Exception as exc:
            logger.warning("CryptoPanic fetch failed: %s", exc)

    # Fallback: no news data available
    logger.info("No CryptoPanic API key — news sentiment will be neutral")
    return []


# ---------------------------------------------------------------------------
# Fetch all sentiment data in parallel
# ---------------------------------------------------------------------------
async def _fetch_all_sentiment_data() -> dict:
    """Fetch fear/greed, funding rate, and news in parallel."""
    fg_task = _fetch_fear_greed()
    fr_task = _fetch_funding_rate()
    news_task = _fetch_crypto_news()

    fg_result, funding_rate, news_headlines = await asyncio.gather(
        fg_task, fr_task, news_task, return_exceptions=True,
    )

    # Handle any exceptions from gather
    if isinstance(fg_result, Exception):
        logger.warning("Fear & Greed gather error: %s", fg_result)
        fg_result = {"score": 50, "label": "neutral"}
    if isinstance(funding_rate, Exception):
        logger.warning("Funding rate gather error: %s", funding_rate)
        funding_rate = 0.0
    if isinstance(news_headlines, Exception):
        logger.warning("News gather error: %s", news_headlines)
        news_headlines = []

    return {
        "fear_greed": fg_result,
        "funding_rate": funding_rate,
        "news_headlines": news_headlines,
    }


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


def _classify_fear_greed(score: int) -> str:
    """Map numeric fear/greed score to a label."""
    try:
        import config
        fear_extreme = getattr(config, "SENTIMENT_FEAR_EXTREME", 20)
        greed_extreme = getattr(config, "SENTIMENT_GREED_EXTREME", 80)
    except Exception:
        fear_extreme = 20
        greed_extreme = 80

    if score <= fear_extreme:
        return "extreme_fear"
    elif score <= 40:
        return "fear"
    elif score <= 60:
        return "neutral"
    elif score < greed_extreme:
        return "greed"
    return "extreme_greed"


def _classify_funding(rate: float) -> str:
    """Classify the funding rate signal."""
    try:
        import config
        threshold = getattr(config, "FUNDING_OVERLEVERAGED_THRESHOLD", 0.001)
    except Exception:
        threshold = 0.001

    if rate > threshold:
        return "overleveraged_long"
    elif rate < -0.0005:
        return "overleveraged_short"
    return "neutral"


def _validate(parsed: dict) -> dict:
    """Normalise raw parsed dict into the expected schema."""
    fg_score = parsed.get("fear_greed_score", 50)
    try:
        fg_score = int(fg_score)
    except (TypeError, ValueError):
        fg_score = 50
    fg_score = max(0, min(100, fg_score))

    fg_label = str(parsed.get("fear_greed_label", "neutral")).lower()
    if fg_label not in VALID_FG_LABELS:
        fg_label = _classify_fear_greed(fg_score)

    funding = parsed.get("funding_rate", 0.0)
    try:
        funding = float(funding)
    except (TypeError, ValueError):
        funding = 0.0

    funding_signal = str(parsed.get("funding_signal", "neutral")).lower()
    if funding_signal not in VALID_FUNDING_SIGNALS:
        funding_signal = _classify_funding(funding)

    news = str(parsed.get("news_sentiment", "neutral")).lower()
    if news not in VALID_NEWS_SENTIMENTS:
        news = "neutral"

    combined = parsed.get("combined_sentiment", 0)
    try:
        combined = int(combined)
    except (TypeError, ValueError):
        combined = 0
    combined = max(-10, min(10, combined))

    bias_adj = str(parsed.get("trade_bias_adjustment", "neutral")).lower()
    if bias_adj not in VALID_BIAS_ADJUSTMENTS:
        bias_adj = "neutral"

    risk_note = str(parsed.get("risk_note", ""))[:200]

    return {
        "fear_greed_score": fg_score,
        "fear_greed_label": fg_label,
        "funding_rate": round(funding, 6),
        "funding_signal": funding_signal,
        "news_sentiment": news,
        "combined_sentiment": combined,
        "trade_bias_adjustment": bias_adj,
        "risk_note": risk_note,
    }


# ---------------------------------------------------------------------------
# LOCAL rule-based sentiment analysis (FREE — no API calls to Claude)
# ---------------------------------------------------------------------------
def _local_sentiment_signal(raw_data: dict) -> dict:
    """Score sentiment using deterministic rules on fetched data.

    Parameters
    ----------
    raw_data : dict
        Output from _fetch_all_sentiment_data().
    """
    fg = raw_data.get("fear_greed", {})
    fg_score = fg.get("score", 50)
    fg_label = _classify_fear_greed(fg_score)

    funding_rate = raw_data.get("funding_rate", 0.0)
    funding_signal = _classify_funding(funding_rate)

    news_headlines = raw_data.get("news_headlines", [])

    # ── Combined sentiment scoring ────────────────────────────────

    sentiment_score = 0  # -10 to +10

    # Fear & Greed contribution (-3 to +3)
    if fg_score <= 15:
        sentiment_score -= 3  # Extreme fear — market panic
    elif fg_score <= 25:
        sentiment_score -= 2
    elif fg_score <= 40:
        sentiment_score -= 1
    elif fg_score >= 85:
        sentiment_score += 3  # Extreme greed — danger zone
    elif fg_score >= 75:
        sentiment_score += 2
    elif fg_score >= 60:
        sentiment_score += 1

    # Funding rate contribution (-3 to +3)
    if funding_rate > 0.003:
        sentiment_score -= 3  # Very overleveraged long — squeeze risk
    elif funding_rate > 0.001:
        sentiment_score -= 1  # Mildly overleveraged
    elif funding_rate < -0.001:
        sentiment_score += 2  # Market over-shorted — squeeze potential
    elif funding_rate < -0.0005:
        sentiment_score += 1

    # News contribution (simple keyword scan if headlines exist)
    if news_headlines:
        bullish_keywords = {
            "etf", "approval", "institutional", "adoption", "rally",
            "surge", "bullish", "record", "milestone", "launch",
        }
        bearish_keywords = {
            "hack", "exploit", "ban", "regulation", "sec", "crash",
            "scam", "fraud", "liquidation", "warning", "bearish",
        }

        news_score = 0
        for headline in news_headlines:
            title_lower = headline.get("title", "").lower()
            for kw in bullish_keywords:
                if kw in title_lower:
                    news_score += 1
                    break
            for kw in bearish_keywords:
                if kw in title_lower:
                    news_score -= 1
                    break

        # Cap news contribution at -3 to +3
        news_score = max(-3, min(3, news_score))
        sentiment_score += news_score

    # Final clamping
    sentiment_score = max(-10, min(10, sentiment_score))

    # Determine news sentiment label
    if news_headlines:
        if sentiment_score >= 5:
            news_sentiment = "very_bullish"
        elif sentiment_score >= 2:
            news_sentiment = "bullish"
        elif sentiment_score <= -5:
            news_sentiment = "very_bearish"
        elif sentiment_score <= -2:
            news_sentiment = "bearish"
        else:
            news_sentiment = "neutral"
    else:
        news_sentiment = "neutral"

    # Determine trade bias adjustment
    if sentiment_score >= 4:
        bias_adj = "boost"
    elif sentiment_score <= -6:
        bias_adj = "flat"  # Strong negative — stop trading
    elif sentiment_score <= -3:
        bias_adj = "reduce"
    else:
        bias_adj = "neutral"

    # Build risk note
    risk_parts = []
    if fg_label == "extreme_fear":
        risk_parts.append(f"Extreme fear ({fg_score})")
    elif fg_label == "extreme_greed":
        risk_parts.append(f"Extreme greed ({fg_score})")
    if funding_signal == "overleveraged_long":
        risk_parts.append(f"Funding overleveraged long ({funding_rate:.4%})")
    elif funding_signal == "overleveraged_short":
        risk_parts.append(f"Market over-shorted ({funding_rate:.4%})")
    risk_note = " | ".join(risk_parts) if risk_parts else "No significant risk signals"

    result = {
        "fear_greed_score": fg_score,
        "fear_greed_label": fg_label,
        "funding_rate": round(funding_rate, 6),
        "funding_signal": funding_signal,
        "news_sentiment": news_sentiment,
        "combined_sentiment": sentiment_score,
        "trade_bias_adjustment": bias_adj,
        "risk_note": risk_note,
    }

    logger.info(
        "Sentiment: F&G=%d(%s) | funding=%s | news=%s | combined=%+d | bias=%s",
        fg_score, fg_label, funding_signal, news_sentiment,
        sentiment_score, bias_adj,
    )
    return result


# ---------------------------------------------------------------------------
# AI-powered sentiment analysis (requires Claude API key)
# ---------------------------------------------------------------------------
def _ai_sentiment_signal(raw_data: dict) -> dict:
    """Call Claude API for sentiment analysis. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic

        user_prompt = (
            "Analyze this on-chain and news data for BTC trading sentiment:\n"
            f"{json.dumps(raw_data, indent=2, default=str)}"
        )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.error("ANTHROPIC_API_KEY is not set — falling back to local mode")
            return _local_sentiment_signal(raw_data)

        client = anthropic.Anthropic(api_key=api_key)

        logger.info("Calling Claude sentiment agent ...")
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=400,
            temperature=0.1,
            system=SENTIMENT_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text
        logger.info("Sentiment agent raw response: %s", raw_text)

        parsed = _extract_json(raw_text)
        result = _validate(parsed)

        logger.info(
            "Sentiment (AI): combined=%+d, bias=%s",
            result["combined_sentiment"], result["trade_bias_adjustment"],
        )
        return result

    except json.JSONDecodeError as exc:
        logger.error("Sentiment agent JSON parse error: %s — falling back to local", exc)
        return _local_sentiment_signal(raw_data)

    except Exception as exc:  # noqa: BLE001
        logger.error("Sentiment agent API error: %s — falling back to local", exc)
        return _local_sentiment_signal(raw_data)


# ---------------------------------------------------------------------------
# Public entry point — fetches data and runs analysis
# ---------------------------------------------------------------------------
async def run_sentiment_agent() -> dict:
    """Fetch on-chain data and evaluate market sentiment.

    This agent fetches its own data from free APIs (Fear & Greed,
    Binance funding rate, CryptoPanic news).

    Automatically uses local rules (FREE) or Claude API based on
    ``config.USE_AI_AGENTS``.  Falls back to local on any AI failure.

    Returns
    -------
    dict
        ``{"fear_greed_score", "fear_greed_label", "funding_rate",
          "funding_signal", "news_sentiment", "combined_sentiment",
          "trade_bias_adjustment", "risk_note"}``
        Always returns a valid dict — never raises.
    """
    # Fetch all data in parallel
    try:
        raw_data = await _fetch_all_sentiment_data()
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to fetch sentiment data: %s", exc)
        return dict(SAFE_DEFAULT)

    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_sentiment_signal(raw_data)
    else:
        return _local_sentiment_signal(raw_data)
