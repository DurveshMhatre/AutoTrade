"""
execution/executor.py
---------------------
Order execution layer using ccxt async Binance.

Functions
---------
place_order            -- place a MARKET spot order
smart_entry            -- layered limit order entry (3 tranches)
check_order_status     -- check fill status of an order
cancel_unfilled_after  -- auto-cancel unfilled limits after timeout
cancel_all_open_orders -- cancel every open order for a symbol
fetch_open_orders      -- fetch all open orders for reconciliation
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


async def cancel_unfilled_after(orders: list[dict], seconds: int, symbol: str, testnet: bool) -> None:
    """Cancel any remaining unfilled limit orders after a timeout."""
    await asyncio.sleep(seconds)
    
    exchange = _build_exchange(testnet=testnet)
    try:
        open_orders = await exchange.fetch_open_orders(symbol)
        open_order_ids = [str(o["id"]) for o in open_orders]
        
        for order in orders:
            order_id = str(order.get("id"))
            if order_id in open_order_ids:
                logger.info(f"Cancelling unfilled limit order {order_id} after {seconds}s timeout.")
                await exchange.cancel_order(order_id, symbol)
    except Exception as exc:
        logger.error(f"Failed to cancel unfilled orders: {exc}")
    finally:
        await _close_exchange(exchange)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def place_order(
    side: str,
    symbol: str,
    quantity: float,
    testnet: bool = True,
    exchange: ccxt.binance | None = None,
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
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    dict
        Full ccxt order dict on success, or
        ``{"status": "failed", "error": "..."}`` on failure.
    """
    own_exchange = exchange is None
    if own_exchange:
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
        if own_exchange:
            await _close_exchange(exchange)


async def smart_entry(
    side: str,
    symbol: str,
    quantity: float,
    entry_price: float,
    atr: float,
    testnet: bool = True,
    exchange: ccxt.binance | None = None,
) -> list[dict]:
    """
    Layered limit order entry used by professional desks.
    Instead of one market order, place 3 limit orders at slightly different levels.
    This reduces slippage and averages into a better position.

    Parameters
    ----------
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.
    """
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange(testnet=testnet)
    orders = []
    try:
        # Split into 3 tranches
        if side.lower() == "buy":
            t1_price = entry_price                        # At signal price
            t2_price = entry_price - (atr * 0.1)          # 10% of ATR below (better fill)
            t3_price = entry_price - (atr * 0.2)          # 20% of ATR below (best fill)
        else:
            t1_price = entry_price                        # At signal price
            t2_price = entry_price + (atr * 0.1)          # 10% of ATR above (better fill)
            t3_price = entry_price + (atr * 0.2)          # 20% of ATR above (best fill)

        t1_qty = quantity * 0.50  # 50% at signal
        t2_qty = quantity * 0.30  # 30% slightly better
        t3_qty = quantity * 0.20  # 20% even better

        for price, qty in [(t1_price, t1_qty), (t2_price, t2_qty), (t3_price, t3_qty)]:
            # Skip micro orders below binance limit
            if qty < 0.0001:
                continue
                
            logger.info(f"Placing LIMIT {side.upper()} order: {qty:.6f} {symbol} @ {price:.2f}")
            order = await exchange.create_limit_order(
                symbol=symbol,
                side=side.lower(),
                amount=round(qty, 6),
                price=round(price, 2),
                params={"timeInForce": "GTC"}
            )
            orders.append(order)
            
        # Cancel unfilled limit orders after 15 minutes (900 seconds)
        if orders:
            asyncio.create_task(cancel_unfilled_after(orders, 900, symbol, testnet))
            
        return orders
        
    except Exception as exc:
        logger.error(f"Smart entry failed: {exc}", exc_info=True)
        return orders
    finally:
        if own_exchange:
            await _close_exchange(exchange)


async def check_order_status(
    order_id: str,
    symbol: str,
    testnet: bool = True,
    exchange: ccxt.binance | None = None,
) -> dict:
    """Check the fill status of a specific order.

    Parameters
    ----------
    order_id : str
        The exchange order ID to check.
    symbol : str
        Trading pair.
    testnet : bool
        If True, use testnet.
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    dict
        ``{"id", "status", "filled", "remaining", "average"}``
        status is one of: "open", "closed", "canceled"
    """
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange(testnet=testnet)
    try:
        order = await exchange.fetch_order(order_id, symbol)
        return {
            "id": order.get("id"),
            "status": order.get("status"),  # open, closed, canceled
            "filled": float(order.get("filled", 0)),
            "remaining": float(order.get("remaining", 0)),
            "average": float(order.get("average", 0)) if order.get("average") else None,
        }
    except Exception as exc:
        logger.error("check_order_status failed for %s: %s", order_id, exc)
        return {"id": order_id, "status": "unknown", "filled": 0, "remaining": 0, "average": None}
    finally:
        if own_exchange:
            await _close_exchange(exchange)


async def fetch_open_orders(
    symbol: str,
    testnet: bool = True,
    exchange: ccxt.binance | None = None,
) -> list[dict]:
    """Fetch all open orders for a symbol from the exchange.

    Used by the reconciliation loop to compare exchange state vs DB state.

    Parameters
    ----------
    symbol : str
        Trading pair.
    testnet : bool
        If True, use testnet.
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    list[dict]
        List of open order dicts from ccxt.
    """
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange(testnet=testnet)
    try:
        open_orders = await exchange.fetch_open_orders(symbol)
        logger.info("fetch_open_orders: %d open orders for %s", len(open_orders), symbol)
        return open_orders
    except Exception as exc:
        logger.error("fetch_open_orders failed: %s", exc)
        return []
    finally:
        if own_exchange:
            await _close_exchange(exchange)


async def cancel_all_open_orders(
    symbol: str,
    testnet: bool = True,
    exchange: ccxt.binance | None = None,
) -> bool:
    """Cancel every open order for *symbol*.

    Parameters
    ----------
    symbol : str
        Trading pair, e.g. ``"BTC/USDT"``.
    testnet : bool
        If ``True`` (default), use Binance testnet sandbox.
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    bool
        ``True`` if all open orders were cancelled (or none existed),
        ``False`` on any failure.
    """
    own_exchange = exchange is None
    if own_exchange:
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
        if own_exchange:
            await _close_exchange(exchange)
