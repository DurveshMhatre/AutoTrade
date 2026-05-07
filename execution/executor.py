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

async def smart_entry(
    side: str,
    symbol: str,
    quantity: float,
    entry_price: float,
    atr: float,
    testnet: bool = True,
) -> list[dict]:
    """
    Layered limit order entry used by professional desks.
    Instead of one market order, place 3 limit orders at slightly different levels.
    This reduces slippage and averages into a better position.
    """
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
