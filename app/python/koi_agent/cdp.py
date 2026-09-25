"""Minimal Chrome DevTools Protocol client — deliberately dependency-free.

Only two capabilities are needed, and both exist so `browser.py` can answer one
question: *which of the browser's page targets is the one this session owns?*

- `list_targets()` — plain HTTP `GET /json/list`. Authoritative id → url/title
  mapping, because the app hands us a CDP targetId and agent-browser does not
  understand targetIds at all.
- `evaluate()` — one `Runtime.evaluate` over the page-level WebSocket, used to
  plant a unique `document.title` marker when several targets would otherwise be
  indistinguishable (every fresh session view starts at `about:blank`).

A real WebSocket client is written here rather than pulled in as a dependency so
that a packaged app never depends on a pip install having succeeded. It covers
text frames, continuation, ping/pong and close — enough for CDP, which sends one
JSON object per frame. It is NOT a general-purpose implementation: no
fragmentation reassembly across extensions, no permessage-deflate.
"""

from __future__ import annotations

import base64
import json
import os
import socket
import struct
import urllib.error
import urllib.request
from typing import Any

FRAME_TEXT = 0x1
FRAME_CLOSE = 0x8
FRAME_PING = 0x9
FRAME_PONG = 0xA

DEFAULT_TIMEOUT = 10.0


class CdpError(RuntimeError):
    """CDP endpoint unreachable, target missing, or command rejected."""


# ── HTTP discovery ──────────────────────────────────────────────────────────

def list_targets(port: int, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Every target the browser exposes, as reported by `/json/list`."""
    url = f"http://127.0.0.1:{port}/json/list"
    request = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise CdpError(f"GET {url} failed: {exc}") from exc
    if not isinstance(payload, list):
        raise CdpError(f"{url} returned {type(payload).__name__}, expected a list")
    return payload


def find_target(port: int, target_id: str, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """The `/json/list` entry for `target_id`, or raise."""
    for entry in list_targets(port, timeout=timeout):
        if entry.get("id") == target_id:
            return entry
    raise CdpError(
        f"target {target_id} is not exposed on CDP port {port}. "
        "The browser view may have been closed before the agent started."
    )


# ── WebSocket ───────────────────────────────────────────────────────────────

def _parse_ws_url(ws_url: str) -> tuple[str, int, str]:
    if not ws_url.startswith("ws://"):
        raise CdpError(f"only plain ws:// endpoints are supported, got {ws_url!r}")
    rest = ws_url[len("ws://"):]
    hostport, _, path = rest.partition("/")
    host, _, port_text = hostport.partition(":")
    return host or "127.0.0.1", int(port_text or 80), "/" + path


def _handshake(sock: socket.socket, host: str, port: int, path: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))

    # Read the response head byte-by-byte: CDP servers are local and fast, and
    # this avoids buffering past the header into frame bytes.
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1)
        if not chunk:
            raise CdpError("connection closed during WebSocket handshake")
        head += chunk
        if len(head) > 16384:
            raise CdpError("WebSocket handshake headers exceeded 16 KB")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    if " 101 " not in status_line:
        raise CdpError(f"WebSocket handshake rejected: {status_line}")


def _send_frame(sock: socket.socket, payload: bytes, opcode: int = FRAME_TEXT) -> None:
    length = len(payload)
    header = bytearray([0x80 | opcode])  # FIN + opcode
    if length < 126:
        header.append(0x80 | length)      # client frames must be masked
    elif length < 1 << 16:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    mask = os.urandom(4)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    remaining = count
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise CdpError("connection closed while reading a WebSocket frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _recv_frame(sock: socket.socket) -> tuple[int, bytes]:
    head = _recv_exact(sock, 2)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recv_exact(sock, 8))[0]
    mask = _recv_exact(sock, 4) if masked else None
    payload = _recv_exact(sock, length) if length else b""
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


class _Connection:
    """One CDP page-level WebSocket, used as a context manager."""

    def __init__(self, ws_url: str, timeout: float):
        self._host, self._port, self._path = _parse_ws_url(ws_url)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 1

    def __enter__(self) -> "_Connection":
        sock = socket.create_connection((self._host, self._port), timeout=self._timeout)
        sock.settimeout(self._timeout)
        try:
            _handshake(sock, self._host, self._port, self._path)
        except BaseException:
            sock.close()
            raise
        self._sock = sock
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        """Send one command and return its `result`, raising on a CDP error."""
        assert self._sock is not None
        message_id = self._next_id
        self._next_id += 1
        body = json.dumps({"id": message_id, "method": method, "params": params or {}})
        _send_frame(self._sock, body.encode("utf-8"))

        while True:
            opcode, payload = _recv_frame(self._sock)
            if opcode == FRAME_PING:
                _send_frame(self._sock, payload, FRAME_PONG)
                continue
            if opcode == FRAME_CLOSE:
                raise CdpError(f"server closed the connection while awaiting {method}")
            if opcode != FRAME_TEXT:
                continue  # CDP only speaks text; skip anything else
            try:
                message = json.loads(payload.decode("utf-8"))
            except ValueError:
                continue
            # Events have no `id`; keep reading until our reply arrives.
            if message.get("id") != message_id:
                continue
            if "error" in message:
                error = message["error"]
                raise CdpError(f"{method} failed: {error.get('message', error)}")
            return message.get("result")


def evaluate(port: int, target_id: str, expression: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Run `expression` in the target's page and return its value."""
    target = find_target(port, target_id, timeout=timeout)
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CdpError(f"target {target_id} exposes no webSocketDebuggerUrl")
    with _Connection(ws_url, timeout) as conn:
        result = conn.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": False,
        })
    if not isinstance(result, dict):
        raise CdpError(f"Runtime.evaluate returned {type(result).__name__}")
    exception = result.get("exceptionDetails")
    if exception:
        raise CdpError(f"page threw while evaluating: {exception.get('text', exception)}")
    return (result.get("result") or {}).get("value")
