"""
main.py -- Automated BTC/USDT Trading Bot Loop
================================================
Ties together every module: data feed, indicators, orchestrator,
trend agent, risk manager, executor, database, and Telegram alerts.

Phase 1 Pipeline (Elite Upgrade):
  1. Fetch 5M candles (existing)
  2. Fetch MTF candles — 1H, 4H, 1D (NEW)
  3. Compute indicators — 5M + per-timeframe (existing + NEW)
  4. Run Regime Agent (NEW)
  5. Run MTF Confluence Agent (NEW)
  6. Run Sentiment Agent (NEW)
  7. Run Enhanced Orchestrator (passes regime + mtf + sentiment)
  8. If approved → Run Trend Agent (existing)
  9. Risk Manager + Execute (existing)

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
from core.data_feed import fetch_ohlcv, fetch_multi_timeframe
from core.indicators import compute_indicators, compute_mtf_indicators
from core.database import init_db, save_trade, save_decision, get_open_trades, update_trade_status, save_regime
from core.risk_manager import evaluate_trade

# ── Agents ──────────────────────────────────────────────────────────
from agents.orchestrator import run_orchestrator
from agents.trend_agent import run_trend_agent
from agents.regime_agent import run_regime_agent
from agents.mtf_agent import run_mtf_agent
from agents.sentiment_agent import run_sentiment_agent

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
    print(f"=== CRYPTO BOT STARTED (ELITE v1) === Testnet: {testnet_flag}")
    print(f"    Symbol   : {config.SYMBOL}")
    print(f"    Timeframe: {config.TIMEFRAME}")
    print(f"    Candles  : {config.CANDLE_LIMIT}")
    print(f"    Max Pos  : {config.MAX_POSITIONS}")
    print(f"    Min Conf : {config.MIN_CONFIDENCE}")
    print(f"    Risk/Trd : {config.RISK_PER_TRADE_PCT * 100:.1f}%")
    print(f"    SL / TP  : {config.STOP_LOSS_PCT * 100:.1f}% / {config.TAKE_PROFIT_PCT * 100:.1f}%")
    print(f"    MTF TFs  : {list(config.MTF_TIMEFRAMES.keys())}")
    print(f"    AI Mode  : {config.USE_AI_AGENTS}")
    print("=" * 60)

    # 5. Infinite loop
    last_error_str = None
    while True:
        try:
            cycle_start = time.time()
            logger.info("--- Cycle start: %s ---", _ts())

            # ── a) Fetch OHLCV candles (5M — primary timeframe) ────
            logger.info("Fetching OHLCV data for %s ...", config.SYMBOL)
            candles = await fetch_ohlcv(
                config.SYMBOL, config.TIMEFRAME, config.CANDLE_LIMIT
            )
            logger.info("Received %d candles (5M)", len(candles))

            # ── b) Compute 5M indicators ───────────────────────────
            market_data = compute_indicators(candles)
            logger.info(
                "Indicators: close=%.2f  RSI=%.2f  trend=%s  vol=%s  ADX=%.1f",
                market_data.get("close", 0),
                market_data.get("rsi", 0),
                market_data.get("trend"),
                market_data.get("volatility"),
                market_data.get("adx") or 0,
            )

            # ── b.5) Sync portfolio & check stops ──────────────────
            await sync_portfolio(db, market_data.get("close", 0), portfolio, testnet_flag)

            # ── c) Fetch MTF candles (1H, 4H, 1D) — Phase 1 ───────
            logger.info("Fetching multi-timeframe data ...")
            mtf_candles = await fetch_multi_timeframe(
                config.SYMBOL, config.MTF_TIMEFRAMES
            )

            # ── d) Compute MTF indicators ──────────────────────────
            mtf_indicators = {"5m": market_data}
            for tf, tf_candles in mtf_candles.items():
                if tf_candles:
                    mtf_indicators[tf] = compute_mtf_indicators(tf_candles)
                    logger.info(
                        "  %s: close=%.2f  trend=%s  ADX=%.1f  RSI=%.1f",
                        tf.upper(),
                        mtf_indicators[tf].get("close") or 0,
                        mtf_indicators[tf].get("trend"),
                        mtf_indicators[tf].get("adx") or 0,
                        mtf_indicators[tf].get("rsi") or 0,
                    )
                else:
                    mtf_indicators[tf] = {}
                    logger.warning("  %s: no data available", tf.upper())

            # ── e) Run Regime Agent — Phase 1 ──────────────────────
            regime_result = run_regime_agent(mtf_indicators)
            logger.info(
                "Regime: %s (conf=%.2f, bias=%s, multiplier=%.2f)",
                regime_result.get("regime"),
                regime_result.get("confidence", 0),
                regime_result.get("strategy_bias"),
                regime_result.get("position_size_multiplier", 0),
            )

            # ── f) Run MTF Confluence Agent — Phase 1 ──────────────
            mtf_result = run_mtf_agent(mtf_indicators)
            logger.info(
                "MTF: score=%+d  daily=%s  4H=%s  approved=%s",
                mtf_result.get("confluence_score", 0),
                mtf_result.get("daily_bias"),
                mtf_result.get("4h_structure"),
                mtf_result.get("trade_approved"),
            )

            # ── g) Run Sentiment Agent — Phase 1 ───────────────────
            sentiment_result = await run_sentiment_agent()
            logger.info(
                "Sentiment: F&G=%d(%s)  funding=%s  news=%s  combined=%+d  bias=%s",
                sentiment_result.get("fear_greed_score", 50),
                sentiment_result.get("fear_greed_label"),
                sentiment_result.get("funding_signal"),
                sentiment_result.get("news_sentiment"),
                sentiment_result.get("combined_sentiment", 0),
                sentiment_result.get("trade_bias_adjustment"),
            )

            # ── h) Save regime snapshot to DB ──────────────────────
            try:
                save_regime(db, {
                    "timestamp": int(time.time()),
                    "regime": regime_result.get("regime", "UNKNOWN"),
                    "confidence": regime_result.get("confidence", 0),
                    "strategy_bias": regime_result.get("strategy_bias", ""),
                    "adx": market_data.get("adx") or 0,
                    "confluence_score": mtf_result.get("confluence_score", 0),
                    "sentiment_score": sentiment_result.get("combined_sentiment", 0),
                    "fear_greed": sentiment_result.get("fear_greed_score", 50),
                    "funding_signal": sentiment_result.get("funding_signal", "neutral"),
                })
            except Exception:
                pass

            # ── i) Orchestrator gate (enhanced with Phase 1 data) ──
            orch_result = run_orchestrator(
                market_data, portfolio,
                regime_data=regime_result,
                mtf_data=mtf_result,
                sentiment_data=sentiment_result,
            )
            logger.info(
                "Orchestrator: analyze=%s  risk=%s  conf=%.2f  reason=%s",
                orch_result.get("analyze"),
                orch_result.get("risk_level"),
                orch_result.get("confidence", 0),
                orch_result.get("reason"),
            )

            # ── j) If orchestrator says no, save decision and sleep ──
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
                            f"🤖 *BOT ALIVE (ELITE v1)* — Monitoring market\n"
                            f"Status: `HOLD` (blocked: {reason})\n"
                            f"BTC: `${close_p:,.2f}` | RSI: `{market_data.get('rsi', 0):.1f}`\n"
                            f"Regime: `{regime_result.get('regime', 'N/A')}` | "
                            f"MTF: `{mtf_result.get('confluence_score', 0):+d}` | "
                            f"F&G: `{sentiment_result.get('fear_greed_score', 50)}`"
                        )
                    except Exception:
                        pass

                await asyncio.sleep(60)
                continue

            # ── k) Run trend agent ─────────────────────────────────
            signal = run_trend_agent(market_data)
            logger.info(
                "Trend signal: %s  confidence=%.2f  reason=%s",
                signal.get("signal"),
                signal.get("confidence", 0),
                signal.get("reason"),
            )

            # ── l) Risk manager evaluation ─────────────────────────
            risk_result = evaluate_trade(
                signal, portfolio, market_data, _config_dict()
            )
            logger.info(
                "Risk manager: approved=%s  reason=%s  qty=%s",
                risk_result.get("approved"),
                risk_result.get("reason"),
                risk_result.get("quantity"),
            )

            # ── m) Execute if approved ─────────────────────────────
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
                    f"*TRADE EXECUTED (ELITE v1)*\n"
                    f"Side: `{side.upper()}`\n"
                    f"Symbol: `{config.SYMBOL}`\n"
                    f"Qty: `{qty:.6f} BTC`\n"
                    f"Price: `${market_data.get('close', 0):,.2f}`\n"
                    f"SL: `${risk_result.get('stop_loss_price', 0):,.2f}`\n"
                    f"TP: `${risk_result.get('take_profit_price', 0):,.2f}`\n"
                    f"Confidence: `{signal.get('confidence', 0):.2f}`\n"
                    f"Risk: `${risk_result.get('risk_dollars', 0):.2f}`\n"
                    f"Regime: `{regime_result.get('regime', 'N/A')}`\n"
                    f"MTF Score: `{mtf_result.get('confluence_score', 0):+d}`\n"
                    f"Sentiment: `{sentiment_result.get('combined_sentiment', 0):+d}`"
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

            # ── n) Cycle summary ───────────────────────────────────
            elapsed = time.time() - cycle_start
            logger.info(
                "Cycle done in %.1fs | regime=%s | MTF=%+d | sentiment=%+d | "
                "signal=%s conf=%.2f | approved=%s reason=%s | next in 300s",
                elapsed,
                regime_result.get("regime"),
                mtf_result.get("confluence_score", 0),
                sentiment_result.get("combined_sentiment", 0),
                signal.get("signal"),
                signal.get("confidence", 0),
                risk_result.get("approved"),
                risk_result.get("reason"),
            )

            # ── o) Sleep 300s (5-minute candle rhythm) ─────────────
            last_error_str = None
            await asyncio.sleep(300)

        # 6. Clean shutdown on Ctrl+C
        except KeyboardInterrupt:
            print("\nBot stopped cleanly")
            break

        # 7. Unexpected errors -- log, alert, and continue
        except Exception as exc:  # noqa: BLE001
            tb = traceback.format_exc()
            logger.error("UNEXPECTED ERROR: %s\n%s", exc, tb)

            current_error_str = str(exc)
            if current_error_str != last_error_str:
                try:
                    await send_telegram_alert(
                        f"*BOT ERROR*\n```\n{current_error_str[:500]}\n```"
                    )
                    last_error_str = current_error_str
                except Exception:  # noqa: BLE001
                    pass
            else:
                logger.info("Skipping Telegram alert (same error as last time)")

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
