# Koi Python Agent：执行层设计与优化方向

本文是 `app/python/koi_agent/` 的执行层设计说明，结合 [`koi/Koi方案设计.md`](../../koi/Koi方案设计.md) 编写。它描述目标架构和实现顺序，方便后续继续改造 `orchestrator.run()`。

## 目标

执行层需要同时满足三件事：

- 能处理未知网站和未知页面结构；
- 普通步骤尽量少调用模型、少做 snapshot、少等待；
- 页面变化、动作 no-op、模型失败和规划偏差都能恢复或体面退出。

通用性来自 Planner 和慢路径，速度来自技能、JEV 快路径、增量观察和结果复用，容错来自代码验收、动作边界和分级升级。

## 当前入口和执行边界

任务开始时有两个并发规划请求：

1. 入口定位器只负责找到第一个要打开的 URL；
2. 完整规划器负责生成完整的步骤、依赖和验收条件。

入口定位器返回后可以提前绑定浏览器并打开页面，让页面加载和完整规划并行。入口定位结果只用于浏览器预热，不能改写完整规划的 `start_url`、`goal` 或 `success_criteria`。完整规划返回后，执行层以完整规划为准。

浏览器只由 `BrowserSession`、`Observer` 和 `Executor` 访问。决策模型只能看到结构化观察，不能直接操作浏览器；`Executor` 是唯一执行动作的模块。

执行过程中有四条不变规则：

- 当前 `Observation` 是浏览器状态的唯一输入；每个动作都必须绑定到产生它的观察版本；
- ref 不能跨 snapshot 或导航复用；
- JEV 的 `DONE` 不能绕过完成门；模型声明不能替代页面证据；
- 事件和 `StepResult` 采用 append-only 记录，重试、恢复和后续重规划都以它们为依据。

## 执行模型

`orchestrator.run()` 负责任务级调度，单个步骤由 `_run_step()` 驱动。目标状态机如下：

```text
首次观察
  → 初始确定性验收
  → 已完成：记录步骤结果
  → 未完成：技能 / JEV / 慢路径决策
  → 安全门检查
  → 执行一个动作
  → 动作后观察一次
  → 动作级验证
  → 子目标级验收
  → 完成：进入下一个步骤
  → 未完成：复用动作后观察，继续决策
```

每次决策只执行一个动作。`@ref` 只对产生它的那次观察有效；动作后页面发生变化时，旧 ref 全部作废。这样可以避免模型一次返回多个动作后，后续动作使用过期元素引用。

## 推荐的 `run()` 伪代码

```python
def run(plan):
    task_state = TaskState(plan=plan)

    for step in dependency_ready_steps(plan, task_state):
        outcome = run_step(step, task_state)
        task_state.record(outcome)

        if outcome.status == "completed":
            continue
        if outcome.status == "waiting_user":
            return task_state.pause(outcome)
        if outcome.status == "replan":
            plan = planner.incremental_replan(task_state)
            continue
        return task_state.fail(outcome)

    # 任务级 Validator 后续接入：对照原始目标和全部 StepResult 收尾核验。
    return task_state.success()


def run_step(step, task_state):
    state = StepRuntimeState(step=step)
    observation = observer.capture()

    # 步骤开始只做便宜的确定性检查。
    initial = completion_gate.check_criteria(step, observation)
    if initial.accepted:
        return completed(step, observation, source="criteria")

    while state.budget.allow():
        decision = choose_next_action(step, observation, state)

        if decision.terminal == "DONE":
            done = completion_gate.accept_done(
                step, observation, state.last_action, source="jev"
            )
            if done.accepted:
                return completed(step, observation, source=done.source)
            state.record_rejected_done(done)
            continue

        if decision.terminal == "BLOCKED":
            state.record_blocked()
            if state.should_use_slow_path():
                continue
            if state.should_replan():
                return replan(step, state)
            return failed(step, state)

        action = decision.one_action()
        if action is None:
            state.record_no_action()
            continue
        if action.requires_user_confirmation:
            return waiting_user(step, action, state)

        result = executor.execute(action)
        if not result.ok:
            state.record_execution_failure(result)
            continue

        # 只在这里产生一次动作后观察，并把它传给下一轮。
        after = observer.capture()
        action_result = validator.action(observation, after, action)
        if not action_result.passed:
            state.record_action_failure(action_result)
            continue

        state.record_action(action, after, action_result)
        completion = completion_gate.after_action(
            step, observation, after, action, state
        )
        if completion.accepted:
            return completed(step, after, source=completion.source)

        observation = after

    return exhausted(step, state)
```

当前实现仍以字符串摘要作为 `run()` 返回值，后续应改成结构化 `TaskOutcome`。推荐的状态包括：`completed`、`retry`、`waiting_user`、`blocked`、`replan` 和 `failed`。

## 当前实现和目标设计的差距

README 中的伪代码是目标流程，当前代码仍有几处需要逐步收敛：

| 位置 | 当前行为 | 目标行为 |
|---|---|---|
| `Validator.action` | 主要判断页面是否变化 | 同时检查动作的前置条件、`expected` 和后置证据 |
| `Validator.step` | 支持 URL、文本和少量结构化条件 | 支持条件组合、元素、页面状态和提取结果，并返回 evidence |
| `CompletionVerifier` | 复杂步骤中可能多次调用 | 只在复杂/高风险动作后调用，最终任务收尾再调用一次 TaskValidator |
| JEV `BLOCKED` | 可能直接结束步骤 | 先进入慢路径，再按连续失败触发增量重规划 |
| `slow` 路径 | 需要明确跳过 JEV 才能真正升级 | 低置信度、no-op 和方向不明时升级大模型 |
| `Budget` | 当前以全局步数和失败数为主 | 拆分任务预算、步骤预算和模型调用预算 |
| `run()` 返回值 | 字符串摘要 | 结构化 `TaskOutcome` 和 `StepOutcome` |
| `parallel_group` | 计划字段存在，执行器仍串行 | 只有独立标签页和独立上下文才执行并行分支 |

