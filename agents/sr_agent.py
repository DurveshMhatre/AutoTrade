"""
Support & Resistance Detection Agent
======================================
Analyzes price history to identify the nearest support and resistance levels.
Evaluates entry quality based on proximity to these key levels.

Supports TWO modes (controlled by config.USE_AI_AGENTS):
  • LOCAL mode (default, FREE): Algorithmic S/R detection (pivot points, round numbers).
  • AI mode: Calls Claude API for level analysis.

Never crashes — returns a safe default on any failure.
"""

import json
import logging
import os
import re

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (used only in AI mode)
# ---------------------------------------------------------------------------
SR_SYSTEM_PROMPT = """You are a technical analyst specializing in key price level identification.

Given the recent price action (OHLCV summary) and current price, identify the most significant support and resistance levels.

METHODOLOGY (in order of strength):
1. SWING HIGHS/LOWS: Look for pivot points where price reversed.
2. ROUND NUMBERS: Psychological levels that act as S/R (e.g., $60,000, $65,000).
3. HIGH VOLUME NODES: Price areas with historically high volume.

FOR EACH LEVEL CALCULATE:
- Level price
- Strength: 1-10 (based on touches and significance)
- Type: "support" | "resistance"

ENTRY QUALITY:
- If current price is within 1.5% of strong support: "excellent"
- If current price is within 1.5% of strong resistance: "poor"
- If price is in mid-air between levels: "fair"

Respond ONLY in JSON:
{"nearest_support": float, "nearest_resistance": float, "support_strength": int(1-10), "resistance_strength": int(1-10), "entry_quality": "excellent|good|fair|poor", "suggested_stop_loss": float, "suggested_take_profit": float, "confidence_adjustment": float(-0.2 to 0.2), "zone_note": "max 30 words"}"""

# ---------------------------------------------------------------------------
# Safe default
# ---------------------------------------------------------------------------
SAFE_DEFAULT = {
    "nearest_support": 0.0,
    "nearest_resistance": float("inf"),
    "support_strength": 1,
    "resistance_strength": 1,
    "entry_quality": "fair",
    "suggested_stop_loss": 0.0,
    "suggested_take_profit": 0.0,
    "confidence_adjustment": 0.0,
    "zone_note": "SR agent encountered an error -- defaulting to fair",
}

VALID_ENTRY_QUALITIES = {"excellent", "good", "fair", "poor"}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start == -1 or end == 0:
        raise ValueError("No JSON object found in response")
    return json.loads(cleaned[start:end])


def _validate(parsed: dict, current_price: float) -> dict:
    support = float(parsed.get("nearest_support") or 0.0)
    resistance = float(parsed.get("nearest_resistance") or float("inf"))
    
    if support >= current_price:
        support = current_price * 0.9  # fallback
    if resistance <= current_price:
        resistance = current_price * 1.1 # fallback

    quality = str(parsed.get("entry_quality", "fair")).lower()
    if quality not in VALID_ENTRY_QUALITIES:
        quality = "fair"
        
    adj = float(parsed.get("confidence_adjustment") or 0.0)
    adj = max(-0.2, min(0.2, adj))

    return {
        "nearest_support": round(support, 2),
        "nearest_resistance": round(resistance, 2),
        "support_strength": int(parsed.get("support_strength", 5)),
        "resistance_strength": int(parsed.get("resistance_strength", 5)),
        "entry_quality": quality,
        "suggested_stop_loss": round(float(parsed.get("suggested_stop_loss", support * 0.99)), 2),
        "suggested_take_profit": round(float(parsed.get("suggested_take_profit", resistance * 0.99)), 2),
        "confidence_adjustment": round(adj, 2),
        "zone_note": str(parsed.get("zone_note", ""))[:100],
    }


def _find_pivot_levels(df: pd.DataFrame, window: int = 5) -> tuple[list[float], list[float]]:
    """Find algorithmic swing highs and lows."""
    if len(df) < window * 2 + 1:
        return [], []
        
    highs = []
    lows = []
    
    for i in range(window, len(df) - window):
        # Local High
        if df['high'].iloc[i] == df['high'].iloc[i-window:i+window+1].max():
            highs.append(df['high'].iloc[i])
        # Local Low
        if df['low'].iloc[i] == df['low'].iloc[i-window:i+window+1].min():
            lows.append(df['low'].iloc[i])
            
    return highs, lows

