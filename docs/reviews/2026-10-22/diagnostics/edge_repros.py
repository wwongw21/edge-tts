"""Read-only edge-tts review reproductions; all WebSocket traffic stays on loopback."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, "/workspace/repos/edge-tts/src")
import aiohttp
from aiohttp import web
import edge_tts.communicate as communicate


def check_split_limit():
    for label, text in (("ASCII", "a" * 10000), ("CJK", "汉" * 3000)):
        chunks = list(communicate.split_text_by_byte_length(text, 4096))
        print(label, "chunk byte lengths:", [len(chunk) for chunk in chunks])
        assert max(map(len, chunks)) > 4096, "Expected the reviewed cap violation"


def check_sync_failure():
    code = '''from edge_tts import Communicate
class Broken(Communicate):
 async def stream(self):
  raise RuntimeError("synthetic transport failure")
  yield
next(Broken("hello").stream_sync())
'''
    env = dict(os.environ, PYTHONPATH="/workspace/repos/edge-tts/src", PYTHONDONTWRITEBYTECODE="1")
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=2, env=env)
        raise AssertionError(f"Expected hang; child exited {result.returncode}: {result.stderr}")
    except subprocess.TimeoutExpired:
        print("stream_sync transport failure: hung; child killed after 2 seconds")


async def check_connector_lifetime():
    requests = 0

    async def handler(request):
        nonlocal requests
        requests += 1
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for _ in range(2):
            await ws.receive()
        headers = b"X-RequestId:test\r\nContent-Type:audio/mpeg\r\nPath:audio\r\n"
        await ws.send_bytes(len(headers).to_bytes(2, "big") + headers + b"fake mp3 bytes")
        await ws.send_str("Path:turn.end\r\n\r\n")
        await ws.close()
        return ws

    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    old_url = communicate.WSS_URL
    communicate.WSS_URL = f"ws://127.0.0.1:{port}/ws?x=1"
    connector = aiohttp.TCPConnector()
    instance = communicate.Communicate("word " * 1000, connector=connector)
    try:
        try:
            _ = [item async for item in instance.stream()]
            raise AssertionError("Expected second-chunk failure")
        except RuntimeError as error:
            assert str(error) == "Session is closed"
            assert connector.closed and requests == 1
            print("custom connector: second chunk failed with", repr(str(error)), "; connector closed after", requests, "request")
    finally:
        communicate.WSS_URL = old_url
        await connector.close()
        await runner.cleanup()


if __name__ == "__main__":
    check_split_limit()
    check_sync_failure()
    asyncio.run(check_connector_lifetime())
