"""Offline regressions for text chunking and stream resource lifetimes."""

import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock

import aiohttp
from aiohttp import web

import edge_tts.communicate as communicate
from edge_tts.exceptions import NoAudioReceived


def _run(coroutine):
    """Run one async test scenario without requiring a newer unittest API."""
    return asyncio.run(coroutine)


async def _start_websocket_server(handler):
    app = web.Application()
    app.router.add_get("/ws", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, "ws://127.0.0.1:%d/ws?test=1" % port


async def _receive_synthesis_request(request):
    websocket = web.WebSocketResponse()
    await websocket.prepare(request)
    await websocket.receive()  # speech.config
    await websocket.receive()  # SSML
    return websocket


async def _send_audio_and_finish(websocket):
    headers = (
        b"X-RequestId:test\r\n"
        b"Content-Type:audio/mpeg\r\n"
        b"Path:audio\r\n"
    )
    await websocket.send_bytes(len(headers).to_bytes(2, "big") + headers + b"audio")
    await websocket.send_str("Path:turn.end\r\n\r\n")
    await websocket.close()


class TextChunkingTests(unittest.TestCase):
    def assert_bounded_and_reconstructed(self, source, limit):
        chunks = list(communicate.split_text_by_byte_length(source, limit))
        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= limit for chunk in chunks))
        for chunk in chunks:
            chunk.decode("utf-8")
        expected = source.encode("utf-8") if isinstance(source, str) else source
        self.assertEqual(b"".join(chunks), expected)
        return chunks

    def test_unspaced_ascii_and_cjk_stay_within_limit(self):
        for source in ("a" * 10000, "汉" * 3000, "x" * 12000):
            with self.subTest(length=len(source)):
                self.assert_bounded_and_reconstructed(source, 4096)

    def test_utf8_character_crossing_the_limit_is_not_split(self):
        source = "a" * 4093 + "😀" + "b" * 10
        self.assert_bounded_and_reconstructed(source, 4096)

    def test_exact_limit_and_one_byte_over(self):
        self.assertEqual(
            self.assert_bounded_and_reconstructed("a" * 4096, 4096),
            [b"a" * 4096],
        )
        self.assert_bounded_and_reconstructed("a" * 4097, 4096)

    def test_entity_at_byte_boundary_stays_whole(self):
        source = b"a" * 4095 + b"&amp;" + b"z" * 20
        chunks = self.assert_bounded_and_reconstructed(source, 4096)
        self.assertEqual(sum(chunk.count(b"&amp;") for chunk in chunks), 1)

    def test_escaped_communicate_text_is_bounded_without_splitting_entities(self):
        original = "a" * 4093 + "&<" + "z" * 20
        expected = ("a" * 4093 + "&amp;&lt;" + "z" * 20).encode("utf-8")
        chunks = list(communicate.Communicate(original).texts)
        self.assertTrue(all(0 < len(chunk) <= 4096 for chunk in chunks))
        self.assertEqual(b"".join(chunks), expected)
        self.assertEqual(sum(chunk.count(b"&amp;") for chunk in chunks), 1)
        self.assertEqual(sum(chunk.count(b"&lt;") for chunk in chunks), 1)

    def test_too_small_limit_fails_without_dropping_a_character_or_entity(self):
        for source, limit in (("汉", 2), (b"&amp;x", 4)):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    list(communicate.split_text_by_byte_length(source, limit))

    def test_whitespace_preference_and_input_validation_are_preserved(self):
        self.assertEqual(
            list(communicate.split_text_by_byte_length("alpha beta\nz zzz", 15)),
            [b"alpha beta", b"z zzz"],
        )
        self.assertEqual(
            list(communicate.split_text_by_byte_length("  alpha  beta  ", 8)),
            [b"alpha", b"beta"],
        )
        self.assertEqual(list(communicate.split_text_by_byte_length("", 8)), [])
        self.assertEqual(
            list(communicate.split_text_by_byte_length(b"abc", 8)), [b"abc"]
        )
        with self.assertRaises(ValueError):
            list(communicate.split_text_by_byte_length("abc", 0))
        with self.assertRaises(TypeError):
            list(communicate.split_text_by_byte_length(123, 8))


