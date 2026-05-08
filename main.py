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
import aiohttp
import time
import traceback
from datetime import datetime, timezone

from dotenv import load_dotenv

# ── Core ────────────────────────────────────────────────────────────
from core.data_feed import fetch_ohlcv, fetch_multi_timeframe, fetch_order_book
from core.indicators import compute_indicators, compute_mtf_indicators, compute_vwap_and_poc
from core.database import init_db, save_trade, save_decision, get_open_trades, update_trade_status, save_regime, update_trade_quantity, save_autopsy, get_trade_by_id
from core.risk_manager import evaluate_trade, compute_portfolio_heat, compute_kelly_position_size
from core.trade_manager import TrailingStopManager

# ── Agents ──────────────────────────────────────────────────────────
from agents.orchestrator import run_orchestrator
from agents.trend_agent import run_trend_agent
from agents.regime_agent import run_regime_agent
from agents.mtf_agent import run_mtf_agent
from agents.sentiment_agent import run_sentiment_agent
from agents.sr_agent import run_sr_agent
from agents.order_flow_agent import run_order_flow_agent
from agents.news_agent import run_news_agent
from agents.autopsy_agent import run_autopsy_agent

# ── Execution ───────────────────────────────────────────────────────
from execution.executor import place_order, smart_entry

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


# Global state for TrailingStopManagers
_active_trade_managers = {}

async def background_autopsy(db, trade_id: int, market_data: dict):
    """Run autopsy in background without blocking the main loop."""
    try:
        # Give DB a moment to flush
        await asyncio.sleep(2)
        trade = get_trade_by_id(db, trade_id)
        if not trade:
            return
            
        logger.info(f"Running Autopsy on closed trade {trade_id}...")
        autopsy_result = run_autopsy_agent(trade, market_data)
        
        save_autopsy(db, {
            "timestamp": int(time.time()),
            "trade_id": trade_id,
            "entry_grade": autopsy_result.get("entry_grade"),
            "exit_quality": autopsy_result.get("exit_quality"),
            "root_cause": autopsy_result.get("root_cause"),
            "lesson": autopsy_result.get("lesson"),
            "pattern_detected": autopsy_result.get("pattern_detected", 0),
            "pattern_description": autopsy_result.get("pattern_description")
        })
        logger.info(f"Autopsy for {trade_id} saved. Grade: {autopsy_result.get('entry_grade')} | Lesson: {autopsy_result.get('lesson')}")
    except Exception as e:
        logger.error(f"Background autopsy failed for {trade_id}: {e}")

