"""
Autopsy Agent
==============
Runs post-trade analysis using Anthropic Claude to determine entry/exit quality, 
root causes of losses, and actionable lessons for the bot's weekly review.

Never crashes — returns a safe default on any failure, including missing API key
or missing anthropic package.
"""

import json
import logging
import os
from typing import Dict, Any

logger = logging.getLogger(__name__)

AUTOPSY_SYSTEM_PROMPT = """You are a trading coach reviewing closed trades for an automated crypto bot. 
Your job is to extract lessons that improve future performance.

For each closed trade you receive:
- Trade metrics (side, entry, exit, P&L, duration)
- Market context at the time of entry/exit

AUTOPSY FRAMEWORK:

1. ENTRY QUALITY (was this a good setup?):
   - Were all signals aligned?
   - Grade: A (excellent) / B (good) / C (mediocre) / D (should not have taken)

2. EXIT QUALITY (did we exit optimally?):
   - For wins: did we exit too early?
   - For losses: was the stop loss placement correct? Was there a better exit earlier?

3. ROOT CAUSE (for losing trades only):
   Diagnose from: wrong_regime | counter_trend | low_volume_breakout | news_driven | stop_too_tight | late_entry | overtraded_session | other

4. LESSON:
   One specific, actionable rule to add or adjust.
   Format: "In future, when [condition], [action]."

5. PATTERN DETECTION:
   Flag if this trade represents a recurring failure pattern.

Respond ONLY with valid JSON in the following format:
{
  "entry_grade": "A|B|C|D",
  "exit_quality": "optimal|good|early|late|poor",
  "root_cause": "wrong_regime" or null,
  "lesson": "In future, when...",
  "pattern_detected": true/false,
  "pattern_description": "description" or null
}
"""

# Safe default returned when autopsy can't run
SAFE_DEFAULT = {
    "entry_grade": "C",
    "exit_quality": "unknown",
    "root_cause": None,
    "lesson": "Autopsy skipped.",
    "pattern_detected": 0,
    "pattern_description": None,
}


def run_autopsy_agent(trade_data: dict, market_context: dict) -> Dict[str, Any]:
    """
    Run the autopsy analysis on a closed trade.
    
    Uses lazy import of anthropic to avoid crashing if the package
    is not installed or no API key is configured.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set — skipping autopsy.")
        return {
            "entry_grade": "C",
            "exit_quality": "unknown",
            "root_cause": "no_api_key",
            "lesson": "Autopsy skipped: no API key configured.",
            "pattern_detected": 0,
            "pattern_description": None
        }

    # Lazy import — only loaded when actually called
    try:
        from anthropic import Anthropic
    except ImportError:
        logger.warning("anthropic package not installed — skipping autopsy.")
        return dict(SAFE_DEFAULT)

    client = Anthropic(api_key=api_key)

    try:
        trade_summary = json.dumps(trade_data, indent=2)
        context_summary = json.dumps(market_context, indent=2)

        prompt = (
            f"Please analyze the following closed trade:\n\n"
            f"TRADE METRICS:\n{trade_summary}\n\n"
            f"MARKET CONTEXT:\n{context_summary}\n\n"
            f"Provide the autopsy in the exact JSON format requested."
        )

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1000,
            system=AUTOPSY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        raw_text = response.content[0].text
        
        # Simple JSON extraction
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0]
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0]

        result = json.loads(raw_text)
        return {
            "entry_grade": result.get("entry_grade", "C"),
            "exit_quality": result.get("exit_quality", "poor"),
            "root_cause": result.get("root_cause"),
            "lesson": result.get("lesson", "No lesson extracted."),
            "pattern_detected": 1 if result.get("pattern_detected") else 0,
            "pattern_description": result.get("pattern_description")
        }
    except Exception as e:
        logger.error(f"Autopsy agent failed: {e}")
        return {
            "entry_grade": "C",
            "exit_quality": "poor",
            "root_cause": "agent_error",
            "lesson": f"Error during autopsy: {e}",
            "pattern_detected": 0,
            "pattern_description": None
        }
