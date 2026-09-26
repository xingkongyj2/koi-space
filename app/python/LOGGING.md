# Python 后端调试日志

每次 agent 进程运行生成 `app/python/log/agent-<UTC时间>-<PID>.jsonl`。
路径相对 Python 包的位置解析，不依赖启动目录或 Electron userData。
新任务自动生效，运行中的任务需要重新启动。

每行是一个 JSON 对象。`time` 是 UTC 时间，`session_id` 是任务 ID；
进入执行步骤后还有 `step_id` 和 `iteration`。`call_id` 和
`parent_call_id` 关联嵌套调用。阶段的 `.start` 记录输入，`.end`
记录返回值和耗时 `ms`，`.error` 记录异常及 traceback。

| stage | 内容 |
| --- | --- |
| `task.received` | 任务输入 |
| `task.configuration` | 使用的模型和运行预算，不包含 API key |
| `planner.plan.start` | 规划输入 |
| `model.http.start` | 模型地址、请求体和超时，不包含认证头 |
| `model.response.raw` | 完整 HTTP 响应文本及状态码 |
| `model.response.parsed` | 解析后的响应 JSON |
| `model.responses.end` | 从响应中提取的模型文本 |
| `planner.json.decoded` | 模型文本解析成的原始计划 JSON |
| `planner.parse.end` / `planner.plan.end` | 校验、规范化后的计划 |
| `model.jev.end` | Jev 选择映射到页面引用后的结果 |
| `decision.model.raw` / `decision.model.parsed` | 文本决策原文及解析结果 |
| `decision.choose.end` | 过滤、转换后的动作和置信度 |
| `observer.capture.end` | 页面快照、差异和元素列表 |
| `executor.command` | 实际执行的命令参数 |
| `browser.run.end` / `browser.cli.end` | 浏览器返回，包括完整 stdout/stderr |
| `validator.action.end` / `validator.step.end` | 动作和步骤校验结果，输入在对应 start 中 |
| `reflection.advise.end` | 反思建议 |
| `orchestrator.iteration` | 当前预算、失败次数和重试建议 |
| `orchestrator.step.exhausted` | 预算或重试耗尽时的状态 |
| `*.fallback` | 被捕获后继续降级执行的异常 |

`kind=event` 保存发给界面的事件，`kind=diagnostic` 保存原有诊断消息。
结构化 trace 不额外截断字段；模型输入本身的快照长度限制仍然保留，
完整观察结果可查看 `observer.capture.end`。日志会包含任务正文、页面内容
和填入表单的数据，目前不会自动轮转或清理。`log/` 已被 Git 忽略。

在仓库根目录实时查看某次运行（替换文件名）：

```sh
tail -f app/python/log/agent-<UTC时间>-<PID>.jsonl
```

使用 jq 筛选某任务的阶段和返回结果：

```sh
jq 'select(.session_id == "任务ID") | {time, stage, step_id, iteration, call_id, result, data, error}' app/python/log/agent-*.jsonl
```