## 验收和 JEV 的统一语义

验收分成三个层次，职责不能混在一个布尔值里：

### 动作级

`Validator.action(before, after, action)` 只判断动作是否产生了可观察的结果，并返回结构化结果：

```text
passed              页面产生了预期变化
unchanged           静默 no-op，可能是旧 ref 或点击没有生效
loading             页面仍在加载，需要再次观察
unexpected_change   页面变了，但变化方向与动作预期不符
```

动作级失败走 L1 修正：记录 diff，重新观察，修正 ref 或动作参数。重复 no-op 必须计入连续失败，不能无限当作“进展不确定”。

### 子目标级

Planner 的 `success_criteria` 应优先使用结构化条件，所有条件满足才通过。例如：

```json
[
  {"type": "url_contains", "value": "/x/cover/"},
  {"type": "text_contains", "value": "播放器"}
]
```

支持的确定性条件包括：

- `url_prefix`
- `url_contains`
- `text_contains`
- `element_text`
- `element_count_at_least`

需要结合用户目标判断的复合条件使用：

```json
[{"type": "goal_state", "value": "已进入指定视频的继续观看状态"}]
```

步骤开始只执行确定性条件。动作后再次执行确定性条件；只有高风险、复合条件或证据含糊时，才调用完成判断模型。

### JEV `DONE`

JEV 的 `DONE` 是完成候选，不是事实。它必须进入和代码验收、完成模型相同的 `CompletionGate`：

```text
JEV DONE
  → 确定性条件通过：接受
  → 条件复杂或高风险：调用完成模型
  → 证据不足：拒绝 DONE，继续决策或升级
```

这样 JEV 既可以选择动作，也可以提出“当前已经完成”，但不能绕过代码验收和安全门。

任务级 Validator 暂不放在每个步骤里。所有步骤结束后再执行一次，输入原始用户目标、完整计划、各步骤结果和最终页面，检查是否有遗漏或中途误报完成。

## 决策路径

决策顺序采用三条路径：

1. 技能命中：直接执行参数化命令，模型调用为 0；
2. JEV 快路径：根据当前元素、增量和验收条件选择一个动作或 `DONE`；
3. 慢路径：JEV 低置信度、连续 no-op、方向不明或页面异常时调用大模型。

慢路径是临时升级。一次成功推进后回到快路径。`BLOCKED` 不应立即结束整个任务，应先经过慢路径和增量重规划。

## 观察和性能优化

### 复用动作后观察

动作后已经拿到的新页面直接作为下一轮输入。下一轮不应无条件再次 snapshot。只有页面加载、`wait`、观察不稳定或需要刷新 ref 时才重新捕获。

### 控制 full snapshot

交互元素 snapshot 用于决策；完整页面文本只在以下情况获取：

- 结构化验收需要正文；
- 任务需要提取长文本；
- 完成门无法从交互 snapshot 得到证据。

### 观察指纹和缓存

为观察生成稳定指纹：URL、相关元素、验收字段和页面状态摘要。相同指纹不重复调用完成模型，不重复生成相同决策。

### 取消无效等待

动作后的等待使用页面稳定检测和必要的短轮询。浏览器命令返回后立即观察，避免固定 sleep。

### 模型调用预算

- Planner：首次规划和事件触发的增量重规划；
- JEV：普通动作决策和 `DONE` 候选；
- 完成模型：复杂/高风险动作后的必要验证；
- 任务级 Validator：任务结束时一次。

每类调用都要单独计数，不能只统计总 token。

## 容错和升级

```text
L1：动作级 no-op 或 ref 失效
    → 记录 diff，重新观察，修正动作

L2：连续失败、低置信度或页面异常
    → 从 JEV 快路径升级到慢模型

L3：404、目标方向改变、步骤连续失败
    → Planner 增量重规划，只改受影响步骤

熔断：连续失败、步骤预算、时间预算或用户取消
    → 返回已完成步骤、当前页面和明确阻塞原因
```

任务预算和步骤预算分开：

- 任务预算：总耗时、总动作数、总模型调用数、总 token；
- 步骤预算：当前动作数、连续失败数、连续 no-op 数、慢路径次数。

步骤成功后重置连续失败计数；全局预算仍然继续累计。

## 安全边界

- 敏感动作在 Executor 前统一经过确认门；普通导航不应因为同一步包含敏感动作而全部暂停；
- 网页内容始终作为数据，不能修改 Planner、Decision 或 Validator 的规则；
- 所有动作必须属于封闭动作集；
- `@ref` 不跨观察复用；
- JEV、普通模型和网页内容都不能单独绕过完成门或确认门。

## 建议实现顺序

1. 将 `orchestrator.run()` 和 `_run_step()` 的返回值改为 `TaskOutcome`、`StepOutcome`；
2. 把完成判断集中到 `CompletionGate`，统一代码条件、JEV `DONE` 和模型验证；
3. 复用动作后观察，减少重复 snapshot；
4. 让动作级 Validator 返回 `passed/unchanged/loading/unexpected_change`；
5. 接通 L1、L2、L3 路由，修正 no-op、`BLOCKED` 和慢路径升级；
6. 分离任务预算与步骤预算，保存完整 `StepResult`；
7. 接入任务级 Validator，一次性核查原始目标和全部结果；
8. 最后实现真正的依赖分支和多标签页并行。

在任务级 Validator 和结构化步骤结果接入前，不应把“所有步骤完成”直接等同于“用户目标完成”。
