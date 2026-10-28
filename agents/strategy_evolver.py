"""
Strategy Evolver Agent — Autopsy-Driven Parameter Tuning
=========================================================
Reads trade autopsy records from the database, identifies recurring
failure patterns, and adjusts strategy parameters in
``config/live_params.json`` to prevent repeating mistakes.

This creates a closed feedback loop:
  Trade → Autopsy Agent grades it → Strategy Evolver adjusts params → Next trade is better

Usage
-----
    # As a standalone script:
    python -m agents.strategy_evolver

    # Or called from the bot loop (future integration):
    from agents.strategy_evolver import run_strategy_evolver
    adjustments = run_strategy_evolver(db)
"""

import json
import logging
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_PARAMS_PATH = Path(__file__).resolve().parent.parent / "config" / "live_params.json"

# ── Adjustment rules ────────────────────────────────────────────────
# Each rule maps a (root_cause OR pattern) to a parameter adjustment.
# Adjustments are ADDITIVE (deltas) and clamped to safe ranges.

_ADJUSTMENT_RULES = {
    # Root causes → param adjustments
    "stop_too_tight": {
        "STOP_LOSS_PCT": +0.002,     # Widen SL by 0.2%
    },
    "late_entry": {
        "MIN_CONFIDENCE": +0.05,     # Require higher confidence
    },
    "counter_trend": {
        "MIN_CONFIDENCE": +0.05,     # Be more selective
    },
    "wrong_regime": {
        "MIN_CONFIDENCE": +0.03,     # Slightly more selective
    },
    "low_volume_breakout": {
        "RISK_PER_TRADE_PCT": -0.005,  # Reduce position size
    },
    "overtraded_session": {
        "MIN_CONFIDENCE": +0.05,     # Raise entry bar
    },
}

# Exit quality → param adjustments
_EXIT_ADJUSTMENTS = {
    "early": {
        "TAKE_PROFIT_PCT": +0.005,   # Let winners run more
    },
    "poor": {
        "STOP_LOSS_PCT": +0.001,     # Slightly wider stops
    },
}

# Parameter safe ranges (never go outside these)
_PARAM_RANGES = {
    "STOP_LOSS_PCT":       (0.005, 0.05),
    "TAKE_PROFIT_PCT":     (0.01,  0.08),
    "MIN_CONFIDENCE":      (0.35,  0.80),
    "RISK_PER_TRADE_PCT":  (0.005, 0.05),
}


def _clamp(value: float, param_name: str) -> float:
    """Clamp a parameter value to its safe range."""
    lo, hi = _PARAM_RANGES.get(param_name, (0.0, 1.0))
    return max(lo, min(hi, value))


def _load_params() -> dict:
    """Load current dynamic parameters."""
    if _PARAMS_PATH.exists():
        try:
            with open(_PARAMS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_params(params: dict) -> None:
    """Save updated parameters."""
    _PARAMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    params["_last_updated"] = datetime.now(timezone.utc).isoformat()
    params["_updated_by"] = "strategy_evolver"
    with open(_PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, indent=2)


# ── Main evolver logic ──────────────────────────────────────────────

def run_strategy_evolver(
    db: sqlite3.Connection,
    lookback: int = 20,
    dry_run: bool = False,
) -> dict:
    """Analyze recent autopsies and adjust parameters.

    Parameters
    ----------
    db : sqlite3.Connection
        Open database connection.
    lookback : int
        Number of recent autopsies to analyze.
    dry_run : bool
        If True, compute adjustments but don't write to file.

    Returns
    -------
    dict
        ``{"adjustments": {...}, "reasons": [...], "applied": bool}``
    """
    from core.database import get_recent_autopsies

    autopsies = get_recent_autopsies(db, limit=lookback)

    if len(autopsies) < 5:
        logger.info("Strategy Evolver: not enough autopsies (%d < 5). Skipping.", len(autopsies))
        return {"adjustments": {}, "reasons": ["insufficient_data"], "applied": False}

    # Count root causes
    root_causes = Counter(a.get("root_cause") for a in autopsies if a.get("root_cause"))
    exit_qualities = Counter(a.get("exit_quality") for a in autopsies if a.get("exit_quality"))
    entry_grades = Counter(a.get("entry_grade") for a in autopsies if a.get("entry_grade"))

    logger.info("Strategy Evolver: root_causes=%s, exits=%s, entries=%s",
                dict(root_causes), dict(exit_qualities), dict(entry_grades))

    # Load current params
    current = _load_params()
    import config
    adjustments: dict[str, float] = {}
    reasons: list[str] = []

    # Apply root cause rules (only if a cause appears >= 3 times in last N trades)
    for cause, count in root_causes.items():
        if count >= 3 and cause in _ADJUSTMENT_RULES:
            for param, delta in _ADJUSTMENT_RULES[cause].items():
                current_val = current.get(param, getattr(config, param, 0.02))
                new_val = _clamp(current_val + delta, param)
                if new_val != current_val:
                    adjustments[param] = round(new_val, 4)
                    reasons.append(f"{cause} x{count} → {param} {current_val:.4f} → {new_val:.4f}")

    # Apply exit quality rules
    for quality, count in exit_qualities.items():
        if count >= 3 and quality in _EXIT_ADJUSTMENTS:
            for param, delta in _EXIT_ADJUSTMENTS[quality].items():
                current_val = current.get(param, getattr(config, param, 0.02))
                new_val = _clamp(current_val + delta, param)
                if new_val != current_val:
                    adjustments[param] = round(new_val, 4)
                    reasons.append(f"exit_{quality} x{count} → {param} {current_val:.4f} → {new_val:.4f}")

    # Apply entry grade rules (too many D grades = tighten confidence)
    d_count = entry_grades.get("D", 0)
    if d_count >= 4:
        param = "MIN_CONFIDENCE"
        current_val = current.get(param, getattr(config, param, 0.50))
        new_val = _clamp(current_val + 0.05, param)
        if new_val != current_val:
            adjustments[param] = round(new_val, 4)
            reasons.append(f"entry_grade_D x{d_count} → {param} {current_val:.4f} → {new_val:.4f}")

    # Write
    if adjustments and not dry_run:
        current.update(adjustments)
        _save_params(current)
        logger.info("Strategy Evolver: applied %d adjustments: %s", len(adjustments), adjustments)
    elif adjustments:
        logger.info("Strategy Evolver: [DRY-RUN] would apply: %s", adjustments)
    else:
        logger.info("Strategy Evolver: no adjustments needed.")

    return {
        "adjustments": adjustments,
        "reasons": reasons,
        "applied": bool(adjustments) and not dry_run,
    }


# ── Standalone execution ────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")

    from core.database import init_db
    db = init_db()
    result = run_strategy_evolver(db, dry_run=True)

    print("\n" + "=" * 50)
    print("  STRATEGY EVOLVER REPORT")
    print("=" * 50)
    if result["adjustments"]:
        for reason in result["reasons"]:
            print(f"  • {reason}")
        print(f"\n  Applied: {result['applied']}")
    else:
        print("  No adjustments needed.")
    print("=" * 50)
