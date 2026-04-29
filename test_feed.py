import asyncio
from core.data_feed import fetch_ohlcv

data = asyncio.run(fetch_ohlcv("BTC/USDT", "5m", 5))
print(f"Got {len(data)} candles. Latest close: {data[-1]['close']}")
for c in data:
    print(c)
