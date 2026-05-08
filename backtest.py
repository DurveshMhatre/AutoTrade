"""
backtest.py -- Historical backtesting harness
===============================================
Runs the trend-following strategy against historical candle data,
simulates trades with SL/TP, generates a performance report, and
optionally calls Claude to analyze the results.

Usage:
    python backtest.py
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

# ── Project imports ─────────────────────────────────────────────────
from core.data_feed import fetch_ohlcv
from core.indicators import compute_indicators
from core.risk_manager import evaluate_trade
from agents.trend_agent import run_trend_agent
import config

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backtest")
logger.setLevel(logging.INFO)


# =====================================================================
#  1. LOCAL RULE-BASED SIGNAL GENERATOR
#     Import from the canonical trend_agent to avoid code duplication.
#     This ensures backtest and live bot always use the same rules.
# =====================================================================

from agents.trend_agent import _local_trend_signal


# =====================================================================
#  2. PERFORMANCE REPORT GENERATOR
# =====================================================================

def generate_performance_report(trades: list, starting_balance: float) -> dict:
    """Compute performance metrics from a list of completed backtest trades.

    Parameters
    ----------
    trades : list[dict]
        Each trade has: side, entry_price, exit_price, quantity, pnl,
        exit_reason, entry_idx, exit_idx, confidence.
    starting_balance : float
        Initial USDT balance.

    Returns
    -------
    dict
        Performance metrics summary.
    """
    if not trades:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "final_balance": starting_balance,
            "return_pct": 0.0,
            "avg_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "profit_factor": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "sl_exits": 0,
            "tp_exits": 0,
            "avg_confidence": 0.0,
        }

    total = len(trades)
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    final_balance = starting_balance + total_pnl
    gross_profit = sum(wins) if wins else 0.0
    gross_loss = abs(sum(losses)) if losses else 0.0

    # Max drawdown (peak-to-trough of equity curve)
    equity = starting_balance
    peak = equity
    max_dd = 0.0
    for pnl in pnls:
        equity += pnl
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    sl_exits = sum(1 for t in trades if t.get("exit_reason") == "stop_loss")
    tp_exits = sum(1 for t in trades if t.get("exit_reason") == "take_profit")
    avg_conf = sum(t.get("confidence", 0) for t in trades) / total

    return {
        "total_trades": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / total * 100, 2),
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(final_balance, 2),
        "return_pct": round((final_balance - starting_balance) / starting_balance * 100, 2),
        "avg_pnl": round(total_pnl / total, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else float("inf"),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sl_exits": sl_exits,
        "tp_exits": tp_exits,
        "avg_confidence": round(avg_conf, 4),
    }


# =====================================================================
#  3. BACKTESTING ENGINE
# =====================================================================

async def run_backtest(
    candles: list,
    starting_balance: float = 1000.0,
    use_claude: bool = False,
    lookback: int = 200,
) -> tuple[list, dict]:
    """Run the strategy over historical candles.

    Parameters
    ----------
    candles : list[dict]
        Full list of OHLCV candle dicts (at least ``lookback + 1``).
    starting_balance : float
        Starting simulated USDT balance.
    use_claude : bool
        If True, use the real Claude-based trend agent (burns API credits).
        If False (default), use the local rule-based signal generator.
    lookback : int
        Number of trailing candles used for indicator computation.

    Returns
    -------
    (trades, report) : tuple[list[dict], dict]
    """
    cfg = {
        "MIN_CONFIDENCE": config.MIN_CONFIDENCE,
        "MAX_POSITIONS": config.MAX_POSITIONS,
        "DAILY_LOSS_LIMIT_PCT": config.DAILY_LOSS_LIMIT_PCT,
        "RISK_PER_TRADE_PCT": config.RISK_PER_TRADE_PCT,
        "STOP_LOSS_PCT": config.STOP_LOSS_PCT,
        "TAKE_PROFIT_PCT": config.TAKE_PROFIT_PCT,
    }

    portfolio = {
        "usdt_balance": starting_balance,
        "open_positions": 0,
        "daily_loss_pct": 0.0,
        "daily_pnl": 0.0,
        "session_trades": 0,
    }

    completed_trades: list[dict] = []
    open_position: Optional[dict] = None  # only 1 position at a time for simplicity

    total_candles = len(candles)
    sim_start = lookback  # first index where we have enough history

    logger.info(
        "Backtest: %d candles, lookback=%d, sim range [%d..%d]",
        total_candles, lookback, sim_start, total_candles - 1,
    )

    signals_generated = 0
    trades_attempted = 0

    for i in range(sim_start, total_candles):
        window = candles[i - lookback: i + 1]  # trailing window including current

        # ── Compute indicators on the trailing window ────────────
        try:
            market_data = compute_indicators(window)
        except Exception as exc:
            logger.debug("Indicator error at candle %d: %s", i, exc)
            continue

        close_price = market_data.get("close") or 0.0
        high_price = candles[i].get("high", close_price)
        low_price = candles[i].get("low", close_price)

        # ── Check open position for SL / TP exits ────────────────
        if open_position is not None:
            pos = open_position
            hit_sl = False
            hit_tp = False

            if pos["side"] == "BUY":
                if low_price <= pos["stop_loss_price"]:
                    hit_sl = True
                    exit_price = pos["stop_loss_price"]
                elif high_price >= pos["take_profit_price"]:
                    hit_tp = True
                    exit_price = pos["take_profit_price"]
            else:  # SELL
                if high_price >= pos["stop_loss_price"]:
                    hit_sl = True
                    exit_price = pos["stop_loss_price"]
                elif low_price <= pos["take_profit_price"]:
                    hit_tp = True
                    exit_price = pos["take_profit_price"]

            if hit_sl or hit_tp:
                # Calculate P&L
                if pos["side"] == "BUY":
                    pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
                else:
                    pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

                trade_record = {
                    "side": pos["side"],
                    "entry_price": pos["entry_price"],
                    "exit_price": round(exit_price, 2),
                    "quantity": pos["quantity"],
                    "pnl": round(pnl, 2),
                    "exit_reason": "stop_loss" if hit_sl else "take_profit",
                    "entry_idx": pos["entry_idx"],
                    "exit_idx": i,
                    "confidence": pos["confidence"],
                    "reason": pos["reason"],
                }
                completed_trades.append(trade_record)

                portfolio["usdt_balance"] += pnl
                portfolio["open_positions"] = 0
                portfolio["daily_pnl"] += pnl
                if portfolio["usdt_balance"] > 0:
                    portfolio["daily_loss_pct"] = max(
                        0.0,
                        -portfolio["daily_pnl"] / starting_balance
                    )

                open_position = None

                logger.debug(
                    "Trade closed at candle %d: %s | pnl=$%.2f | reason=%s",
                    i, trade_record["side"], pnl,
                    trade_record["exit_reason"],
                )

        # ── Generate signal (skip if already in a position) ──────
        if open_position is not None:
            continue

        if use_claude:
            signal = run_trend_agent(market_data)
        else:
            signal = _local_trend_signal(market_data)

        signals_generated += 1

        if signal["signal"] == "HOLD":
            continue

        # ── Risk manager evaluation ──────────────────────────────
        risk_result = evaluate_trade(signal, portfolio, market_data, cfg)
        trades_attempted += 1

        if not risk_result.get("approved"):
            continue

        # ── Open a position ──────────────────────────────────────
        qty = risk_result["quantity"]
        open_position = {
            "side": signal["signal"],
            "entry_price": close_price,
            "quantity": qty,
            "stop_loss_price": risk_result["stop_loss_price"],
            "take_profit_price": risk_result["take_profit_price"],
            "entry_idx": i,
            "confidence": signal.get("confidence", 0),
            "reason": signal.get("reason", ""),
        }
        portfolio["open_positions"] = 1

        logger.debug(
            "Position opened at candle %d: %s %.6f BTC @ $%.2f",
            i, signal["signal"], qty, close_price,
        )

    # ── Force-close any remaining open position at last close ────
    if open_position is not None:
        pos = open_position
        exit_price = candles[-1].get("close", pos["entry_price"])
        if pos["side"] == "BUY":
            pnl = (exit_price - pos["entry_price"]) * pos["quantity"]
        else:
            pnl = (pos["entry_price"] - exit_price) * pos["quantity"]

        completed_trades.append({
            "side": pos["side"],
            "entry_price": pos["entry_price"],
            "exit_price": round(exit_price, 2),
            "quantity": pos["quantity"],
            "pnl": round(pnl, 2),
            "exit_reason": "end_of_data",
            "entry_idx": pos["entry_idx"],
            "exit_idx": total_candles - 1,
            "confidence": pos["confidence"],
            "reason": pos["reason"],
        })
        portfolio["usdt_balance"] += pnl

    # ── Generate performance report ──────────────────────────────
    report = generate_performance_report(completed_trades, starting_balance)
    report["candles_processed"] = total_candles - sim_start
    report["signals_generated"] = signals_generated
    report["trades_attempted"] = trades_attempted

    return completed_trades, report


# =====================================================================
#  4. CLAUDE ANALYSIS AGENT
# =====================================================================

ANALYSIS_SYSTEM_PROMPT = (
    "You are a quantitative trading analyst reviewing backtest results for an "
    "automated BTC/USDT trading bot. Analyze the performance data and provide a "
    "concise, actionable assessment. Focus on: win rate vs benchmark (50%), "
    "profit factor (target > 1.5), drawdown risk, and whether the strategy "
    "shows genuine edge or is over-fitted. Be direct and critical. If the "
    "results are poor, say so clearly. End with 3 specific improvements to "
    "the entry/exit rules."
)


def analyze_backtest_with_claude(report: dict, trades: list) -> str:
    """Send backtest results to Claude for critical analysis.

    Parameters
    ----------
    report : dict
        Performance metrics from ``generate_performance_report``.
    trades : list[dict]
        Completed trade list.

    Returns
    -------
    str
        Claude's analysis text, or an error message on failure.
    """
    # Pick the 5 worst trades by P&L
    sorted_trades = sorted(trades, key=lambda t: t.get("pnl", 0))
    worst_5 = sorted_trades[:5]

    user_prompt = (
        f"Backtest report: {json.dumps(report, indent=2)}\n\n"
        f"Sample losing trades: {json.dumps(worst_5, indent=2)}"
    )

    try:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key == "your_anthropic_api_key_here":
            # ── FREE local analysis when no API key ──────────────────
            return _local_analysis(report, worst_5)

        client = anthropic.Anthropic(api_key=api_key)

        response = client.messages.create(
            model="claude-haiku-4-5-20250414",
            max_tokens=1500,
            system=ANALYSIS_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )

        return response.content[0].text

    except Exception as exc:  # noqa: BLE001
        logger.warning("Claude analysis failed: %s — using local analysis", exc)
        return _local_analysis(report, worst_5)


def _local_analysis(report: dict, worst_trades: list) -> str:
    """Generate a local text analysis of backtest results (no API needed)."""
    total = report.get("total_trades", 0)
    if total == 0:
        return "No trades were executed during this backtest period."

    wr = report.get("win_rate", 0)
    pf = report.get("profit_factor", 0)
    dd = report.get("max_drawdown_pct", 0)
    ret = report.get("return_pct", 0)
    sl = report.get("sl_exits", 0)
    tp = report.get("tp_exits", 0)

    lines = ["BACKTEST PERFORMANCE ANALYSIS (Local)", "=" * 40, ""]

    # Overall verdict
    if ret > 0 and wr > 50:
        lines.append(f"VERDICT: POSITIVE — {ret:+.2f}% return with {wr:.1f}% win rate.")
    elif ret > 0:
        lines.append(f"VERDICT: MARGINAL — Profitable ({ret:+.2f}%) but win rate ({wr:.1f}%) needs improvement.")
    else:
        lines.append(f"VERDICT: NEGATIVE — {ret:.2f}% return. Strategy needs refinement.")

    lines.append("")

    # Key metrics
    lines.append("KEY METRICS:")
    lines.append(f"  Win Rate: {wr:.1f}% {'✅' if wr > 50 else '⚠️'} (target: >50%)")
    pf_str = f"{pf:.2f}" if pf != float('inf') else "inf"
    lines.append(f"  Profit Factor: {pf_str} {'✅' if pf > 1.5 else '⚠️'} (target: >1.5)")
    lines.append(f"  Max Drawdown: {dd:.2f}% {'✅' if dd < 10 else '⚠️'} (target: <10%)")
    lines.append(f"  SL/TP Exits: {sl}/{tp} {'⚠️ too many stop losses' if sl > tp else '✅ good TP ratio'}")

    lines.append("")
    lines.append("SUGGESTIONS:")
    if sl > tp:
        lines.append("  1. Stop-loss is getting hit too often — consider widening SL or tightening entry conditions.")
    if wr < 50:
        lines.append("  2. Win rate below 50% — add additional confirmation filters before entering trades.")
    if dd > 10:
        lines.append("  3. Drawdown is high — reduce position size or add a cooldown period after consecutive losses.")
    if total < 10:
        lines.append("  Note: Very few trades — the backtest window may be too short for reliable conclusions.")
        lines.append("  Run with more candles (longer time period) for better statistical significance.")

    return "\n".join(lines)


# =====================================================================
#  5. MAIN
# =====================================================================

async def main():
    """Fetch candles, run backtest, print report, run Claude analysis."""

    print("=" * 60)
    print("  BACKTEST ENGINE -- BTC/USDT 5m Trend-Following")
    print("=" * 60)

    # ── 1. Fetch 1000 candles ────────────────────────────────────
    print("\n[1/4] Fetching 1000 candles from Binance ...")
    candles = await fetch_ohlcv(config.SYMBOL, config.TIMEFRAME, limit=1000)
    print(f"       Received {len(candles)} candles")
    print(f"       First: {datetime.fromtimestamp(candles[0]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"       Last:  {datetime.fromtimestamp(candles[-1]['timestamp']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")

    # ── 2. Run backtest ──────────────────────────────────────────
    print("\n[2/4] Running backtest (local rule-based signals) ...")
    start_time = time.time()
    trades, report = await run_backtest(candles, starting_balance=1000.0, use_claude=False)
    elapsed = time.time() - start_time
    print(f"       Completed in {elapsed:.1f}s")

    # ── 3. Print performance report ──────────────────────────────
    print("\n" + "=" * 60)
    print("  PERFORMANCE REPORT")
    print("=" * 60)
    print(f"  Candles processed    : {report.get('candles_processed', 0)}")
    print(f"  Signals generated    : {report.get('signals_generated', 0)}")
    print(f"  Trades attempted     : {report.get('trades_attempted', 0)}")
    print(f"  Total trades closed  : {report['total_trades']}")
    print(f"  Wins / Losses        : {report.get('wins', 0)} / {report.get('losses', 0)}")
    print(f"  Win rate             : {report['win_rate']}%")
    print(f"  Total P&L            : ${report['total_pnl']}")
    print(f"  Final balance        : ${report['final_balance']}  (started at $1000)")
    print(f"  Return               : {report['return_pct']}%")
    print(f"  Avg P&L per trade    : ${report['avg_pnl']}")
    print(f"  Profit factor        : {report['profit_factor']}")
    print(f"  Avg win / Avg loss   : ${report.get('avg_win', 0)} / ${report.get('avg_loss', 0)}")
    print(f"  Best / Worst trade   : ${report['best_trade']} / ${report['worst_trade']}")
    print(f"  Max drawdown         : {report['max_drawdown_pct']}%")
    print(f"  SL exits / TP exits  : {report['sl_exits']} / {report['tp_exits']}")
    print(f"  Avg confidence       : {report['avg_confidence']}")
    print("=" * 60)

    # Print trade log
    if trades:
        print("\n  TRADE LOG (all trades):")
        print(f"  {'#':>3}  {'Side':<5} {'Entry':>10} {'Exit':>10} {'Qty':>10} {'P&L':>10} {'Exit Reason':<14} {'Conf':>6}")
        print(f"  {'-'*3}  {'-'*5} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*14} {'-'*6}")
        for idx, t in enumerate(trades, 1):
            print(
                f"  {idx:>3}  {t['side']:<5} "
                f"${t['entry_price']:>9,.2f} ${t['exit_price']:>9,.2f} "
                f"{t['quantity']:>10.6f} "
                f"${t['pnl']:>9.2f} "
                f"{t['exit_reason']:<14} "
                f"{t['confidence']:>6.2f}"
            )
        print()

    # ── 4. Claude analysis ───────────────────────────────────────
    print("[3/4] Sending results to Claude for analysis ...")
    analysis = analyze_backtest_with_claude(report, trades)

    # Save to file
    analysis_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "backtest_analysis.txt"
    )
    with open(analysis_path, "w", encoding="utf-8") as f:
        f.write("BACKTEST ANALYSIS -- Generated by Claude\n")
        f.write(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write("=" * 60 + "\n\n")
        f.write(analysis)
        f.write("\n")

    print(f"       Analysis saved to: {analysis_path}")

    # Print first part of analysis
    print("\n" + "=" * 60)
    print("  CLAUDE'S ANALYSIS (preview)")
    print("=" * 60)
    # Print first 3 paragraphs
    paragraphs = [p.strip() for p in analysis.split("\n\n") if p.strip()]
    for p in paragraphs[:3]:
        print(f"\n{p}")
    if len(paragraphs) > 3:
        print(f"\n  ... ({len(paragraphs) - 3} more paragraphs in backtest_analysis.txt)")
    print("\n" + "=" * 60)

    # Save report as JSON too
    report_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "backtest_report.json"
    )
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({"report": report, "trades": trades}, f, indent=2)
    print(f"\n[4/4] Full report saved to: {report_path}")
    print("\nBacktest complete.")


if __name__ == "__main__":
    asyncio.run(main())
