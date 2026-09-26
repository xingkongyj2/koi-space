# Python 后端调试日志

在 PyCharm 中打开 `app/python/debug_task.py`，修改文件底部的入口，然后点击 Run：

```python
if __name__ == "__main__":
    user_input = "打开 https://example.com"  # 修改成你要测试的问题
    browser_path = None
    user_data_dir = None
    cdp_port = None
    target_id = None
    raise SystemExit(run_debug(
        user_input,
        browser_path=browser_path,
        user_data_dir=user_data_dir,
        cdp_port=cdp_port,
        target_id=target_id,
    ))
```

不需要设置命令行参数。PyCharm 使用 Python 3.11 或更高版本。

默认向本项目正在运行的桌面应用提交任务，由应用准备浏览器并调用完整 Python agent，
按任务运行规划、观察、决策、执行、校验、反思/重试并输出最终事件。
实际经过哪些阶段取决于任务和规划结果（例如成功时不会触发失败反思）。
脚本等待任务结束，打印最终结果及 Python 日志路径。

优先检查当前分支的隔离 profile，再检查平台默认 profile；
应用使用自定义目录时，在入口里设置 `user_data_dir`，或使用已有的 `AGB_USER_DATA_DIR` 环境配置。
应用模式使用应用启动时的模型配置。等待时间默认 600 秒；超时或中断脚本不会停止应用中的任务。

如果项目应用未启动，在入口里填写自己的浏览器路径：

```python
browser_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
```

脚本会启动该浏览器的独立测试实例，使用临时 profile，没有日常浏览器的登录状态；
测试结束后自动关闭。该模式需要安装 `agent-browser` 并配置真实模型。
可通过 `run_debug` 的 `config_path` 和 `headless` 参数设置模型配置文件及隐藏窗口。

如果要连接已运行、开启 CDP 的浏览器，设置 `cdp_port` 和 `target_id`，
并保持 `browser_path`、`user_data_dir` 为 `None`；这种方式不会关闭已有浏览器。

每次 agent 进程运行在 `app/python/log/` 生成两份同名日志：

- `agent-<UTC时间>-<PID>.log`：直接阅读，带时间和阶段标题、记录分隔线、缩进 JSON；多行文本和模型 JSON 字符串在下方单独展开。
- `agent-<UTC时间>-<PID>.jsonl`：保留原始字段和类型，供程序或 jq 分析。

路径相对 Python 包的位置解析，不依赖启动目录或 Electron userData。
新任务自动生效，运行中的任务需要重新启动。

JSONL 中每行是一个 JSON 对象。`time` 是 UTC 时间，`session_id` 是任务 ID；
进入执行步骤后还有 `step_id` 和 `iteration`。`call_id` 和
`parent_call_id` 关联嵌套调用。阶段的 `.start` 记录输入，`.end`
记录返回值和耗时 `ms`，`.error` 记录异常及 traceback。

| stage | 内容 |
| --- | --- |
| `task.received` | 任务输入 |
| `task.configuration` | 使用的模型和运行预算，不包含 API key |
| `planner.plan.start` | 规划输入 |
| `model.request` | 模型地址、请求体和超时，不包含认证头 |
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
| `validator.action.end` / `validator.step.end` | 动作和步骤校验结果；`unchanged` 表示命令成功但页面暂未显示变化，不等于动作失败 |
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
tail -f app/python/log/agent-<UTC时间>-<PID>.log
```

使用 jq 筛选某任务的阶段和返回结果：

```sh
jq 'select(.session_id == "任务ID") | {time, stage, step_id, iteration, call_id, result, data, error}' app/python/log/agent-*.jsonl
```
