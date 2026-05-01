"""Quick WebSocket connection test."""
import asyncio
import json
import websockets


async def test():
    async with websockets.connect("ws://127.0.0.1:8001/ws") as ws:
        # Receive snapshot
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        print(f"Message 1 type: {msg['type']}")
        data = msg.get("data", {})
        print(f"  Keys: {list(data.keys())}")
        print(f"  Bot status: {data.get('bot_status', 'N/A')}")
        print(f"  BTC price: {data.get('current_btc_price', 'N/A')}")

        # Wait for a price broadcast
        msg2 = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        print(f"Message 2 type: {msg2['type']}")
        if msg2["type"] == "price":
            print(f"  Live BTC: ${msg2['price']:,.2f}")

        print("WebSocket OK!")


asyncio.run(test())