class SyncStreamTests(unittest.TestCase):
    def run_sync_child(self, source):
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, "-c", source],
            capture_output=True,
            text=True,
            timeout=5,
            env=environment,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sync stream checks passed", result.stdout)

    def test_worker_failure_before_first_item_is_raised_and_loop_is_closed(self):
        self.run_sync_child(
            """
import asyncio
import threading
from edge_tts import Communicate

original_new_event_loop = asyncio.new_event_loop
loops = []
def track_loop():
    loop = original_new_event_loop()
    loops.append(loop)
    return loop
asyncio.new_event_loop = track_loop

failure = RuntimeError('before first item')
class Broken(Communicate):
    async def stream(self):
        raise failure
        yield

for _ in range(4):
    try:
        next(Broken('test').stream_sync())
    except RuntimeError as error:
        assert error is failure
    else:
        raise AssertionError('worker failure became normal end-of-stream')

assert len(loops) == 4
assert all(loop.is_closed() for loop in loops)
assert not any(
    thread.name.startswith('ThreadPoolExecutor')
    for thread in threading.enumerate()
)
print('sync stream checks passed')
"""
        )

    def test_worker_failure_after_an_item_keeps_order_and_original_error(self):
        self.run_sync_child(
            """
import aiohttp
from edge_tts import Communicate

failure = aiohttp.ClientConnectionError('after an item')
item = {'type': 'audio', 'data': b'first'}
class Broken(Communicate):
    async def stream(self):
        yield item
        raise failure

stream = Broken('test').stream_sync()
assert next(stream) == item
try:
    next(stream)
except aiohttp.ClientConnectionError as error:
    assert error is failure
else:
    raise AssertionError('worker failure became normal end-of-stream')
print('sync stream checks passed')
"""
        )

    def test_empty_and_ordered_streams_finish(self):
        class Sample(communicate.Communicate):
            def __init__(self, values):
                super().__init__("test")
                self.values = values

            async def stream(self):
                for value in self.values:
                    await asyncio.sleep(0)
                    yield value

        self.assertEqual(list(Sample([]).stream_sync()), [])
        values = [
            {"type": "audio", "data": b"one"},
            {"type": "audio", "data": b"two"},
        ]
        self.assertEqual(list(Sample(values).stream_sync()), values)


    def test_early_close_does_not_deadlock_or_leave_worker_thread(self):
        self.run_sync_child(
            """
import asyncio
import threading
from edge_tts import Communicate

values = [
    {'type': 'audio', 'data': b'one'},
    {'type': 'audio', 'data': b'two'},
    {'type': 'audio', 'data': b'three'},
]
class Sample(Communicate):
    async def stream(self):
        for value in values:
            await asyncio.sleep(0)
            yield value

early = Sample('test').stream_sync()
assert next(early) == values[0]
early.close()
assert not any(
    thread.name.startswith('ThreadPoolExecutor')
    for thread in threading.enumerate()
)
print('sync stream checks passed')
"""
        )

    def test_event_loop_creation_failure_wakes_consumer(self):
        self.run_sync_child(
            """
import asyncio
from edge_tts import Communicate

failure = RuntimeError('loop creation failed')
def fail_new_event_loop():
    raise failure
asyncio.new_event_loop = fail_new_event_loop

try:
    next(Communicate('test').stream_sync())
except RuntimeError as error:
    assert error is failure
else:
    raise AssertionError('loop setup failure became normal end-of-stream')
print('sync stream checks passed')
"""
        )


class ConnectorOwnershipTests(unittest.TestCase):
    def test_supplied_connector_survives_403_retry_and_multiple_chunks(self):
        async def scenario():
            requests = 0

            async def handler(request):
                nonlocal requests
                requests += 1
                if requests == 1:
                    return web.Response(status=403)
                websocket = await _receive_synthesis_request(request)
                await _send_audio_and_finish(websocket)
                return websocket

            runner, url = await _start_websocket_server(handler)
            old_url = communicate.WSS_URL
            communicate.WSS_URL = url
            connector = aiohttp.TCPConnector()
            try:
                instance = communicate.Communicate(
                    "word " * 1000, connector=connector
                )
                with mock.patch.object(
                    communicate.DRM, "handle_client_response_error"
                ):
                    messages = [message async for message in instance.stream()]
                self.assertTrue(messages)
                self.assertGreaterEqual(
                    requests, 3
                )  # retry plus at least two text chunks
                self.assertFalse(connector.closed)
            finally:
                communicate.WSS_URL = old_url
                await connector.close()
                await runner.cleanup()

        _run(scenario())

    def test_supplied_connector_stays_open_when_a_later_chunk_fails(self):
        async def scenario():
            requests = 0

            async def handler(request):
                nonlocal requests
                requests += 1
                websocket = await _receive_synthesis_request(request)
                if requests == 1:
                    await _send_audio_and_finish(websocket)
                else:
                    await websocket.send_str("Path:turn.end\r\n\r\n")
                    await websocket.close()
                return websocket

            runner, url = await _start_websocket_server(handler)
            old_url = communicate.WSS_URL
            communicate.WSS_URL = url
            connector = aiohttp.TCPConnector()
            try:
                instance = communicate.Communicate(
                    "word " * 1000, connector=connector
                )
                with self.assertRaises(NoAudioReceived):
                    _ = [message async for message in instance.stream()]
                self.assertEqual(requests, 2)
                self.assertFalse(connector.closed)
            finally:
                communicate.WSS_URL = old_url
                await connector.close()
                await runner.cleanup()

        _run(scenario())

    def test_internally_owned_connectors_close_after_success_and_error(self):
        async def scenario():
            for fail_second in (False, True):
                requests = 0

                async def handler(request):
                    nonlocal requests
                    requests += 1
                    websocket = await _receive_synthesis_request(request)
                    if fail_second and requests == 2:
                        await websocket.send_str("Path:turn.end\r\n\r\n")
                        await websocket.close()
                    else:
                        await _send_audio_and_finish(websocket)
                    return websocket

                runner, url = await _start_websocket_server(handler)
                old_url = communicate.WSS_URL
                communicate.WSS_URL = url
                sessions = []
                connectors = []
                original_client_session = aiohttp.ClientSession

                def capture_client_session(*args, **kwargs):
                    session = original_client_session(*args, **kwargs)
                    sessions.append(session)
                    connectors.append(session.connector)
                    return session

                try:
                    instance = communicate.Communicate("word " * 1000)
                    with mock.patch.object(
                        communicate.aiohttp,
                        "ClientSession",
                        capture_client_session,
                    ):
                        if fail_second:
                            with self.assertRaises(NoAudioReceived):
                                _ = [message async for message in instance.stream()]
                        else:
                            _ = [message async for message in instance.stream()]
                    self.assertGreaterEqual(requests, 2)
                    self.assertTrue(sessions)
                    self.assertTrue(all(session.closed for session in sessions))
                    self.assertTrue(all(connector.closed for connector in connectors))
                finally:
                    communicate.WSS_URL = old_url
                    await runner.cleanup()

        _run(scenario())


if __name__ == "__main__":
    unittest.main()
