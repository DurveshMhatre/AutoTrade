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
from core.data_feed import fetch_ohlcv, fetch_multi_timeframe, fetch_order_book, fetch_balance, ExchangeSession
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
from agents.mean_reversion_agent import run_mean_reversion_agent

# ── Execution ───────────────────────────────────────────────────────
from execution.executor import place_order, smart_entry, check_order_status, fetch_open_orders

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

async def background_autopsy(trade_id: int, market_data: dict):
    """Run autopsy in background with its own DB connection (C5 fix)."""
    try:
        await asyncio.sleep(2)
        bg_db = init_db()
        try:
            trade = get_trade_by_id(bg_db, trade_id)
            if not trade:
                return
            logger.info(f"Running Autopsy on closed trade {trade_id}...")
            autopsy_result = run_autopsy_agent(trade, market_data)
            save_autopsy(bg_db, {
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
        finally:
            bg_db.close()
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
                    asyncio.create_task(background_autopsy(trade_id, market_data))

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

    # 3. Initialise portfolio state — sync balance from exchange (C1 fix)
    try:
        initial_balance = await fetch_balance(testnet=testnet_flag)
        if initial_balance <= 0:
            logger.warning("Balance sync returned 0, using fallback $1000")
            initial_balance = 1000.0
    except Exception as e:
        logger.warning("Initial balance fetch failed: %s — using $1000", e)
        initial_balance = 1000.0

    portfolio = {
        "usdt_balance": initial_balance,
        "open_positions": len(get_open_trades(db)),
        "daily_loss_pct": 0.0,
        "daily_pnl": 0.0,
        "session_trades": 0,
    }
    _last_reset_date = datetime.now(timezone.utc).date()
    _cycle_count = 0
    _reconcile_counter = 0

    # ── Agent result cache — avoid calling Claude on every cycle for slow-changing data ──
    _agent_cache = {
        "regime": {"result": None, "last_cycle": 0},
        "mtf": {"result": None, "last_cycle": 0},
        "sentiment": {"result": None, "last_cycle": 0},
        "sr": {"result": None, "last_cycle": 0},
        "news": {"result": None, "last_cycle": 0},
    }

    def _should_refresh(agent_name: str, current_cycle: int) -> bool:
        interval = config.AGENT_REFRESH_CYCLES.get(agent_name, 1)
        last = _agent_cache[agent_name]["last_cycle"]
        return (current_cycle - last) >= interval

    def _get_cached(agent_name: str):
        return _agent_cache[agent_name]["result"]

    def _set_cached(agent_name: str, result, cycle: int):
        _agent_cache[agent_name]["result"] = result
        _agent_cache[agent_name]["last_cycle"] = cycle

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
            _cycle_count += 1
            logger.info("--- Cycle #%d start: %s ---", _cycle_count, _ts())

            # ── Daily PnL reset at UTC midnight (C6 fix) ──────────
            now_utc = datetime.now(timezone.utc)
            if now_utc.date() != _last_reset_date:
                logger.info("Daily reset: PnL was $%.2f, resetting.", portfolio["daily_pnl"])
                portfolio["daily_pnl"] = 0.0
                portfolio["daily_loss_pct"] = 0.0
                _last_reset_date = now_utc.date()

            # ── Sync balance from exchange (C1 fix) — light check ─
            try:
                actual_balance = await fetch_balance(testnet=testnet_flag)
                if actual_balance > 0:
                    portfolio["usdt_balance"] = actual_balance
            except Exception as e:
                logger.warning("Balance sync failed, using last known: %s", e)

            # ── Sync position counter from DB (H2 fix) ────────────
            portfolio["open_positions"] = len(get_open_trades(db))

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

            # ── e) Run Regime Agent — Phase 1 (CACHED) ─────────────
            if _should_refresh("regime", _cycle_count) or _get_cached("regime") is None:
                regime_result = run_regime_agent(mtf_indicators)
                _set_cached("regime", regime_result, _cycle_count)
                logger.info(
                    "Regime refreshed: %s (conf=%.2f, bias=%s, multiplier=%.2f)",
                    regime_result.get("regime"),
                    regime_result.get("confidence", 0),
                    regime_result.get("strategy_bias"),
                    regime_result.get("position_size_multiplier", 0),
                )
            else:
                regime_result = _get_cached("regime")
                logger.info("Regime (cached): %s", regime_result.get("regime"))
            
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

            # ── f) Run MTF Confluence Agent — Phase 1 (CACHED) ────
            if _should_refresh("mtf", _cycle_count) or _get_cached("mtf") is None:
                mtf_result = run_mtf_agent(mtf_indicators)
                _set_cached("mtf", mtf_result, _cycle_count)
                logger.info(
                    "MTF refreshed: score=%+d  daily=%s  4H=%s  approved=%s",
                    mtf_result.get("confluence_score", 0),
                    mtf_result.get("daily_bias"),
                    mtf_result.get("4h_structure"),
                    mtf_result.get("trade_approved"),
                )
            else:
                mtf_result = _get_cached("mtf")
                logger.info("MTF (cached): score=%+d", mtf_result.get("confluence_score", 0))

            # ── g) Run Sentiment Agent — Phase 1 (CACHED) ─────────
            if _should_refresh("sentiment", _cycle_count) or _get_cached("sentiment") is None:
                sentiment_result = await run_sentiment_agent()
                _set_cached("sentiment", sentiment_result, _cycle_count)
                logger.info(
                    "Sentiment refreshed: F&G=%d(%s)  funding=%s  combined=%+d  bias=%s",
                    sentiment_result.get("fear_greed_score", 50),
                    sentiment_result.get("fear_greed_label"),
                    sentiment_result.get("funding_signal"),
                    sentiment_result.get("combined_sentiment", 0),
                    sentiment_result.get("trade_bias_adjustment"),
                )
            else:
                sentiment_result = _get_cached("sentiment")
                logger.info("Sentiment (cached): combined=%+d", sentiment_result.get("combined_sentiment", 0))

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

            # ── g.2) Run Phase 2 Agents (S&R and News CACHED) ─────
            if _should_refresh("sr", _cycle_count) or _get_cached("sr") is None:
                sr_result = run_sr_agent(mtf_candles.get("1h", candles), market_data.get("close", 0))
                _set_cached("sr", sr_result, _cycle_count)
                logger.info("S&R refreshed: Supp=$%.2f  Res=$%.2f  Quality=%s", sr_result.get("nearest_support", 0), sr_result.get("nearest_resistance", 0), sr_result.get("entry_quality"))
            else:
                sr_result = _get_cached("sr")
                logger.info("S&R (cached): Supp=$%.2f  Res=$%.2f", sr_result.get("nearest_support", 0), sr_result.get("nearest_resistance", 0))

            order_flow_result = run_order_flow_agent(order_book, vwap_data, market_data.get("close", 0))
            logger.info("Order Flow: Bias=%s  B/A Ratio=%.2f  VWAP=$%.2f", order_flow_result.get("order_flow_bias"), order_flow_result.get("bid_ask_ratio"), vwap_data.get("vwap", 0))

            if _should_refresh("news", _cycle_count) or _get_cached("news") is None:
                news_result = run_news_agent(headlines)
                _set_cached("news", news_result, _cycle_count)
                logger.info("News refreshed: Action=%s  Score=%.2f  MajorEvent=%s", news_result.get("trading_action"), news_result.get("weighted_sentiment_score", 0), news_result.get("major_event_detected"))
            else:
                news_result = _get_cached("news")
                logger.info("News (cached): Action=%s", news_result.get("trading_action"))

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

            # ── i) Run Signal Agent — route to correct agent ────────
            current_regime = regime_result.get("regime", "")
            strategy_type = active_strategy.get("type", "TREND_FOLLOWING")

            if strategy_type == "MEAN_REVERSION":
                signal = run_mean_reversion_agent(market_data, sr_result, regime=current_regime)
                logger.info(
                    "Mean Reversion signal: %s conf=%.2f dist_sr=%.2f%% R:R=%.2f",
                    signal.get("signal"), signal.get("confidence", 0),
                    signal.get("distance_to_sr_pct", 999), signal.get("rr_ratio", 0),
                )
            else:
                signal = run_trend_agent(market_data, regime=current_regime)
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
                        # Escape underscores in reason to prevent Telegram Markdown parse errors
                        safe_reason = reason.replace("_", " ") if reason else "unknown"
                        await send_telegram_alert(
                            f"\U0001f916 *BOT ALIVE (ELITE v3)* \u2014 Monitoring market\n"
                            f"Status: HOLD (blocked: {safe_reason})\n"
                            f"BTC: ${market_data.get('close', 0):,.2f} | Heat: {portfolio.get('heat_pct', 0):.1f}%\n"
                            f"Regime: {regime_result.get('regime', 'N/A')} | "
                            f"MTF: {mtf_result.get('confluence_score', 0):+d}"
                        )
                    except Exception:
                        pass

                await asyncio.sleep(60)
                continue

            # ── k2) Handle FLATTEN signal — close existing longs ──
            if orch_result.get("final_signal") == "FLATTEN":
                open_trades = get_open_trades(db)
                if open_trades:
                    logger.info("FLATTEN: Closing %d open long(s).", len(open_trades))
                    for ot in open_trades:
                        try:
                            close_qty = ot.get("quantity", 0)
                            if close_qty >= 0.0001:
                                result = await place_order("sell", config.SYMBOL, close_qty, testnet=testnet_flag)
                                if result.get("status") != "failed":
                                    close_price = market_data.get("close", 0)
                                    pnl = (close_price - ot.get("price", 0)) * close_qty
                                    update_trade_status(db, ot["id"], "closed", close_price, pnl)
                                    portfolio["daily_pnl"] += pnl
                                    trade_id = ot["id"]
                                    _active_trade_managers.pop(trade_id, None)
                                    asyncio.create_task(background_autopsy(trade_id, market_data))
                                    await send_telegram_alert(
                                        f"📤 *FLATTEN* — Closed long #{trade_id}\n"
                                        f"PnL: `${pnl:,.2f}` | Reason: SELL signal"
                                    )
                        except Exception as e:
                            logger.error("FLATTEN close failed for trade %s: %s", ot.get("id"), e)
                else:
                    logger.info("FLATTEN signal but no open longs to close.")
                await asyncio.sleep(60)
                continue

            # ── k3) Re-enable risk manager hard blocks (H5 fix) ───
            risk_check = evaluate_trade(
                {"signal": orch_result["final_signal"],
                 "confidence": orch_result.get("conviction_score", 0) / 100.0},
                portfolio, market_data,
                {"MIN_CONFIDENCE": config.MIN_CONFIDENCE,
                 "MAX_POSITIONS": config.MAX_POSITIONS,
                 "DAILY_LOSS_LIMIT_PCT": config.DAILY_LOSS_LIMIT_PCT,
                 "RISK_PER_TRADE_PCT": config.RISK_PER_TRADE_PCT,
                 "STOP_LOSS_PCT": config.STOP_LOSS_PCT,
                 "TAKE_PROFIT_PCT": config.TAKE_PROFIT_PCT}
            )
            if not risk_check.get("approved"):
                logger.info("Risk manager blocked: %s", risk_check.get("reason"))
                await asyncio.sleep(60)
                continue
            # Compute stats from trade history (last 20 closed trades)
            from core.database import get_recent_trades
            closed_trades = [t for t in get_recent_trades(db, limit=50) if t.get("status") == "closed"]
            if len(closed_trades) >= 20:
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
            
            # ── Hybrid Sizing: Risk-Based Floor + Kelly Scaler (Bug 2 fix) ──
            # Standard risk-based position: RISK_PER_TRADE_PCT of balance
            base_risk_usd = portfolio["usdt_balance"] * config.RISK_PER_TRADE_PCT  # 2% of balance
            
            # If Kelly shows an edge, use Kelly. Otherwise, use the base risk amount.
            if kelly_result["position_usd"] > 0:
                kelly_usd = kelly_result["position_usd"]
            else:
                kelly_usd = base_risk_usd
                logger.info("Kelly shows no edge (%.1f%%) — using base risk sizing: $%.2f",
                           kelly_result["kelly_raw"] * 100, base_risk_usd)

            # Calculate entry levels
            entry_price = market_data.get("close", 0)
            atr = market_data.get("atr", entry_price * 0.02)

            # Method 1: Risk-based sizing (preferred)
            # Risk $20 per trade with 1.5% stop = $20 / (price * 0.015) BTC
            risk_usd = max(portfolio["usdt_balance"] * config.RISK_PER_TRADE_PCT, config.TARGET_RISK_PER_TRADE_USD)
            sl_distance_pct = config.STOP_LOSS_PCT  # 0.015
            sl_distance_usd = entry_price * sl_distance_pct
            risk_based_qty = risk_usd / sl_distance_usd if sl_distance_usd > 0 else 0

            # Method 2: Kelly-adjusted sizing
            kelly_qty = kelly_usd / entry_price if entry_price > 0 else 0

            # Take the LARGER of risk-based and kelly, to ensure fees are beatable
            qty = max(risk_based_qty, kelly_qty)

            # Apply Tier from Master Orchestrator
            _TIER_MAP = {"full": 1.0, "100": 1.0, "75": 0.75, "50": 0.50}
            tier_raw = str(orch_result.get("position_size_tier", "50")).lower()
            tier_multiplier = _TIER_MAP.get(tier_raw, 0.50)
            qty = qty * tier_multiplier

            # Hard caps — never more than 3% of balance
            qty = min(qty, portfolio["usdt_balance"] * 0.03 / entry_price)

            # ABSOLUTE MINIMUM: 0.001 BTC — below this, fees destroy every trade
            ABSOLUTE_MIN_BTC = max(config.MIN_POSITION_BTC, config.MIN_POSITION_USD / entry_price)
            if qty < ABSOLUTE_MIN_BTC:
                logger.info(
                    "Position size $%.2f (%.6f BTC) below viable minimum $%.2f (%.6f BTC). "
                    "Increasing to minimum to cover fees.",
                    qty * entry_price, qty, ABSOLUTE_MIN_BTC * entry_price, ABSOLUTE_MIN_BTC
                )
                # Only trade if balance supports the minimum
                if portfolio["usdt_balance"] >= ABSOLUTE_MIN_BTC * entry_price * 1.1:
                    qty = ABSOLUTE_MIN_BTC
                else:
                    logger.info("Balance too low for minimum position. Skipping.")
                    await asyncio.sleep(60)
                    continue

            logger.info(
                "Final sizing: risk_based=%.6f kelly=%.6f final=%.6f BTC ($%.2f) tier=%s",
                risk_based_qty, kelly_qty, qty, qty * entry_price,
                orch_result.get("position_size_tier", "50")
            )

            # ── m) Smart Tiered Execution ─────────────────────────
            side = orch_result["final_signal"].lower()
            logger.info("Placing SMART %s limit entries for %.6f BTC ...", side.upper(), qty)
            
            orders = await smart_entry(
                side, config.SYMBOL, qty, entry_price, atr, testnet=testnet_flag
            )

            if orders:
                # C2/C3 fix: Wait briefly, then check actual fill status
                await asyncio.sleep(5)
                filled_qty = 0.0
                avg_fill_price = entry_price
                for o in orders:
                    try:
                        order_status = await check_order_status(
                            str(o.get("id")), config.SYMBOL, testnet=testnet_flag
                        )
                        if order_status.get("filled", 0) > 0:
                            filled_qty += order_status["filled"]
                            if order_status.get("average"):
                                avg_fill_price = order_status["average"]
                    except Exception as e:
                        logger.warning("Order status check failed: %s", e)

                if filled_qty >= 0.0001:
                    # Save trade to database with actual fill data
                    trade_record = {
                        "timestamp": int(time.time()),
                        "symbol": config.SYMBOL,
                        "side": side,
                        "price": avg_fill_price,
                        "quantity": filled_qty,
                        "reason": orch_result.get("summary", ""),
                        "pnl": 0.0,
                        "status": "open",
                        "stop_loss": avg_fill_price - (atr * 2) if side == "buy" else avg_fill_price + (atr * 2),
                        "take_profit": 0,
                    }
                    save_trade(db, trade_record)

                    # ── Create TrailingStopManager with SR overrides for RANGING ──
                    trade_id_new = None
                    try:
                        open_trades_now = get_open_trades(db)
                        if open_trades_now:
                            trade_id_new = open_trades_now[-1]["id"]
                    except Exception:
                        pass

                    if trade_id_new:
                        if strategy_type == "MEAN_REVERSION" and signal.get("suggested_sl"):
                            _active_trade_managers[trade_id_new] = TrailingStopManager(
                                entry_price=avg_fill_price,
                                side=side,
                                atr=atr,
                                regime=current_regime,
                                tp_override=signal.get("suggested_tp"),
                                sl_override=signal.get("suggested_sl")
                            )
                        else:
                            _active_trade_managers[trade_id_new] = TrailingStopManager(
                                entry_price=avg_fill_price, side=side, atr=atr, regime=current_regime
                            )

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
                        f"Filled Qty: `{filled_qty:.6f} BTC`\n"
                        f"Avg Fill: `${avg_fill_price:,.2f}`\n"
                        f"Conviction: `{orch_result.get('conviction_score', 0)}`\n"
                        f"Size Tier: `{orch_result.get('position_size_tier', '50')}%`\n"
                        f"Reason: `{orch_result.get('summary', 'Approved')}`\n"
                        f"Heat: `{portfolio.get('heat_pct', 0):.1f}%`"
                    )
                    await send_telegram_alert(alert_msg)

                    portfolio["session_trades"] += 1
                else:
                    logger.info("No fills yet — limit orders placed, will be tracked.")
            else:
                logger.error("Smart entry failed to place any limit orders.")

            # ── n) Cycle summary ───────────────────────────────────
            elapsed = time.time() - cycle_start
            logger.info(
                "Cycle #%d done in %.1fs | regime=%s | MTF=%+d | "
                "signal=%s conf=%d | approved=%s tier=%s | next in 300s",
                _cycle_count, elapsed,
                regime_result.get("regime"),
                mtf_result.get("confluence_score", 0),
                orch_result.get("final_signal"),
                orch_result.get("conviction_score", 0),
                orch_result.get("trade_approved"),
                orch_result.get("position_size_tier"),
            )

            # ── n2) Hourly heartbeat (M5 fix) ─────────────────────
            if _cycle_count % 12 == 0:  # Every 12 cycles ≈ 1 hour
                try:
                    await send_telegram_alert(
                        f"🤖 *HEARTBEAT* — Cycle #{_cycle_count}\n"
                        f"BTC: `${market_data.get('close', 0):,.2f}`\n"
                        f"Open: `{portfolio['open_positions']}`\n"
                        f"Daily PnL: `${portfolio['daily_pnl']:,.2f}`\n"
                        f"Balance: `${portfolio['usdt_balance']:,.2f}`"
                    )
                except Exception:
                    pass

            # ── n3) Heavy reconciliation every 20 cycles (C3 fix) ─
            _reconcile_counter += 1
            if _reconcile_counter >= 20:
                _reconcile_counter = 0
                try:
                    exchange_orders = await fetch_open_orders(config.SYMBOL, testnet=testnet_flag)
                    db_open = get_open_trades(db)
                    if len(db_open) > 0 and len(exchange_orders) == 0:
                        logger.warning("RECONCILIATION: DB has %d open trades but exchange has 0 open orders. Possible stale trades.", len(db_open))
                except Exception as e:
                    logger.warning("Reconciliation check failed: %s", e)

            # ── o) Sleep 300s (5-minute candle rhythm) ─────────────
            last_error_str = None
            await asyncio.sleep(300)

        # 6. Clean shutdown on Ctrl+C (M2 fix)
        except KeyboardInterrupt:
            print("\nBot shutting down — cancelling open orders...")
            try:
                from execution.executor import cancel_all_open_orders
                await cancel_all_open_orders(config.SYMBOL, testnet=testnet_flag)
            except Exception:
                pass
            print("Bot stopped cleanly")
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
