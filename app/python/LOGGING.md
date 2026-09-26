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

每次 agent 进程运行只在 `app/python/log/` 生成一份格式化日志：
`agent-<UTC时间>-<PID>.log`。不再生成 JSONL 或单独的 timing 日志。

日志标签使用中文，模型原文、网页内容和实际命令参数保持原样。
每层结束时输出一个块，标题写清层名和总耗时，正文合并最终输入、输出、
模型请求及请求耗时。多行网页内容和模型文本在块内展开。异常也在同一块中记录。

| 层 | 保留内容 |
| --- | --- |
| 规划层 · ①入口定位 | 输入、定位模型请求体与输出、请求耗时、最终入口 |
| 规划层 · ②任务拆分 | 输入、规划模型请求体与输出、请求耗时、最终计划；修复请求同块保留 |
| 观察层 | 网页网址、内容、交互元素和变化、耗时 |
| 决策层 | 输入、JEV 请求与请求耗时、最终动作；使用文本模型时同样保留请求 |
| 执行层 · 本轮命令 | 动作输入、实际命令序列（含清空和聚焦）、执行结果、耗时 |
| 验证层 / 验收层 | 输入和最终校验结果；模型验收请求同块保留 |
| 反思层 / 技能层 / 记忆层 | 最终输入、输出和耗时，仅实际调用时记录 |
| 执行层 · 子任务 / 最终结果 | 子任务输入、最终结果和总耗时 |
| 任务层 | 任务输入、最终消息和总耗时 |

不再单独打印层级 start/end、HTTP 原文/解析/提取副本、浏览器底层调用、
中间 flow 诊断和每条界面事件。失败的模型请求保留响应内容，便于检查错误原因。
界面的 NDJSON 事件协议与任务历史照常保留。

标题中的 `session_id`、`step_id` 和 `iteration` 对应任务、子步骤和执行轮次。
模型认证头不写入日志。路径不依赖启动目录或 Electron userData。
新任务自动生效，运行中的任务需要重新启动。日志目录已被 Git 忽略，旧日志不会自动删除。

在仓库根目录实时查看某次运行（替换文件名）：

```sh
tail -f app/python/log/agent-<UTC时间>-<PID>.log
```
