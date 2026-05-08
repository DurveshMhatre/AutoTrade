"""
core/data_feed.py
─────────────────
Async data-feed layer for the Automated Trading Bot.

• ExchangeSession   — reusable async context manager for exchange sessions
• fetch_ohlcv       — historical OHLCV candles via REST
• fetch_multi_timeframe — multi-TF candles in one session
• fetch_order_book  — live depth
• fetch_balance     — account USDT balance
• get_current_price — latest ticker price via REST
• stream_prices     — real-time trade stream via WebSocket
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

def _build_exchange(*, authenticated: bool = False, testnet: bool | None = None) -> ccxt.binance:
    """
    Return a ccxt async Binance instance.

    Parameters
    ----------
    authenticated : bool
        If True, attach API keys for private endpoints (order placement, etc.).
        If False (default), create a lightweight public-only instance.
    testnet : bool | None
        Override testnet flag. If None, reads from BINANCE_TESTNET env var.
    """
    if testnet is None:
        testnet = os.getenv("BINANCE_TESTNET", "false").lower() == "true"

    # Use ThreadedResolver to avoid aiodns/c-ares DNS issues on Windows
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    session = aiohttp.ClientSession(connector=connector)

    config: dict = {
        "enableRateLimit": True,
        "options": {
            "defaultType": "spot",
            "fetchMarkets": ["spot"],
            "warnOnFetchOpenOrdersWithoutSymbol": False,
        },
        "session": session,
    }

    if authenticated:
        config["apiKey"] = os.getenv("BINANCE_API_KEY", "")
        config["secret"] = os.getenv("BINANCE_SECRET", "")

    exchange = ccxt.binance(config)

    if testnet:
        exchange.set_sandbox_mode(True)
        # Prevent CCXT from hitting the unstable futures testnet
        if "api" in exchange.urls and isinstance(exchange.urls["api"], dict):
            exchange.urls["api"]["fapiPublic"] = "https://testnet.binance.vision/api/v3"
            exchange.urls["api"]["fapiPrivate"] = "https://testnet.binance.vision/api/v3"

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


# ─── Reusable Exchange Session ──────────────────────────────────────

class ExchangeSession:
    """Async context manager that creates a single exchange session
    and cleans it up on exit. Pass the exchange object to all data
    functions within the same cycle to avoid repeated TCP/SSL handshakes.

    Usage::

        async with ExchangeSession(authenticated=True) as exchange:
            candles = await fetch_ohlcv("BTC/USDT", exchange=exchange)
            balance = await fetch_balance(exchange=exchange)
    """

    def __init__(self, *, authenticated: bool = False, testnet: bool | None = None):
        self._authenticated = authenticated
        self._testnet = testnet
        self._exchange: ccxt.binance | None = None

    async def __aenter__(self) -> ccxt.binance:
        self._exchange = _build_exchange(
            authenticated=self._authenticated,
            testnet=self._testnet,
        )
        return self._exchange

    async def __aexit__(self, *args) -> None:
        if self._exchange:
            await _close_exchange(self._exchange)
            self._exchange = None


# ─── 1. Historical OHLCV ────────────────────────────────────────────

async def fetch_ohlcv(
    symbol: str = "BTC/USDT",
    timeframe: str = "5m",
    limit: int = 200,
    max_retries: int = 3,
    exchange: ccxt.binance | None = None,
) -> list[dict]:
    """
    Fetch historical OHLCV candles from Binance.

    Returns a list of dicts:
        [{timestamp, open, high, low, close, volume}, ...]

    Parameters
    ----------
    exchange : ccxt.binance | None
        If provided, reuse this exchange session (no cleanup).
        If None, create and clean up a temporary session.
    """
    own_exchange = exchange is None
    if own_exchange:
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
        if own_exchange:
            await _close_exchange(exchange)


# ─── 1b. Multi-Timeframe OHLCV ─────────────────────────────────────

async def fetch_multi_timeframe(
    symbol: str = "BTC/USDT",
    timeframes: dict | None = None,
    max_retries: int = 3,
    exchange: ccxt.binance | None = None,
) -> dict[str, list[dict]]:
    """Fetch OHLCV candles for multiple timeframes using a single exchange session.

    Parameters
    ----------
    symbol : str
        Trading pair.
    timeframes : dict | None
        Mapping of ``{timeframe: candle_limit}``.
        Defaults to ``{"1h": 200, "4h": 100, "1d": 60}``.
    max_retries : int
        Retry count per timeframe.
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    dict[str, list[dict]]
        ``{"1h": [...candles...], "4h": [...], "1d": [...]}``
        Timeframes that fail after all retries are returned as empty lists.
    """
    if timeframes is None:
        timeframes = {"1h": 200, "4h": 100, "1d": 60}

    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange()

    result: dict[str, list[dict]] = {}

    try:
        for tf, limit in timeframes.items():
            candles: list[dict] = []

            for attempt in range(1, max_retries + 1):
                try:
                    raw = await exchange.fetch_ohlcv(symbol, tf, limit=limit)
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
                    _log(f"fetch_multi_timeframe: {tf} → {len(candles)} candles")
                    break  # success — move to next timeframe

                except (ccxt.NetworkError, ccxt.ExchangeError, ExchangeNotAvailable) as exc:
                    _log(
                        f"fetch_multi_timeframe: {tf} {type(exc).__name__} "
                        f"(attempt {attempt}/{max_retries}): {exc}"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(2 ** attempt)

            result[tf] = candles

    finally:
        if own_exchange:
            await _close_exchange(exchange)

    return result


# ─── 1c. Order Book (Depth) ────────────────────────────────────────

async def fetch_order_book(
    symbol: str = "BTC/USDT",
    limit: int = 50,
    exchange: ccxt.binance | None = None,
) -> dict:
    """Fetch the order book depth from Binance.

    Parameters
    ----------
    symbol : str
        Trading pair.
    limit : int
        Number of bids and asks to fetch.
    exchange : ccxt.binance | None
        If provided, reuse this exchange session.

    Returns
    -------
    dict
        ``{"bids": [[price, amount], ...], "asks": [[price, amount], ...]}``
    """
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange()
    try:
        order_book = await exchange.fetch_order_book(symbol, limit=limit)
        return {
            "bids": order_book.get("bids", []),
            "asks": order_book.get("asks", []),
        }
    except Exception as exc:
        _log(f"fetch_order_book failed: {exc}")
        return {"bids": [], "asks": []}
    finally:
        if own_exchange:
            await _close_exchange(exchange)


# ─── 1d. Account Balance ───────────────────────────────────────────

async def fetch_balance(
    exchange: ccxt.binance | None = None,
    testnet: bool | None = None,
) -> float:
    """Fetch the free USDT balance from Binance.

    Parameters
    ----------
    exchange : ccxt.binance | None
        If provided, reuse this **authenticated** exchange session.
        If None, create a temporary authenticated session.
    testnet : bool | None
        Override testnet flag. Only used when creating a temp session.

    Returns
    -------
    float
        Free USDT balance.
    """
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange(authenticated=True, testnet=testnet)
    try:
        balance = await exchange.fetch_balance()
        usdt_free = float(balance.get("USDT", {}).get("free", 0))
        _log(f"fetch_balance: USDT free = {usdt_free:.2f}")
        return usdt_free
    except Exception as exc:
        _log(f"fetch_balance failed: {exc}")
        return 0.0
    finally:
        if own_exchange:
            await _close_exchange(exchange)


# ─── 2. Current ticker price ────────────────────────────────────────

async def get_current_price(
    symbol: str = "BTC/USDT",
    exchange: ccxt.binance | None = None,
) -> float:
    """Return the latest ticker price for *symbol* as a float."""
    own_exchange = exchange is None
    if own_exchange:
        exchange = _build_exchange()
    try:
        ticker = await exchange.fetch_ticker(symbol)
        price = float(ticker["last"])
        _log(f"get_current_price: {symbol} → {price}")
        return price
    finally:
        if own_exchange:
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
