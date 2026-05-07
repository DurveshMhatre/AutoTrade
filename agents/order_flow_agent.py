"""
Order Flow & Volume Profile Agent
===================================
Analyzes the live Binance order book and volume profile to detect
bid/ask walls, order flow imbalances, and VWAP relationships.

Supports TWO modes:
  • LOCAL mode (default, FREE): Algorithmic order book analysis.
  • AI mode: Calls Claude API for nuanced order flow analysis.
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
ORDERFLOW_SYSTEM_PROMPT = """You are an order flow analyst. Analyze volume and order book data.

ORDER BOOK ANALYSIS:
- Identify bid walls: clusters of large buy orders.
- Identify ask walls: clusters of large sell orders.
- Calculate bid/ask imbalance. Ratio > 1.5 is bullish, < 0.67 is bearish.

VOLUME ANALYSIS:
- VWAP position: price above VWAP = bullish bias, below = bearish.
- POC (Point of Control): highest volume price level.

Respond ONLY in JSON:
{"bid_ask_ratio": float, "order_flow_bias": "bullish|bearish|neutral", "nearest_bid_wall": float, "nearest_ask_wall": float, "price_vs_vwap": "above|below", "volume_note": "max 30 words", "order_flow_confidence_adj": float(-0.2 to 0.2)}"""

SAFE_DEFAULT = {
    "bid_ask_ratio": 1.0,
    "order_flow_bias": "neutral",
    "nearest_bid_wall": 0.0,
    "nearest_ask_wall": float("inf"),
    "price_vs_vwap": "above",
    "volume_note": "Order flow agent encountered an error",
    "order_flow_confidence_adj": 0.0,
}

VALID_BIASES = {"bullish", "bearish", "neutral"}

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
    ratio = float(parsed.get("bid_ask_ratio") or 1.0)
    bias = str(parsed.get("order_flow_bias", "neutral")).lower()
    if bias not in VALID_BIASES:
        bias = "neutral"
        
    adj = float(parsed.get("order_flow_confidence_adj") or 0.0)
    adj = max(-0.2, min(0.2, adj))
    
    return {
        "bid_ask_ratio": round(ratio, 2),
        "order_flow_bias": bias,
        "nearest_bid_wall": float(parsed.get("nearest_bid_wall") or 0.0),
        "nearest_ask_wall": float(parsed.get("nearest_ask_wall") or float("inf")),
        "price_vs_vwap": str(parsed.get("price_vs_vwap", "above")).lower(),
        "volume_note": str(parsed.get("volume_note", ""))[:100],
        "order_flow_confidence_adj": round(adj, 2),
    }

# ---------------------------------------------------------------------------
# LOCAL Algorithmic Order Flow (FREE)
# ---------------------------------------------------------------------------
def _local_order_flow(order_book: dict, vwap_data: dict, current_price: float) -> dict:
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    
    if not bids or not asks:
        return SAFE_DEFAULT
        
    # Calculate total volume in order book
    total_bid_vol = sum(float(b[1]) for b in bids)
    total_ask_vol = sum(float(a[1]) for a in asks)
    
    bid_ask_ratio = total_bid_vol / total_ask_vol if total_ask_vol > 0 else 1.0
    
    # Find walls (largest order in the book)
    try:
        max_bid = max(bids, key=lambda x: float(x[1]))
        nearest_bid_wall = float(max_bid[0])
    except ValueError:
        nearest_bid_wall = 0.0
        
    try:
        max_ask = max(asks, key=lambda x: float(x[1]))
        nearest_ask_wall = float(max_ask[0])
    except ValueError:
        nearest_ask_wall = float("inf")
        
    vwap = vwap_data.get("vwap", 0.0)
    price_vs_vwap = "above" if current_price >= vwap else "below"
    
    # Scoring
    bias = "neutral"
    conf_adj = 0.0
    
    if bid_ask_ratio > 1.5:
        bias = "bullish"
        conf_adj += 0.05
    elif bid_ask_ratio < 0.67:
        bias = "bearish"
        conf_adj -= 0.05
        
    if price_vs_vwap == "above":
        conf_adj += 0.05
    else:
        conf_adj -= 0.05
        
    note = f"B/A Ratio: {bid_ask_ratio:.2f}. Price is {price_vs_vwap} VWAP."
    
    return _validate({
        "bid_ask_ratio": bid_ask_ratio,
        "order_flow_bias": bias,
        "nearest_bid_wall": nearest_bid_wall,
        "nearest_ask_wall": nearest_ask_wall,
        "price_vs_vwap": price_vs_vwap,
        "volume_note": note,
        "order_flow_confidence_adj": conf_adj,
    })

# ---------------------------------------------------------------------------
# AI-Powered Order Flow
# ---------------------------------------------------------------------------
def _ai_order_flow(order_book: dict, vwap_data: dict, current_price: float) -> dict:
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return _local_order_flow(order_book, vwap_data, current_price)
            
        payload = {
            "top_5_bids": order_book.get("bids", [])[:5],
            "top_5_asks": order_book.get("asks", [])[:5],
            "vwap": vwap_data.get("vwap"),
            "poc": vwap_data.get("poc_price"),
            "current_price": current_price
        }
        
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=200,
            temperature=0.1,
            system=ORDERFLOW_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}]
        )
        
        return _validate(_extract_json(response.content[0].text))
    except Exception as exc:
        logger.error(f"OrderFlow AI agent error: {exc}")
        return _local_order_flow(order_book, vwap_data, current_price)

# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------
def run_order_flow_agent(order_book: dict, vwap_data: dict, current_price: float) -> dict:
    try:
        import config
        use_ai = getattr(config, "USE_AI_AGENTS", False)
    except Exception:
        use_ai = False

    if use_ai:
        return _ai_order_flow(order_book, vwap_data, current_price)
    return _local_order_flow(order_book, vwap_data, current_price)
