"""
Agent Router — AI-powered trade analysis (placeholder).
Will be fully implemented in Phase 5 (AI Analyst).
"""

from fastapi import APIRouter

router = APIRouter(tags=["agent"])


@router.post("/agent/analyze")
async def analyze_trades(body: dict = None):
    """AI trade analyst endpoint — placeholder for Phase 5."""
    return {
        "analysis": "AI analyst not yet configured. This will be implemented in Phase 5.",
        "recommendations": [],
        "risk_score": 0,
        "generated_at": "",
    }


@router.get("/agent/daily-summary")
async def daily_summary():
    """Cached daily AI review — placeholder for Phase 5."""
    return {
        "analysis": "Daily summary not yet available.",
        "recommendations": [],
        "risk_score": 0,
        "generated_at": "",
    }
