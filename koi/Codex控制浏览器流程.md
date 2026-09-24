# CDP 是什么 & Codex 控制浏览器的整体流程

> 背景：Browser Use Desktop 应用（Electron），梳理 codex 作为 agent 如何驱动应用内置浏览器。

## 一、CDP 是什么

**CDP = Chrome DevTools Protocol**，Chrome/Chromium 内置的一个**调试控制协议**。平时按 F12 打开的开发者工具，背后就是通过它跟浏览器内核通信的。

- 本质是一个 **JSON-RPC 消息协议**，通过 WebSocket 传输
- 浏览器开放一个端口（如 `:9222`），任何客户端连上来，发一条 JSON 命令，浏览器执行并返回 JSON 结果。例如：

```json
→ {"method": "Page.navigate", "params": {"url": "https://example.com"}}
← {"frameId": "..."}
```

- 能力覆盖浏览器的一切：导航、点击、输入、截图、读 DOM、拦截网络、执行任意 JS……
- Playwright、Puppeteer、agent-browser、browser-harness-js —— **全都是 CDP 客户端**，只是封装程度不同

## 二、整体流程：从用户发任务到浏览器动起来

```
 ①用户输入任务
      │
      ▼
 ②Electron 主进程（编排层）
      │  a. 创建会话
      │  b. 创建内置 Chromium 浏览器视图（WebContentsView）
      │  c. 应用启动时就开了 remote-debugging-port
      │     → 这个内置浏览器有一个 CDP 端口
      │  d. 查出该浏览器视图的 targetId（CDP 里"哪个页面"的身份证）
      │
      │  spawn 子进程: codex exec --json
      │  给它三样东西:
      │    · stdin: 系统引导(告诉它:你有 browser-harness-js 工具、
      │             target 是 X、端口是 Y、读 AGENTS.md) + 用户任务
      │    · env:  BU_TARGET_ID、BU_CDP_PORT、API key(CODEX_API_KEY)
      │    · cwd:  harness 目录(里面有工具和说明书)
      ▼
 ③codex 进程（agent，自己跑 LLM 循环）
      │  codex 本身没有任何浏览器功能！
      │  它只有一个通用能力: 执行 shell 命令
      │  它"控制浏览器" = 敲命令:
      │
      │    $ browser-harness-js 'await connectToAssignedTarget()'
      │    $ browser-harness-js 'await session.Page.navigate({url:"..."})'
      │    $ browser-harness-js 'await session.Runtime.evaluate({...})'
      ▼
 ④browser-harness-js（CDP 客户端，长驻 Bun server）
      │  把 JS 调用翻译成 CDP 的 JSON 消息
      │  经 WebSocket 发到 BU_CDP_PORT，
      │  并用 BU_TARGET_ID 声明"操作的是这个页面"
      ▼
 ⑤内置 Chromium 执行
         导航/点击/截图… → 用户在应用窗口里实时看到页面变化
```

## 三、codex 的一次典型「感知-决策-行动」循环

以任务"去 example.com 把标题告诉我"为例：

1. **行动**：codex 执行 shell 命令 `browser-harness-js 'await session.Page.navigate({url:"https://example.com"})'`
2. **CDP 传输**：这条命令变成 JSON 消息 `{"method":"Page.navigate",...}` 走 WebSocket 进入内置 Chromium，浏览器真的开始加载页面
3. **感知**：codex 再执行一条命令取页面信息（比如 `document.title` 或截图），结果原样打印到它的 stdout，**codex 把它当作普通命令输出读到**
4. **决策**：LLM 看到输出，决定下一步——继续操作，还是任务完成
5. 循环往复，直到 codex 输出最终回答（`turn.completed` 事件），编排层收到后把会话标为完成

## 四、最关键的一句话总结

**codex 和浏览器之间没有任何直接通道**。整条链路是：

> codex 只会执行 shell 命令 → 环境里恰好预装了一个 CDP 客户端 CLI（browser-harness-js）→ 环境变量恰好告诉它连哪个端口、哪个页面 → 那个端口后面恰好是应用内置的 Chromium。

所以「codex 控制浏览器」是**约定出来的能力**，不是集成的能力：靠系统提示词 + AGENTS.md 教会 codex 这套工具的存在和用法。

这也是自己写 agent 层时的接入点——你的 agent 只要能执行 shell 命令（或直接说 CDP），拿到 `BU_TARGET_ID` / `BU_CDP_PORT` 这两个环境变量，就能以完全相同的方式驱动浏览器；甚至可以完全不用 browser-harness-js，用 Vercel agent-browser 或自己写的 CDP 客户端替代，浏览器和编排层完全不用动。

## 附：补充事实（来自代码确认）

- 浏览器是**应用内置的 Electron Chromium**（WebContentsView），不启动任何外部浏览器进程；每个会话一个独立视图，最多 10 个并发；User-Agent 伪装成 Firefox
- 应用启动时开 `--remote-debugging-port`，所以内置浏览器暴露的是**标准 CDP HTTP/WebSocket 端点**（`/json/list`、`/devtools/page/<id>`），任何标准 CDP 客户端都能连
- API key 注入方式：编排层 spawn codex 时设置环境变量 `CODEX_API_KEY`（先删掉继承的 `OPENAI_API_KEY`/`CODEX_API_KEY` 防止覆盖 OAuth）；没存 key 时 codex 回落到 `~/.codex/auth.json` 的订阅登录
- codex 返回：stdout NDJSON 事件流（`thread.started` 拿 thread_id 用于续聊 / `item.*` 工具调用与正文增量 / `turn.completed` 用量与结束），由 adapter 翻译成统一 HlEvent → SQLite 持久化 → IPC → UI 渲染
- browser-harness-js 的定制点：截图自动存到 `BU_OUTPUTS_DIR`，在聊天界面作为 `file_output` 事件展示（若换 agent-browser 需自己对接这条链路）
