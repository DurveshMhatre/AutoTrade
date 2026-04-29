"""
core/data_feed.py
─────────────────
Async data-feed layer for the Automated Trading Bot.

• fetch_ohlcv   — historical OHLCV candles via REST
• get_current_price — latest ticker price via REST
• stream_prices — real-time trade stream via WebSocket
"""

import asyncio
import json
import os
from datetime import datetime, timezone

import aiohttp
import ccxt.async_support as ccxt
from ccxt.base.errors import ExchangeNotAvailable
import websockets
from dotenv import load_dotenv

load_dotenv()

# ─── Exchange factory ───────────────────────────────────────────────

def _build_exchange(*, authenticated: bool = False) -> ccxt.binance:
    """
    Return a ccxt async Binance instance.

    Parameters
    ----------
    authenticated : bool
        If True, attach API keys for private endpoints (order placement, etc.).
        If False (default), create a lightweight public-only instance.
    """
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

    # Use ThreadedResolver to avoid aiodns/c-ares DNS issues on Windows
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector)

    config: dict = {
        "enableRateLimit": True,
        "options": {"defaultType": "spot"},
        "session": session,
    }

    if authenticated:
        config["apiKey"] = os.getenv("BINANCE_API_KEY", "")
        config["secret"] = os.getenv("BINANCE_SECRET", "")

    exchange = ccxt.binance(config)

    if testnet:
        exchange.set_sandbox_mode(True)

    return exchange


async def _close_exchange(exchange: ccxt.binance) -> None:
    """Cleanly shut down the exchange and its underlying aiohttp session."""
    if hasattr(exchange, "session") and exchange.session:
        await exchange.session.close()
    await exchange.close()
    # Allow the event loop to process the socket closures
    await asyncio.sleep(0.25)


def _log(msg: str) -> None:
    """Print a timestamped log line to stdout."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}")


# ─── 1. Historical OHLCV ────────────────────────────────────────────

async def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "5m",
    limit: int = 200,
    max_retries: int = 3,
) -> list[dict]:
    """
    Fetch historical OHLCV candles from Binance.

    Returns a list of dicts:
        [{timestamp, open, high, low, close, volume}, ...]

    Retries up to *max_retries* times on NetworkError / ExchangeError
    with exponential back-off (2 ** attempt seconds).
    """
    exchange = _build_exchange()
    last_exc: Exception | None = None

    try:
        for attempt in range(1, max_retries + 1):
            try:
                raw = await exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                candles = [
                    {
                        "timestamp": row[0],
                        "open": row[1],
                        "high": row[2],
                        "low": row[3],
                        "close": row[4],
                        "volume": row[5],
                    }
                    for row in raw
                ]
                _log(f"fetch_ohlcv: got {len(candles)} candles for {symbol} ({timeframe})")
                return candles

            except (ccxt.NetworkError, ccxt.ExchangeError, ExchangeNotAvailable) as exc:
                last_exc = exc
                _log(
                    f"fetch_ohlcv: {type(exc).__name__} (attempt {attempt}/{max_retries}): {exc}"
                )

            if attempt < max_retries:
                backoff = 2 ** attempt
                _log(f"fetch_ohlcv: retrying in {backoff}s …")
                await asyncio.sleep(backoff)

        _log("fetch_ohlcv: all retries exhausted")
        raise last_exc  # type: ignore[misc]

    finally:
        await _close_exchange(exchange)


# ─── 2. Current ticker price ────────────────────────────────────────

async def get_current_price(symbol: str = "BTC/USDT") -> float:
    """Return the latest ticker price for *symbol* as a float."""
    exchange = _build_exchange()
    try:
        ticker = await exchange.fetch_ticker(symbol)
        price = float(ticker["last"])
        _log(f"get_current_price: {symbol} → {price}")
        return price
    finally:
        await _close_exchange(exchange)


# ─── 3. WebSocket trade stream ──────────────────────────────────────

async def stream_prices(
    symbol: str = "BTC/USDT",
    callback=None,
    reconnect_delay: float = 3.0,
) -> None:
    """
    Stream real-time trade prices from the Binance WebSocket API.

    Calls  callback(price: float)  on every incoming trade.
    Automatically reconnects on disconnection.
    """
    # Binance WS expects a lowercase market pair like "btcusdt"
    ws_symbol = symbol.replace("/", "").lower()
    testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

    if testnet:
        ws_url = f"wss://testnet.binance.vision/ws/{ws_symbol}@trade"
    else:
        ws_url = f"wss://stream.binance.com:9443/ws/{ws_symbol}@trade"

    while True:
        try:
            _log(f"stream_prices: connecting to {ws_url}")
            async with websockets.connect(ws_url) as ws:
                _log(f"stream_prices: connected — streaming {symbol}")
                async for message in ws:
                    data = json.loads(message)
                    price = float(data["p"])
                    if callback:
                        await callback(price) if asyncio.iscoroutinefunction(callback) else callback(price)

        except (websockets.ConnectionClosed, ConnectionError, OSError) as exc:
            _log(f"stream_prices: connection lost ({exc}), reconnecting in {reconnect_delay}s …")
            await asyncio.sleep(reconnect_delay)
        except Exception as exc:
            _log(f"stream_prices: unexpected error ({exc}), reconnecting in {reconnect_delay}s …")
            await asyncio.sleep(reconnect_delay)
