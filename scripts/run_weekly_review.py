"""
run_weekly_review.py
=====================
Standalone script to trigger the Weekly Strategy Agent.
Fetches all autopsies from the last 7 days and outputs a strategic report.
"""

import sys
import os
import time
from pathlib import Path
from dotenv import load_dotenv

# Add parent dir to path so we can import core/agents
sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.database import init_db, get_recent_autopsies
from agents.weekly_strategy_agent import run_weekly_review

def main():
    # Ensure .env is loaded from project root regardless of cwd
    project_root = Path(__file__).resolve().parent.parent
    load_dotenv(project_root / ".env")
    
    print("=" * 60)
    print("=== WEEKLY STRATEGY REVIEW ===")
    print("=" * 60)
    
    db = init_db()
    
    # In a real scenario, we'd filter by timestamp for the last 7 days.
    # For now, we'll just grab the 50 most recent autopsies.
    autopsies = get_recent_autopsies(db, limit=50)
    
    if not autopsies:
        print("No autopsies found in database. Cannot run review.")
        return
        
    print(f"Loaded {len(autopsies)} autopsies. Running AI review...")
    
    portfolio_stats = {
        "usdt_balance_est": 10000, # Mock stats
        "open_positions": 0
    }
    
    result = run_weekly_review(autopsies, portfolio_stats)
    
    print("\n--- RESULTS ---")
    print(f"VERDICT: {result.get('verdict', 'N/A').upper()}")
    print("\nSYSTEMATIC ISSUES:")
    for issue in result.get('systematic_issues', []):
        print(f"  - {issue}")
        
    print("\nRULE UPDATES PROPOSED:")
    for rule in result.get('rule_updates', []):
        print(f"\n  Target: {rule.get('target')}")
        print(f"  Issue: {rule.get('issue')}")
        print(f"  Proposal: {rule.get('proposal')}")
        print(f"  Confidence: {rule.get('confidence')}")
        
    print("\nSUMMARY:")
    print(result.get('summary', ''))
    
if __name__ == "__main__":
    main()
