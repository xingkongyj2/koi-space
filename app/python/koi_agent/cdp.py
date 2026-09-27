"""不依赖第三方库的最小 CDP 客户端。

通过 HTTP /json/list 查找 targetId，再使用页面级 WebSocket 执行
Runtime.evaluate，给空白标签页添加可区分的标题标记。
仅支持本地 CDP 所需的文本帧、ping/pong 和关闭帧；不实现通用
WebSocket 的扩展、压缩及分片重组。"""

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
    """CDP 端点不可达、目标缺失或命令被拒绝。"""


# ── HTTP discovery ──────────────────────────────────────────────────────────


def list_targets(port: int, timeout: float = DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """通过 /json/list 获取浏览器当前暴露的全部目标。"""
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
    """按 targetId 精确查找页面；不存在时抛出异常。"""
    for entry in list_targets(port, timeout=timeout):
        if entry.get("id") == target_id:
            return entry
    raise CdpError(
        f"target {target_id} is not exposed on CDP port {port}. "
        "The browser view may have been closed before the agent started."
    )


# ── WebSocket ───────────────────────────────────────────────────────────────


def _parse_ws_url(ws_url: str) -> tuple[str, int, str]:
    """解析本地明文 WebSocket 地址，不支持远程 TLS 连接。"""
    if not ws_url.startswith("ws://"):
        raise CdpError(f"only plain ws:// endpoints are supported, got {ws_url!r}")
    rest = ws_url[len("ws://") :]
    hostport, _, path = rest.partition("/")
    host, _, port_text = hostport.partition(":")
    return host or "127.0.0.1", int(port_text or 80), "/" + path


def _handshake(sock: socket.socket, host: str, port: int, path: str) -> None:
    """发送升级请求并验证 101 响应，不越界读取后续帧。"""
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
    """编码单个带随机掩码的客户端帧，按负载长度选择帧头格式。"""
    length = len(payload)
    header = bytearray([0x80 | opcode])  # FIN + opcode
    if length < 126:
        header.append(0x80 | length)  # client frames must be masked
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
    """循环读取指定字节数，中途断连则明确报错。"""
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
    """解码单个帧的长度、操作码和可选掩码。"""
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
    """以上下文管理器维护单个页面的 CDP WebSocket 连接。"""

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
        """发送一个带 ID 的 CDP 命令，仅接收对应响应，忽略其他事件。"""
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
    """在指定 target 的页面中执行表达式，并返回可序列化结果。"""
    target = find_target(port, target_id, timeout=timeout)
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        raise CdpError(f"target {target_id} exposes no webSocketDebuggerUrl")
    with _Connection(ws_url, timeout) as conn:
        result = conn.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
            },
        )
    if not isinstance(result, dict):
        raise CdpError(f"Runtime.evaluate returned {type(result).__name__}")
    exception = result.get("exceptionDetails")
    if exception:
        raise CdpError(f"page threw while evaluating: {exception.get('text', exception)}")
    return (result.get("result") or {}).get("value")
