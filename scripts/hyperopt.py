"""
scripts/hyperopt.py — Hyperparameter Optimisation for DurveshBot
=================================================================
Uses Optuna (TPE sampler) to search for the best strategy parameters
by running backtests over historical data.

Writes the winning parameters to ``config/live_params.json`` so the
live bot picks them up on its next import of ``config.py``.

Usage
-----
    # Install once:   pip install optuna
    # Then run:
    python scripts/hyperopt.py --iterations 200
    python scripts/hyperopt.py --iterations 50 --symbol ETH/USDT
    python scripts/hyperopt.py --walk-forward          # use walk-forward validation
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from core.data_feed import fetch_ohlcv
from backtest import run_backtest, walk_forward_backtest, generate_performance_report

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("hyperopt")
logger.setLevel(logging.INFO)


# ─── Search space ──────────────────────────────────────────────────────

def _suggest_params(trial) -> dict:
    """Define the hyperparameter search space for Optuna."""
    return {
        "STOP_LOSS_PCT": trial.suggest_float("STOP_LOSS_PCT", 0.008, 0.035, step=0.001),
        "TAKE_PROFIT_PCT": trial.suggest_float("TAKE_PROFIT_PCT", 0.015, 0.06, step=0.005),
        "MIN_CONFIDENCE": trial.suggest_float("MIN_CONFIDENCE", 0.40, 0.70, step=0.05),
        "RISK_PER_TRADE_PCT": trial.suggest_float("RISK_PER_TRADE_PCT", 0.01, 0.04, step=0.005),
    }


# ─── Objective function ───────────────────────────────────────────────

_candle_cache: list | None = None


def _make_objective(candles: list, use_walk_forward: bool):
    """Return an Optuna objective function that backtests with suggested params."""

    def objective(trial):
        params = _suggest_params(trial)

        if use_walk_forward:
            wf_report = asyncio.run(
                walk_forward_backtest(candles, params=params)
            )
            if "error" in wf_report:
                return -9999.0  # Not enough data

            total_pnl = wf_report["total_pnl"]
            total_trades = wf_report["total_trades"]
            max_dd = wf_report["max_drawdown_pct"]
        else:
            _, report = asyncio.run(
                run_backtest(candles, starting_balance=1000.0, params=params)
            )
            total_pnl = report["total_pnl"]
            total_trades = report["total_trades"]
            max_dd = report["max_drawdown_pct"]

        # Penalise: too few trades (not enough data), extreme drawdown
        if total_trades < 5:
            return -9999.0

        # Composite score: reward profit, penalise drawdown
        # Sharpe-like: return / drawdown, with trade count bonus
        dd_penalty = max(max_dd, 1.0)  # avoid div-by-zero
        score = (total_pnl / dd_penalty) + (total_trades * 0.1)

        trial.set_user_attr("total_pnl", total_pnl)
        trial.set_user_attr("total_trades", total_trades)
        trial.set_user_attr("max_drawdown_pct", max_dd)

        return score

    return objective


# ─── Save best params ─────────────────────────────────────────────────

def _save_best(best_params: dict, score: float):
    """Write the best parameters to config/live_params.json."""
    out_path = _PROJECT_ROOT / "config" / "live_params.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing to preserve non-optimised keys
    existing = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except Exception:
            pass

    existing.update(best_params)
    existing["_last_updated"] = datetime.now(timezone.utc).isoformat()
    existing["_updated_by"] = "hyperopt"
    existing["_best_score"] = round(score, 4)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, indent=2)

    logger.info("Best params saved to %s", out_path)


# ─── Main ──────────────────────────────────────────────────────────────

async def _fetch_data(symbol: str, timeframe: str, limit: int) -> list:
    """Fetch candle data for backtesting."""
    logger.info("Fetching %d %s candles for %s ...", limit, timeframe, symbol)
    candles = await fetch_ohlcv(symbol, timeframe, limit)
    logger.info("Received %d candles", len(candles))
    return candles


def main():
    parser = argparse.ArgumentParser(description="Hyperparameter optimisation for DurveshBot")
    parser.add_argument("--iterations", "-n", type=int, default=100, help="Number of Optuna trials")
    parser.add_argument("--symbol", "-s", type=str, default="BTC/USDT", help="Trading pair")
    parser.add_argument("--timeframe", "-t", type=str, default="5m", help="Candle timeframe")
    parser.add_argument("--candles", "-c", type=int, default=5000, help="Number of candles to fetch")
    parser.add_argument("--walk-forward", "-wf", action="store_true", help="Use walk-forward validation")
    args = parser.parse_args()

    # Fetch data
    candles = asyncio.run(_fetch_data(args.symbol, args.timeframe, args.candles))

    if len(candles) < 500:
        logger.error("Not enough candles (%d) for meaningful optimisation. Need at least 500.", len(candles))
        sys.exit(1)

    # Run optimisation
    try:
        import optuna
        optuna.logging.set_verbosity(optuna.logging.WARNING)
    except ImportError:
        logger.error(
            "Optuna not installed. Run:  pip install optuna\n"
            "Or add optuna to requirements.txt and reinstall."
        )
        sys.exit(1)

    logger.info("Starting Optuna optimisation: %d trials, walk-forward=%s", args.iterations, args.walk_forward)

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
        study_name=f"durveshbot_{args.symbol.replace('/', '_')}",
    )

    objective = _make_objective(candles, args.walk_forward)
    study.optimize(objective, n_trials=args.iterations, show_progress_bar=True)

    # Results
    best = study.best_trial
    print("\n" + "=" * 60)
    print("  HYPEROPT RESULTS")
    print("=" * 60)
    print(f"  Best score        : {best.value:.4f}")
    print(f"  Total PnL         : ${best.user_attrs.get('total_pnl', 0):.2f}")
    print(f"  Total trades      : {best.user_attrs.get('total_trades', 0)}")
    print(f"  Max drawdown      : {best.user_attrs.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Trials run        : {len(study.trials)}")
    print()
    print("  Best parameters:")
    for k, v in best.params.items():
        print(f"    {k:25s} = {v}")
    print("=" * 60)

    # Save
    _save_best(best.params, best.value)
    print(f"\n  ✅ Best params written to config/live_params.json")
    print(f"  The live bot will pick these up on next startup.\n")


if __name__ == "__main__":
    main()
