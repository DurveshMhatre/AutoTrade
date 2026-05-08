"""
monitoring/telegram_alerts.py
==============================
Telegram notification system for the Automated Trading Bot.

Core function:
    send_alert(message, alert_type)  — sends a prefixed message via Telegram
                                        or prints to stdout if no token set.

Pre-built alert templates:
    trade_alert(...)         — formatted trade execution notification
    error_alert(...)         — formatted error notification
    daily_report_alert(...)  — multi-line daily performance summary

Backward-compatible alias:
    send_telegram_alert(message) — calls send_alert(message, "info")
"""

import logging
import os
import time

import aiohttp
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert type → prefix mapping
# ---------------------------------------------------------------------------
ALERT_PREFIXES = {
    "trade": "*TRADE*",
    "error": "*ERROR*",
    "info": "*INFO*",
    "daily": "*DAILY REPORT*",
}

# ---------------------------------------------------------------------------
# Rate limiter — prevent Telegram API throttling in error loops
# ---------------------------------------------------------------------------
_last_alert_ts: float = 0.0
_MIN_ALERT_INTERVAL: float = 10.0  # Max 1 alert per 10 seconds


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------
async def send_alert(message: str, alert_type: str = "info") -> bool:
    """Send a prefixed alert message via Telegram or print to stdout.

    Parameters
    ----------
    message : str
        The alert body text.
    alert_type : str
        One of ``"trade"``, ``"error"``, ``"info"``, ``"daily"``.
        Determines the prefix prepended to the message.

    Returns
    -------
    bool
        ``True`` if the message was delivered (or printed), ``False`` on failure.
    """
    global _last_alert_ts

    # Rate limiting — skip if too soon after last alert
    now = time.time()
    if now - _last_alert_ts < _MIN_ALERT_INTERVAL:
        logger.debug("Telegram rate limited — skipping alert (type=%s)", alert_type)
        return True
    _last_alert_ts = now
    prefix = ALERT_PREFIXES.get(alert_type, ALERT_PREFIXES["info"])
    full_message = f"{prefix} | {message}"

    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    # ── Graceful degradation: no token → print to stdout ──────────────
    if not token or not chat_id:
        print(f"[TELEGRAM-STDOUT] {full_message}")
        logger.info("Telegram credentials not set — printed to stdout")
        return True

    # ── Send via Telegram Bot API ─────────────────────────────────────
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": full_message,
        "parse_mode": "Markdown",
    }

    try:
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    logger.info("Telegram alert sent successfully (%s)", alert_type)
                    return True
                else:
                    body = await resp.text()
                    logger.error(
                        "Telegram API error %d: %s", resp.status, body
                    )
                    return False
    except Exception as exc:  # noqa: BLE001
        logger.error("Telegram alert failed: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Backward-compatible alias (used by main.py)
# ---------------------------------------------------------------------------
async def send_telegram_alert(message: str) -> bool:
    """Backward-compatible wrapper — calls ``send_alert(message, "info")``."""
    return await send_alert(message, "info")


# ---------------------------------------------------------------------------
# Pre-built alert templates
# ---------------------------------------------------------------------------
async def trade_alert(
    signal: str,
    symbol: str,
    quantity: float,
    price: float,
    stop_loss: float,
    take_profit: float,
    reason: str,
) -> bool:
    """Send a formatted trade execution alert.

    Format::

        TRADE | BUY 0.205128 BTC/USDT @ $65000.00
               | SL: $64025.00 | TP: $66950.00
               | Strong bullish trend
    """
    message = (
        f"{signal} {quantity:.6f} {symbol} @ ${price:,.2f}\n"
        f"SL: ${stop_loss:,.2f} | TP: ${take_profit:,.2f}\n"
        f"{reason}"
    )
    return await send_alert(message, "trade")


async def error_alert(error_message: str) -> bool:
    """Send a formatted error alert.

    Format::

        ERROR | Bot encountered: Connection timeout after 10s
    """
    message = f"Bot encountered: {error_message}"
    return await send_alert(message, "error")


async def daily_report_alert(
    trades_today: int,
    pnl_today: float,
    win_rate: float,
    balance: float,
) -> bool:
    """Send a multi-line daily performance summary.

    Format::

        DAILY REPORT | Daily Summary
        Trades: 5
        P&L: $+127.50
        Win Rate: 60.0%
        Balance: $10,127.50
    """
    pnl_sign = "+" if pnl_today >= 0 else ""
    message = (
        f"Daily Summary\n"
        f"Trades: {trades_today}\n"
        f"P&L: ${pnl_sign}{pnl_today:,.2f}\n"
        f"Win Rate: {win_rate * 100:.1f}%\n"
        f"Balance: ${balance:,.2f}"
    )
    return await send_alert(message, "daily")