# ---------------------------------------------------------------------------
# LOCAL algorithmic S/R (FREE)
# ---------------------------------------------------------------------------
def _local_sr_signal(candles: list[dict], current_price: float) -> dict:
    """Detect support/resistance algorithmically.
    
    Parameters
    ----------
    candles : list[dict]
        HTF candles (e.g., 4H or 1H) for strong level detection.
    current_price : float
    """
    if not candles or len(candles) < 20:
        return SAFE_DEFAULT

    try:
        import config
        prox_thresh = getattr(config, "SR_PROXIMITY_PCT", 0.015)
    except Exception:
        prox_thresh = 0.015

    df = pd.DataFrame(candles)
    for col in ("high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
        
    # Find pivots
    highs, lows = _find_pivot_levels(df, window=5)
    
    # Also add round numbers near current price
    magnitude = 10**(len(str(int(current_price))) - 1) # e.g. 10000 for 50000
    if magnitude > 10000:
        magnitude = 10000
    elif magnitude < 1000:
        magnitude = 1000
        
    round_below = (int(current_price) // magnitude) * magnitude
    round_above = round_below + magnitude
    lows.append(round_below)
    highs.append(round_above)
    
    # Filter valid levels
    valid_supports = [l for l in lows if l < current_price]
    valid_resistances = [h for h in highs if h > current_price]
    
    nearest_support = max(valid_supports) if valid_supports else current_price * 0.95
    nearest_resistance = min(valid_resistances) if valid_resistances else current_price * 1.05
    
    # Calculate proximity
    dist_to_supp = (current_price - nearest_support) / current_price
    dist_to_res = (nearest_resistance - current_price) / current_price
    
    # Determine Entry Quality
    if dist_to_supp <= prox_thresh and dist_to_res > prox_thresh:
        entry_quality = "excellent"
        conf_adj = 0.15
        note = f"Price very close to support at {nearest_support:,.0f}."
    elif dist_to_res <= prox_thresh:
        entry_quality = "poor"
        conf_adj = -0.20
        note = f"Price buying into resistance at {nearest_resistance:,.0f}."
    else:
        entry_quality = "fair"
        conf_adj = -0.05
        note = "Price in mid-air between levels."
        
    # SL just below support, TP just below resistance
    suggested_sl = nearest_support * 0.99
    suggested_tp = nearest_resistance * 0.99
    
    return _validate({
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "support_strength": 7,
        "resistance_strength": 7,
        "entry_quality": entry_quality,
        "suggested_stop_loss": suggested_sl,
        "suggested_take_profit": suggested_tp,
        "confidence_adjustment": conf_adj,
        "zone_note": note,
    }, current_price)


# ---------------------------------------------------------------------------
# AI-powered S/R (requires Claude)
# ---------------------------------------------------------------------------
def _ai_sr_signal(candles: list[dict], current_price: float) -> dict:
    try:
        import anthropic
        
        # Summarize candles to save tokens (last 20 + highs/lows)
        recent_candles = candles[-20:]
        df = pd.DataFrame(candles)
        data_summary = {
            "current_price": current_price,
            "period_high": df["high"].max(),
            "period_low": df["low"].min(),
            "recent_candles": [
                {"c": c["close"], "h": c["high"], "l": c["low"], "v": c["volume"]} 
                for c in recent_candles
            ]
        }
        
        user_prompt = f"Analyze S/R for this market:\n{json.dumps(data_summary)}"
        
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return _local_sr_signal(candles, current_price)
            
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=250,
            temperature=0.1,
            system=SR_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        
        parsed = _extract_json(response.content[0].text)
        return _validate(parsed, current_price)
        
    except Exception as exc:
        logger.error(f"SR AI agent error: {exc}")
        return _local_sr_signal(candles, current_price)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_sr_agent(candles: list[dict], current_price: float) -> dict:
    """Analyze Support and Resistance levels.
    
    Parameters
    ----------
    candles : list[dict]
        HTF candles (e.g. 4H or 1D) for major levels.
    current_price : float
    """
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_sr_signal(candles, current_price)
    else:
        return _local_sr_signal(candles, current_price)
