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
from core.database import init_db, save_trade, save_decision, get_open_trades, update_trade_status
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


async def sync_portfolio(db, current_price: float, portfolio: dict, testnet_flag: bool) -> None:
    """Check open trades and close them if TP or SL is hit."""
    open_trades = get_open_trades(db)
    
    # Sync portfolio counter with actual open trades in DB
    portfolio["open_positions"] = len(open_trades)

    for trade in open_trades:
        side = trade["side"].lower()
        entry_price = trade["price"]
        quantity = trade["quantity"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        
        close_trade = False
        pnl = 0.0
        reason = ""

        if side == "buy":
            pnl = (current_price - entry_price) * quantity
            if sl > 0 and current_price <= sl:
                close_trade = True
                reason = "Stop Loss Hit"
            elif tp > 0 and current_price >= tp:
                close_trade = True
                reason = "Take Profit Hit"
        elif side == "sell":
            pnl = (entry_price - current_price) * quantity
            if sl > 0 and current_price >= sl:
                close_trade = True
                reason = "Stop Loss Hit"
            elif tp > 0 and current_price <= tp:
                close_trade = True
                reason = "Take Profit Hit"

        if close_trade:
            logger.info("Closing trade ID %s: %s at $%.2f (PnL: $%.2f)", trade["id"], reason, current_price, pnl)
            
            # Execute counter-trade
            exit_side = "sell" if side == "buy" else "buy"
            order_result = await place_order(
                exit_side, trade["symbol"], quantity, testnet=testnet_flag
            )
            
            if order_result.get("status") != "failed":
                update_trade_status(db, trade["id"], "closed", current_price, pnl)
                portfolio["open_positions"] -= 1
                portfolio["daily_pnl"] += pnl
                
                try:
                    await send_telegram_alert(
                        f"*TRADE CLOSED*\n"
                        f"Reason: `{reason}`\n"
                        f"Side: `{exit_side.upper()}`\n"
                        f"Price: `${current_price:,.2f}`\n"
                        f"PnL: `${pnl:,.2f}`"
                    )
                except Exception:
                    pass

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

            # ── b.5) Sync portfolio & check stops ───────────────────
            await sync_portfolio(db, market_data.get("close", 0), portfolio, testnet_flag)

            # ── c) Orchestrator gate ────────────────────────────────
            orch_result = run_orchestrator(market_data, portfolio)
            logger.info(
                "Orchestrator: analyze=%s  risk=%s  conf=%.2f  reason=%s",
                orch_result.get("analyze"),
                orch_result.get("risk_level"),
                orch_result.get("confidence", 0),
                orch_result.get("reason"),
            )

            # ── d) If orchestrator says no, save decision and sleep ───
            if not orch_result.get("analyze", False):
                reason = orch_result.get("reason", "unknown")
                logger.info(
                    "Orchestrator blocked analysis: %s -- sleeping 60s",
                    reason,
                )

                # Log BB width for debugging
                bb_w = market_data.get("bb_upper", 0) - market_data.get("bb_lower", 0)
                close_p = market_data.get("close", 1)
                logger.info(
                    "  BB width: $%.2f (%.3f%% of close=$%.2f)",
                    bb_w, (bb_w / close_p * 100) if close_p else 0, close_p,
                )

                # Save blocked decision to DB (so dashboard shows history)
                blocked_decision = {
                    "timestamp": int(time.time()),
                    "signal": "HOLD",
                    "confidence": orch_result.get("confidence", 0),
                    "reason": f"orchestrator_blocked: {reason}",
                    "approved": 0,
                }
                try:
                    save_decision(db, blocked_decision)
                except Exception:
                    pass

                # Send periodic heartbeat so user knows bot is alive
                if not hasattr(run_bot, "_blocked_count"):
                    run_bot._blocked_count = 0
                run_bot._blocked_count += 1

                if run_bot._blocked_count % 60 == 1:  # First block + every ~1 hour
                    try:
                        await send_telegram_alert(
                            f"🤖 *BOT ALIVE* — Monitoring market\n"
                            f"Status: `HOLD` (blocked: {reason})\n"
                            f"BTC: `${close_p:,.2f}` | RSI: `{market_data.get('rsi', 0):.1f}`\n"
                            f"Trend: `{market_data.get('trend', 'unknown')}` | "
                            f"BB width: `${bb_w:.2f}`"
                        )
                    except Exception:
                        pass

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
                trade_status = "open" if order_result.get("status") != "failed" else "failed"
                trade_record = {
                    "timestamp": int(time.time()),
                    "symbol": config.SYMBOL,
                    "side": side,
                    "price": market_data.get("close", 0),
                    "quantity": qty,
                    "reason": signal.get("reason", ""),
                    "pnl": 0.0,
                    "status": trade_status,
                    "stop_loss": risk_result.get("stop_loss_price", 0),
                    "take_profit": risk_result.get("take_profit_price", 0),
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
