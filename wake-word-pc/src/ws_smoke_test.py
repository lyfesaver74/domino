import asyncio

import websockets


async def main() -> None:
    async with websockets.connect("ws://127.0.0.1:8765/ws") as ws:
        for _ in range(6):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=3.0)
            except asyncio.TimeoutError:
                print("(timeout waiting for message)", flush=True)
                return
            print(msg, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
