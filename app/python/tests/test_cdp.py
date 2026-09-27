"""Tests for the hand-written CDP client in koi_agent.cdp.

The WebSocket code is the riskiest part of the Python migration — it is written
from scratch to keep the agent dependency-free — so `ConnectionAgainstRealSocket`
drives it against an actual TCP server rather than mocks.

Run with:  cd app && PYTHONPATH=python python3 python/tests/test_cdp.py
"""

from __future__ import annotations

import json
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from koi_agent import cdp  # noqa: E402

# ── server-side helpers, written independently of the code under test ─────────


def recv_exactly(conn: socket.socket, count: int) -> bytes:
    buffer = b""
    while len(buffer) < count:
        chunk = conn.recv(count - len(buffer))
        if not chunk:
            raise AssertionError(f"client closed mid-frame ({len(buffer)}/{count} bytes)")
        buffer += chunk
    return buffer


def read_frame(conn: socket.socket) -> tuple[int, bytes]:
    """Read exactly one frame. Length-prefixed reads matter: guessing from
    whatever a single recv() returned silently accepts truncated frames."""
    head = recv_exactly(conn, 2)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", recv_exactly(conn, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", recv_exactly(conn, 8))[0]
    # Client frames are masked and the 4-byte key precedes the payload.
    key = recv_exactly(conn, 4) if masked else None
    payload = recv_exactly(conn, length) if length else b""
    if key:
        payload = bytes(b ^ key[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def encode_frame(payload: bytes, opcode: int = 0x1) -> bytes:
    """Server-to-client frames are unmasked, per RFC 6455."""
    length = len(payload)
    head = bytearray([0x80 | opcode])
    if length < 126:
        head.append(length)
    elif length < 1 << 16:
        head.append(126)
        head += struct.pack(">H", length)
    else:
        head.append(127)
        head += struct.pack(">Q", length)
    return bytes(head) + payload


class ParseWsUrl(unittest.TestCase):
    def test_splits_host_port_and_path(self):
        self.assertEqual(
            cdp._parse_ws_url("ws://127.0.0.1:56353/devtools/page/ABC123"),
            ("127.0.0.1", 56353, "/devtools/page/ABC123"),
        )

    def test_defaults_the_port_and_root_path(self):
        self.assertEqual(cdp._parse_ws_url("ws://example.com"), ("example.com", 80, "/"))

    def test_rejects_secure_websockets(self):
        # A remote wss:// endpoint would need TLS; this client is localhost-only
        # by design and should say so instead of silently mis-parsing.
        with self.assertRaises(cdp.CdpError):
            cdp._parse_ws_url("wss://remote.example/cdp?token=x")


class ConnectionAgainstRealSocket(unittest.TestCase):
    """Spin up a TCP server that speaks just enough WebSocket + CDP to answer
    commands, then drive `_Connection` against it."""

    def _serve(self, handler) -> int:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def run():
            conn, _ = listener.accept()
            with conn:
                try:
                    handler(conn)
                except Exception as exc:  # surface server-side bugs as test noise
                    print(f"[test server] handler failed: {exc!r}", file=sys.stderr)
                finally:
                    listener.close()

        threading.Thread(target=run, daemon=True).start()
        return port

    @staticmethod
    def _accept_upgrade(conn: socket.socket) -> dict[str, str]:
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = conn.recv(1)
            if not chunk:
                raise AssertionError("client closed during handshake")
            head += chunk
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: irrelevant\r\n\r\n"
        )
        lines = head.decode("ascii").split("\r\n")
        return {"request_line": lines[0], "raw": head.decode("ascii")}

    def test_handshake_and_round_trip(self):
        seen: dict[str, object] = {}

        def handler(conn: socket.socket) -> None:
            seen.update(self._accept_upgrade(conn))
            opcode, payload = read_frame(conn)
            message = json.loads(payload.decode("utf-8"))
            seen["opcode"] = opcode
            seen["message"] = message
            reply = {
                "id": message["id"],
                "result": {"result": {"type": "string", "value": "marked"}},
            }
            conn.sendall(encode_frame(json.dumps(reply).encode("utf-8")))

        port = self._serve(handler)
        with cdp._Connection(f"ws://127.0.0.1:{port}/devtools/page/T1", timeout=5.0) as conn:
            result = conn.call("Runtime.evaluate", {"expression": "document.title = 'x'"})

        self.assertEqual(result, {"result": {"type": "string", "value": "marked"}})
        self.assertIn("GET /devtools/page/T1 HTTP/1.1", seen["request_line"])
        self.assertIn("Upgrade: websocket", seen["raw"])
        self.assertIn("Sec-WebSocket-Key: ", seen["raw"])
        self.assertEqual(seen["opcode"], 0x1)
        self.assertEqual(seen["message"]["method"], "Runtime.evaluate")

    def test_skips_events_and_answers_to_a_ping(self):
        def handler(conn: socket.socket) -> None:
            self._accept_upgrade(conn)
            read_frame(conn)  # the command
            conn.sendall(encode_frame(b"ping!", opcode=0x9))
            opcode, payload = read_frame(conn)  # expect a pong echo
            assert opcode == 0xA and payload == b"ping!", f"expected pong, got {opcode}/{payload!r}"
            # Events carry no `id` and must be skipped, not mistaken for a reply.
            conn.sendall(encode_frame(json.dumps({"method": "Runtime.consoleAPICalled"}).encode()))
            conn.sendall(encode_frame(json.dumps({"id": 1, "result": {"ok": True}}).encode()))

        port = self._serve(handler)
        with cdp._Connection(f"ws://127.0.0.1:{port}/devtools/page/T1", timeout=5.0) as conn:
            self.assertEqual(conn.call("Page.enable"), {"ok": True})

    def test_surfaces_a_cdp_error(self):
        def handler(conn: socket.socket) -> None:
            self._accept_upgrade(conn)
            _, payload = read_frame(conn)
            message = json.loads(payload.decode("utf-8"))
            reply = {
                "id": message["id"],
                "error": {"code": -32601, "message": "'Method not found'"},
            }
            conn.sendall(encode_frame(json.dumps(reply).encode("utf-8")))

        port = self._serve(handler)
        with cdp._Connection(f"ws://127.0.0.1:{port}/devtools/page/T1", timeout=5.0) as conn:
            with self.assertRaises(cdp.CdpError) as caught:
                conn.call("Nope.nope")
        self.assertIn("Method not found", str(caught.exception))

    def test_rejects_a_non_101_handshake(self):
        def handler(conn: socket.socket) -> None:
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = conn.recv(1)
                if not chunk:
                    return
                head += chunk
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")

        port = self._serve(handler)
        with self.assertRaises(cdp.CdpError) as caught:
            with cdp._Connection(f"ws://127.0.0.1:{port}/devtools/page/T1", timeout=5.0):
                pass
        self.assertIn("403", str(caught.exception))

    def test_large_payloads_use_extended_length(self):
        """CDP results routinely exceed the 125-byte inline length, so both the
        16-bit client form and the server's own framing must be right."""
        big = "x" * 70_000

        def handler(conn: socket.socket) -> None:
            self._accept_upgrade(conn)
            _, payload = read_frame(conn)
            message = json.loads(payload.decode("utf-8"))
            self.assertEqual(len(message["params"]["expression"]), 70_000)
            reply = {"id": message["id"], "result": {"value": big}}
            conn.sendall(encode_frame(json.dumps(reply).encode("utf-8")))
            # Drain before returning: closing while the client still has 70 KB to
            # read makes the kernel RST the connection and discard it.
            while conn.recv(4096):
                pass

        port = self._serve(handler)
        with cdp._Connection(f"ws://127.0.0.1:{port}/devtools/page/T1", timeout=10.0) as conn:
            result = conn.call("Runtime.evaluate", {"expression": big})
        self.assertEqual(result["value"], big)


if __name__ == "__main__":
    unittest.main(verbosity=2)
