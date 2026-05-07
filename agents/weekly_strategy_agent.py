"""
Weekly Strategy Review Agent
=============================
Analyzes 7 days of trade autopsies and system decisions to propose 
concrete improvements to the bot's configuration or agent prompts.
"""

import json
import logging
import os
from typing import Dict, Any, List
from anthropic import Anthropic

logger = logging.getLogger(__name__)

WEEKLY_SYSTEM_PROMPT = """You are the chief strategist reviewing trading performance to evolve the bot's strategy.
You receive a list of trade autopsies from the past week.

YOUR OUTPUT must include:

1. PERFORMANCE VERDICT: Is the strategy improving, stable, or degrading?
2. TOP 3 SYSTEMATIC ISSUES: What keeps causing losses?
3. RULE UPDATES: Specific rules to update in config.py or agent prompts.
4. NEW RULE PROPOSALS: Propose up to 2 entirely new rules backed by evidence.
5. CONFIDENCE: high, medium, or low.

Respond ONLY with valid JSON in the following format:
{
  "verdict": "improving|stable|degrading",
  "systematic_issues": ["issue 1", "issue 2"],
  "rule_updates": [
    {
      "target": "trend_agent|config.py|etc",
      "issue": "current problem",
      "proposal": "new rule text",
      "evidence": "trade IDs or pattern",
      "confidence": "high|medium|low"
    }
  ],
  "summary": "Brief explanation for the user."
}
"""

def run_weekly_review(autopsies: List[dict], portfolio_stats: dict) -> Dict[str, Any]:
    """
    Run the weekly review analysis.
    """
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    try:
        data_summary = {
            "autopsies": autopsies,
            "portfolio_stats": portfolio_stats
        }
        
        prompt = (
            f"Please analyze the following weekly data:\n\n"
            f"{json.dumps(data_summary, indent=2)}\n\n"
            f"Provide the review in the exact JSON format requested."
        )

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1500,
            system=WEEKLY_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )

        raw_text = response.content[0].text
        
        if "```json" in raw_text:
            raw_text = raw_text.split("```json")[1].split("```")[0]
        elif "```" in raw_text:
            raw_text = raw_text.split("```")[1].split("```")[0]

        return json.loads(raw_text)
    except Exception as e:
        logger.error(f"Weekly Strategy Agent failed: {e}")
        return {
            "verdict": "error",
            "systematic_issues": [],
            "rule_updates": [],
            "summary": f"Failed to run analysis: {e}"
        }