async def sync_portfolio(db, market_data: dict, portfolio: dict, testnet_flag: bool, current_regime: str) -> None:
    """Check open trades and close them via TrailingStopManager logic."""
    open_trades = get_open_trades(db)
    current_price = market_data.get("close", 0)
    current_atr = market_data.get("atr", current_price * 0.02)
    
    # Check Heat Limit
    heat_data = compute_portfolio_heat(open_trades, portfolio["usdt_balance"], current_regime)
    portfolio["heat_pct"] = heat_data["current_heat_pct"]
    portfolio["max_heat_pct"] = heat_data["max_heat_pct"]
    portfolio["new_trade_blocked"] = heat_data["new_trade_blocked"]
    
    # Sync portfolio counter with actual open trades in DB
    portfolio["open_positions"] = len(open_trades)

    for trade in open_trades:
        trade_id = trade["id"]
        side = trade["side"].lower()
        entry_price = trade["price"]
        quantity = trade["quantity"]
        
        if trade_id not in _active_trade_managers:
            # Rebuild manager if missing
            _active_trade_managers[trade_id] = TrailingStopManager(entry_price, side, current_atr, current_regime)
            
        manager = _active_trade_managers[trade_id]
        update_result = manager.update(current_price, current_atr)
        
        # Save new trailing stop to DB if changed
        if manager.trailing_stop != trade["stop_loss"]:
            try:
                cursor = db.cursor()
                cursor.execute("UPDATE trades SET stop_loss = ? WHERE id = ?", (manager.trailing_stop, trade_id))
                db.commit()
            except Exception as e:
                logger.error(f"Failed to update trailing stop in DB: {e}")

        for action in update_result["actions"]:
            act_type = action["action"]
            reason = action["reason"]
            
            pnl = 0.0
            if side == "buy":
                pnl = (current_price - entry_price) * quantity
            else:
                pnl = (entry_price - current_price) * quantity
                
            if act_type == "partial_close":
                close_qty = quantity * action["pct"]
                # Skip micro orders below binance limit
                if close_qty < 0.0001:
                    continue
                    
                partial_pnl = pnl * action["pct"]
                logger.info("Partial close trade ID %s: %s at $%.2f", trade_id, reason, current_price)
                
                exit_side = "sell" if side == "buy" else "buy"
                order_result = await place_order(exit_side, trade["symbol"], close_qty, testnet=testnet_flag)
                if order_result.get("status") != "failed":
                    new_qty = quantity - close_qty
                    update_trade_quantity(db, trade_id, new_qty)
                    portfolio["daily_pnl"] += partial_pnl
                    try:
                        await send_telegram_alert(f"*PARTIAL TAKE PROFIT*\nReason: `{reason}`\nSide: `{exit_side.upper()}`\nPrice: `${current_price:,.2f}`\nPnL: `${partial_pnl:,.2f}`\nRemaining Qty: `{new_qty:.6f}`")
                    except Exception:
                        pass
                        
            elif act_type == "close_all":
                logger.info("Closing full trade ID %s: %s at $%.2f (PnL: $%.2f)", trade_id, reason, current_price, pnl)
                exit_side = "sell" if side == "buy" else "buy"
                order_result = await place_order(exit_side, trade["symbol"], quantity, testnet=testnet_flag)
                
                if order_result.get("status") != "failed":
                    update_trade_status(db, trade_id, "closed", current_price, pnl)
                    portfolio["open_positions"] -= 1
                    portfolio["daily_pnl"] += pnl
                    if trade_id in _active_trade_managers:
                        del _active_trade_managers[trade_id]
                    try:
                        await send_telegram_alert(f"*TRADE CLOSED*\nReason: `{reason}`\nSide: `{exit_side.upper()}`\nPrice: `${current_price:,.2f}`\nPnL: `${pnl:,.2f}`")
                    except Exception:
                        pass
                    
                    # Trigger autopsy
                    asyncio.create_task(background_autopsy(db, trade_id, market_data))

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

            # ── b.5) Fetch MTF candles (1H, 4H, 1D) — Phase 1 ───────
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
            
            # ── e.1) Strategy Switching — Phase 4 ──────────────────
            # Map regime agent's strategy_bias output to STRATEGIES keys
            _BIAS_TO_STRATEGY = {
                "trend_follow": "TREND_FOLLOWING",
                "mean_revert": "MEAN_REVERSION",
                "flat": "FLAT",
                "short_only": "FLAT",  # Spot bot can't short, so treat as flat
            }
            strategy_bias = regime_result.get("strategy_bias", "flat")
            strategy_key = _BIAS_TO_STRATEGY.get(strategy_bias, "FLAT")
            active_strategy = config.STRATEGIES.get(strategy_key, config.STRATEGIES["FLAT"])
            logger.info("Active Strategy: %s → %s (Action: %s)", strategy_bias, strategy_key, active_strategy["action"])

            if active_strategy["action"] == "NO_TRADE":
                logger.info("Regime is unfavorable. Strategy dictates NO_TRADE. Sleeping 5 mins.")
                # Send periodic heartbeat
                if not hasattr(run_bot, "_flat_count"):
                    run_bot._flat_count = 0
                run_bot._flat_count += 1
                if run_bot._flat_count % 12 == 1: # Once per hour
                    try:
                        await send_telegram_alert(
                            f"🤖 *BOT ALIVE (ELITE v4)* — Monitoring market\n"
                            f"Status: `FLAT` (Regime: {regime_result.get('regime', 'N/A')})\n"
                            f"BTC: `${market_data.get('close', 0):,.2f}` | Heat: `{portfolio.get('heat_pct', 0):.1f}%`"
                        )
                    except Exception:
                        pass
                await asyncio.sleep(300)
                continue

            # ── e.5) Sync portfolio & check trailing stops ─────────
            await sync_portfolio(db, market_data, portfolio, testnet_flag, regime_result.get("regime", "default"))

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
                "Sentiment: F&G=%d(%s)  funding=%s  combined=%+d  bias=%s",
                sentiment_result.get("fear_greed_score", 50),
                sentiment_result.get("fear_greed_label"),
                sentiment_result.get("funding_signal"),
                sentiment_result.get("combined_sentiment", 0),
                sentiment_result.get("trade_bias_adjustment"),
            )

            # ── g.1) Fetch Phase 2 Data ────────────────────────────
            logger.info("Fetching order book and news data...")
            import config as bot_config
            order_book = await fetch_order_book(bot_config.SYMBOL, bot_config.ORDER_BOOK_DEPTH)
            vwap_data = compute_vwap_and_poc(candles)
            
            # Note: News is fetched using CryptoPanic fallback internally in the news agent if no AI.
            # But the AI agent expects headlines. For simplicity, we just pass an empty list and let it fetch internally.
            # Wait, `run_news_agent` in `news_agent.py` expects headlines. Let's fetch them first.
            headlines = []
            cp_key = os.getenv("CRYPTOPANIC_API_KEY", "")
            if cp_key:
                try:
                    async with aiohttp.ClientSession() as session:
                        url = f"https://cryptopanic.com/api/v1/posts/?auth_token={cp_key}&public=true&filter=hot&currencies=BTC"
                        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                headlines = [{"title": r.get("title", ""), "source": r.get("source", {}).get("title", "unknown")} for r in data.get("results", [])[:10]]
                except Exception as e:
                    logger.warning("News fetch failed: %s", e)

            # ── g.2) Run Phase 2 Agents ────────────────────────────
            sr_result = run_sr_agent(mtf_candles.get("1h", candles), market_data.get("close", 0))
            logger.info("S&R: Supp=$%.2f  Res=$%.2f  Quality=%s", sr_result.get("nearest_support", 0), sr_result.get("nearest_resistance", 0), sr_result.get("entry_quality"))

            order_flow_result = run_order_flow_agent(order_book, vwap_data, market_data.get("close", 0))
            logger.info("Order Flow: Bias=%s  B/A Ratio=%.2f  VWAP=$%.2f", order_flow_result.get("order_flow_bias"), order_flow_result.get("bid_ask_ratio"), vwap_data.get("vwap", 0))

            news_result = run_news_agent(headlines)
            logger.info("News: Action=%s  Score=%.2f  MajorEvent=%s", news_result.get("trading_action"), news_result.get("weighted_sentiment_score", 0), news_result.get("major_event_detected"))

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

            # ── i) Run Trend Agent (Base Signal) ───────────────────
            signal = run_trend_agent(market_data)
            logger.info(
                "Trend signal: %s  confidence=%.2f  reason=%s",
                signal.get("signal"),
                signal.get("confidence", 0),
                signal.get("reason"),
            )

            # ── j) Master Orchestrator gate ─────────────────────────
            # Pass the active_strategy dict into the orchestrator so it knows our risk limits
            orch_result = run_orchestrator(
                market_data, portfolio,
                trend_data=signal,
                regime_data=regime_result,
                mtf_data=mtf_result,
                sentiment_data=sentiment_result,
                sr_data=sr_result,
                order_flow_data=order_flow_result,
                news_data=news_result,
                active_strategy=active_strategy,
            )
            logger.info(
                "Master Orchestrator: approved=%s  conf=%d  signal=%s  tier=%s  reason=%s",
                orch_result.get("trade_approved"),
                orch_result.get("conviction_score", 0),
                orch_result.get("final_signal"),
                orch_result.get("position_size_tier"),
                orch_result.get("veto_reason") or "None",
            )

            # ── k) If orchestrator says no, save decision and sleep ──
            if not orch_result.get("trade_approved", False) or portfolio.get("new_trade_blocked", False):
                reason = orch_result.get("veto_reason") or ("Heat Blocked" if portfolio.get("new_trade_blocked") else "unknown")
                logger.info(
                    "Orchestrator blocked trade: %s -- sleeping 60s",
                    reason,
                )

                # Save blocked decision to DB
                blocked_decision = {
                    "timestamp": int(time.time()),
                    "signal": "HOLD",
                    "confidence": orch_result.get("conviction_score", 0) / 100.0,
                    "reason": f"orchestrator_blocked: {reason}",
                    "approved": 0,
                }
                try:
                    save_decision(db, blocked_decision)
                except Exception:
                    pass

                # Send periodic heartbeat
                if not hasattr(run_bot, "_blocked_count"):
                    run_bot._blocked_count = 0
                run_bot._blocked_count += 1

                if run_bot._blocked_count % 60 == 1:
                    try:
                        await send_telegram_alert(
                            f"🤖 *BOT ALIVE (ELITE v3)* — Monitoring market\n"
                            f"Status: `HOLD` (blocked: {reason})\n"
                            f"BTC: `${market_data.get('close', 0):,.2f}` | Heat: `{portfolio.get('heat_pct', 0):.1f}%`\n"
                            f"Regime: `{regime_result.get('regime', 'N/A')}` | "
                            f"MTF: `{mtf_result.get('confluence_score', 0):+d}`"
                        )
                    except Exception:
                        pass

                await asyncio.sleep(60)
                continue

            # ── l) Dynamic Position Sizing (Kelly Criterion) ────────
            # Compute stats from trade history (last 30 closed trades)
            from core.database import get_recent_trades
            closed_trades = [t for t in get_recent_trades(db, limit=50) if t.get("status") == "closed"]
            if len(closed_trades) >= 10:
                wins = [t for t in closed_trades if t.get("pnl", 0) > 0]
                losses = [t for t in closed_trades if t.get("pnl", 0) <= 0]
                hist_win_rate = len(wins) / len(closed_trades)
                hist_avg_win = (sum(t["pnl"] / (t["price"] * t["quantity"]) for t in wins) / len(wins)) if wins else 0.03
                hist_avg_loss = (abs(sum(t["pnl"] / (t["price"] * t["quantity"]) for t in losses) / len(losses))) if losses else 0.015
                # Safety floor
                hist_avg_loss = max(hist_avg_loss, 0.005)
            else:
                # Conservative defaults until we have enough history
                hist_win_rate = 0.50
                hist_avg_win = 0.025
                hist_avg_loss = 0.015

            kelly_result = compute_kelly_position_size(
                portfolio_balance=portfolio["usdt_balance"],
                win_rate=hist_win_rate,
                avg_win_pct=hist_avg_win,
                avg_loss_pct=hist_avg_loss,
                signal_confidence=orch_result.get("conviction_score", 50) / 100.0,
                regime_multiplier=regime_result.get("position_size_multiplier", 1.0),
                sentiment_adj=0.0
            )
            
            # Apply Tier from Master Orchestrator
            _TIER_MAP = {"full": 1.0, "100": 1.0, "75": 0.75, "50": 0.50}
            tier_raw = str(orch_result.get("position_size_tier", "50")).lower()
            tier_multiplier = _TIER_MAP.get(tier_raw, 0.50)
            kelly_usd = kelly_result["position_usd"] * tier_multiplier
            
            # Safety check vs balance
            kelly_usd = min(kelly_usd, portfolio["usdt_balance"])
            
            # Calculate quantity and entry levels
            entry_price = market_data.get("close", 0)
            atr = market_data.get("atr", entry_price * 0.02)
            qty = kelly_usd / entry_price
            
            logger.info("Kelly Sizing: %s | Final Size = %.6f BTC ($%.2f)", kelly_result["reasoning"], qty, kelly_usd)

            # Minimum check
            if qty < 0.0001:
                logger.info("Quantity too small for Binance (%.6f < 0.0001). Skipping.", qty)
                await asyncio.sleep(60)
                continue

            # ── m) Smart Tiered Execution ─────────────────────────
            side = orch_result["final_signal"].lower()
            logger.info("Placing SMART %s limit entries for %.6f BTC ...", side.upper(), qty)
            
            orders = await smart_entry(
                side, config.SYMBOL, qty, entry_price, atr, testnet=testnet_flag
            )

            if orders:
                # Calculate aggregate position size if partially filled instantly
                filled_qty = sum([o.get("amount", 0) for o in orders])
                if filled_qty > 0:
                    # Save trade to database
                    trade_record = {
                        "timestamp": int(time.time()),
                        "symbol": config.SYMBOL,
                        "side": side,
                        "price": entry_price, # We use signal price as avg entry for tracking
                        "quantity": filled_qty,
                        "reason": orch_result.get("summary", ""),
                        "pnl": 0.0,
                        "status": "open",
                        "stop_loss": entry_price - (atr * 2) if side == "buy" else entry_price + (atr * 2),
                        "take_profit": 0, # Not used in DB anymore, managed by TrailingStopManager
                    }
                    save_trade(db, trade_record)

                    # Save agent decision
                    decision_record = {
                        "timestamp": int(time.time()),
                        "signal": orch_result["final_signal"],
                        "confidence": orch_result.get("conviction_score", 0) / 100.0,
                        "reason": orch_result.get("summary", ""),
                        "approved": 1,
                    }
                    save_decision(db, decision_record)

                    # Send Telegram alert
                    alert_msg = (
                        f"*SMART TRADE EXECUTED (ELITE v3)*\n"
                        f"Side: `{side.upper()}`\n"
                        f"Symbol: `{config.SYMBOL}`\n"
                        f"Target Qty: `{qty:.6f} BTC`\n"
                        f"Layered Entry: `${entry_price:,.2f}` avg\n"
                        f"Conviction: `{orch_result.get('conviction_score', 0)}`\n"
                        f"Size Tier: `{orch_result.get('position_size_tier', '50')}%`\n"
                        f"Reason: `{orch_result.get('summary', 'Approved')}`\n"
                        f"Heat: `{portfolio.get('heat_pct', 0):.1f}%`"
                    )
                    await send_telegram_alert(alert_msg)

                    portfolio["session_trades"] += 1
            else:
                logger.error("Smart entry failed to place any limit orders.")

            # ── n) Cycle summary ───────────────────────────────────
            elapsed = time.time() - cycle_start
            logger.info(
                "Cycle done in %.1fs | regime=%s | MTF=%+d | "
                "signal=%s conf=%d | approved=%s tier=%s | next in 300s",
                elapsed,
                regime_result.get("regime"),
                mtf_result.get("confluence_score", 0),
                orch_result.get("final_signal"),
                orch_result.get("conviction_score", 0),
                orch_result.get("trade_approved"),
                orch_result.get("position_size_tier"),
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
