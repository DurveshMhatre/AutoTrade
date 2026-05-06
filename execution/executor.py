"""
execution/executor.py
---------------------
Order execution layer using ccxt async Binance.

Functions
---------
place_order         -- place a MARKET spot order
cancel_all_open_orders -- cancel every open order for a symbol
"""

import asyncio
import logging
import os

import aiohttp
import ccxt.async_support as ccxt
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_exchange(*, testnet: bool = True) -> ccxt.binance:
    """Create an authenticated ccxt async Binance instance."""
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector)

    exchange = ccxt.binance(
        {
            "apiKey": os.getenv("BINANCE_API_KEY", ""),
            "secret": os.getenv("BINANCE_SECRET", ""),
            "enableRateLimit": True,
            "options": {
                "defaultType": "spot",
                "fetchMarkets": ["spot"],
                "warnOnFetchOpenOrdersWithoutSymbol": False,
            },
            "session": session,
        }
    )

    if testnet:
        exchange.set_sandbox_mode(True)
        # Prevent CCXT from hitting the unstable futures testnet
        if "api" in exchange.urls and isinstance(exchange.urls["api"], dict):
            exchange.urls["api"]["fapiPublic"] = "https://testnet.binance.vision/api/v3"
            exchange.urls["api"]["fapiPrivate"] = "https://testnet.binance.vision/api/v3"

    return exchange


async def _close_exchange(exchange: ccxt.binance) -> None:
    """Cleanly shut down the exchange and its underlying aiohttp session."""
    try:
        if hasattr(exchange, "session") and exchange.session:
            await exchange.session.close()
        await exchange.close()
        await asyncio.sleep(0.25)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def place_order(
    side: str,
    symbol: str,
    quantity: float,
    testnet: bool = True,
) -> dict:
    """Place a MARKET spot order on Binance.

    Parameters
    ----------
    side : str
        ``"buy"`` or ``"sell"``.
    symbol : str
        Trading pair, e.g. ``"BTC/USDT"``.
    quantity : float
        Amount to trade (in base currency, e.g. BTC).
    testnet : bool
        If ``True`` (default), use Binance testnet sandbox.

    Returns
    -------
    dict
        Full ccxt order dict on success, or
        ``{"status": "failed", "error": "..."}`` on failure.
    """
    exchange = _build_exchange(testnet=testnet)
    try:
        logger.info(
            "Placing MARKET %s order: %s %.6f (testnet=%s)",
            side.upper(), symbol, quantity, testnet,
        )
        order = await exchange.create_market_order(symbol, side, quantity)
        logger.info("Order placed successfully: id=%s", order.get("id"))
        return order

    except Exception as exc:  # noqa: BLE001
        logger.error("Order failed: %s", exc, exc_info=True)
        return {"status": "failed", "error": str(exc)}

    finally:
        await _close_exchange(exchange)


async def cancel_all_open_orders(symbol: str, testnet: bool = True) -> bool:
    """Cancel every open order for *symbol*.

    Parameters
    ----------
    symbol : str
        Trading pair, e.g. ``"BTC/USDT"``.
    testnet : bool
        If ``True`` (default), use Binance testnet sandbox.

    Returns
    -------
    bool
        ``True`` if all open orders were cancelled (or none existed),
        ``False`` on any failure.
    """
    exchange = _build_exchange(testnet=testnet)
    try:
        open_orders = await exchange.fetch_open_orders(symbol)
        if not open_orders:
            logger.info("No open orders to cancel for %s", symbol)
            return True

        for order in open_orders:
            await exchange.cancel_order(order["id"], symbol)
            logger.info("Cancelled order %s for %s", order["id"], symbol)

        logger.info("All %d open orders cancelled for %s", len(open_orders), symbol)
        return True

    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to cancel orders for %s: %s", symbol, exc, exc_info=True)
        return False

    finally:
        await _close_exchange(exchange)
