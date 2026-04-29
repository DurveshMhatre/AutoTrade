"""
main.py -- Automated BTC/USDT Trading Bot Loop
================================================
Ties together every module: data feed, indicators, orchestrator,
trend agent, risk manager, executor, database, and Telegram alerts.

Usage:
    python main.py
"""

import asyncio
import json
import logging
import os
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

# ── Core ────────────────────────────────────────────────────────────
from core.data_feed import fetch_ohlcv
from core.indicators import compute_indicators
from core.database import init_db, save_trade, save_decision
from core.risk_manager import evaluate_trade

# ── Agents ──────────────────────────────────────────────────────────
from agents.orchestrator import run_orchestrator
from agents.trend_agent import run_trend_agent

# ── Execution ───────────────────────────────────────────────────────
from execution.executor import place_order

# ── Monitoring ──────────────────────────────────────────────────────
from monitoring.telegram_alerts import send_telegram_alert

# ── Config ──────────────────────────────────────────────────────────
import config

# ── Logging setup ───────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-24s | %(levelname)-7s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bot")


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _ts() -> str:
    """Return a human-readable UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _config_dict() -> dict:
    """Return config values as a plain dict for the risk manager."""
    return {
        "MIN_CONFIDENCE": config.MIN_CONFIDENCE,
        "MAX_POSITIONS": config.MAX_POSITIONS,
        "DAILY_LOSS_LIMIT_PCT": config.DAILY_LOSS_LIMIT_PCT,
        "RISK_PER_TRADE_PCT": config.RISK_PER_TRADE_PCT,
        "STOP_LOSS_PCT": config.STOP_LOSS_PCT,
        "TAKE_PROFIT_PCT": config.TAKE_PROFIT_PCT,
    }


# ────────────────────────────────────────────────────────────────────
# Main bot loop
# ────────────────────────────────────────────────────────────────────

async def run_bot() -> None:
    """Run the trading bot in an infinite loop."""

    # 1. Load environment variables
    load_dotenv()

    testnet_flag = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    # 2. Initialise database
    db = init_db()

    # 3. Initialise portfolio state
    portfolio = {
        "usdt_balance": 1000.0,
        "open_positions": 0,
        "daily_loss_pct": 0.0,
        "daily_pnl": 0.0,
        "session_trades": 0,
    }

    # 4. Startup banner
    print("=" * 60)
    print(f"=== CRYPTO BOT STARTED === Testnet: {testnet_flag}")
    print(f"    Symbol   : {config.SYMBOL}")
    print(f"    Timeframe: {config.TIMEFRAME}")
    print(f"    Candles  : {config.CANDLE_LIMIT}")
    print(f"    Max Pos  : {config.MAX_POSITIONS}")
    print(f"    Min Conf : {config.MIN_CONFIDENCE}")
    print(f"    Risk/Trd : {config.RISK_PER_TRADE_PCT * 100:.1f}%")
    print(f"    SL / TP  : {config.STOP_LOSS_PCT * 100:.1f}% / {config.TAKE_PROFIT_PCT * 100:.1f}%")
    print("=" * 60)

    # 5. Infinite loop
    while True:
        try:
            cycle_start = time.time()
            logger.info("--- Cycle start: %s ---", _ts())

            # ── a) Fetch OHLCV candles ──────────────────────────────
            logger.info("Fetching OHLCV data for %s ...", config.SYMBOL)
            candles = await fetch_ohlcv(
                config.SYMBOL, config.TIMEFRAME, config.CANDLE_LIMIT
            )
            logger.info("Received %d candles", len(candles))

            # ── b) Compute indicators ──────────────────────────────
            market_data = compute_indicators(candles)
            logger.info(
                "Indicators: close=%.2f  RSI=%.2f  trend=%s  vol=%s",
                market_data.get("close", 0),
                market_data.get("rsi", 0),
                market_data.get("trend"),
                market_data.get("volatility"),
            )

            # ── c) Orchestrator gate ────────────────────────────────
            orch_result = run_orchestrator(market_data, portfolio)
            logger.info(
                "Orchestrator: analyze=%s  risk=%s  conf=%.2f  reason=%s",
                orch_result.get("analyze"),
                orch_result.get("risk_level"),
                orch_result.get("confidence", 0),
                orch_result.get("reason"),
            )

            # ── d) If orchestrator says no, sleep and continue ──────
            if not orch_result.get("analyze", False):
                logger.info(
                    "Orchestrator blocked analysis: %s -- sleeping 60s",
                    orch_result.get("reason"),
                )
                await asyncio.sleep(60)
                continue

            # ── e) Run trend agent ──────────────────────────────────
            signal = run_trend_agent(market_data)
            logger.info(
                "Trend signal: %s  confidence=%.2f  reason=%s",
                signal.get("signal"),
                signal.get("confidence", 0),
                signal.get("reason"),
            )

            # ── f) Risk manager evaluation ──────────────────────────
            risk_result = evaluate_trade(
                signal, portfolio, market_data, _config_dict()
            )
            logger.info(
                "Risk manager: approved=%s  reason=%s  qty=%s",
                risk_result.get("approved"),
                risk_result.get("reason"),
                risk_result.get("quantity"),
            )

            # ── g) Execute if approved ──────────────────────────────
            if risk_result.get("approved"):
                side = signal["signal"].lower()  # "buy" or "sell"
                qty = risk_result["quantity"]

                logger.info("Placing %s order for %.6f BTC ...", side.upper(), qty)
                order_result = await place_order(
                    side, config.SYMBOL, qty, testnet=testnet_flag
                )

                # Save trade to database
                trade_record = {
                    "timestamp": int(time.time()),
                    "symbol": config.SYMBOL,
                    "side": side,
                    "price": market_data.get("close", 0),
                    "quantity": qty,
                    "reason": signal.get("reason", ""),
                    "pnl": 0.0,
                    "status": order_result.get("status", "failed"),
                }
                save_trade(db, trade_record)

                # Save agent decision to database
                decision_record = {
                    "timestamp": int(time.time()),
                    "signal": signal["signal"],
                    "confidence": signal.get("confidence", 0),
                    "reason": signal.get("reason", ""),
                    "approved": 1,
                }
                save_decision(db, decision_record)

                # Send Telegram alert
                alert_msg = (
                    f"*TRADE EXECUTED*\n"
                    f"Side: `{side.upper()}`\n"
                    f"Symbol: `{config.SYMBOL}`\n"
                    f"Qty: `{qty:.6f} BTC`\n"
                    f"Price: `${market_data.get('close', 0):,.2f}`\n"
                    f"SL: `${risk_result.get('stop_loss_price', 0):,.2f}`\n"
                    f"TP: `${risk_result.get('take_profit_price', 0):,.2f}`\n"
                    f"Confidence: `{signal.get('confidence', 0):.2f}`\n"
                    f"Risk: `${risk_result.get('risk_dollars', 0):.2f}`"
                )
                await send_telegram_alert(alert_msg)

                # Update portfolio
                portfolio["open_positions"] += 1
                portfolio["session_trades"] += 1

                logger.info(
                    "Trade complete: %s %.6f BTC @ $%.2f | "
                    "open_positions=%d | session_trades=%d",
                    side.upper(), qty,
                    market_data.get("close", 0),
                    portfolio["open_positions"],
                    portfolio["session_trades"],
                )
            else:
                # Save rejected decision
                decision_record = {
                    "timestamp": int(time.time()),
                    "signal": signal.get("signal", "HOLD"),
                    "confidence": signal.get("confidence", 0),
                    "reason": risk_result.get("reason", ""),
                    "approved": 0,
                }
                save_decision(db, decision_record)

            # ── h) Cycle summary ────────────────────────────────────
            elapsed = time.time() - cycle_start
            logger.info(
                "Cycle done in %.1fs | signal=%s conf=%.2f | "
                "approved=%s reason=%s | next in 300s",
                elapsed,
                signal.get("signal"),
                signal.get("confidence", 0),
                risk_result.get("approved"),
                risk_result.get("reason"),
            )

            # ── i) Sleep 300s (5-minute candle rhythm) ──────────────
            await asyncio.sleep(300)

        # 6. Clean shutdown on Ctrl+C
        except KeyboardInterrupt:
            print("\nBot stopped cleanly")
            break

        # 7. Unexpected errors -- log, alert, and continue
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.error("UNEXPECTED ERROR: %s\n%s", exc, tb)

            try:
                await send_telegram_alert(
                    f"*BOT ERROR*\n```\n{str(exc)[:500]}\n```"
                )
            except Exception:  # noqa: BLE001
                pass

            logger.info("Sleeping 30s before retry ...")
            await asyncio.sleep(30)


# ────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\nBot stopped cleanly")
