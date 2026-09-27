"""Koi 浏览器代理的 Python 后端。

通过 NDJSON HlEvent 与 Electron 通信。cdp.py 负责识别所属 target，
browser.py 负责 agent-browser 命令与绑定，Executor 统一执行动作。
宿主接入点位于 app/src/main/hl/engines/python/adapter.ts。"""

from . import browser, cdp, protocol

__all__ = ["browser", "cdp", "protocol"]
