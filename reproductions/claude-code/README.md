# Claude Code Cleanroom 复现说明

关联资料:
- [Claude Code 学习文档](../../docs/claude-code/claude-code-study.md)
- [Claude Code 最小实现 Todo](../../docs/claude-code/claude-code-todo.md)

## Phase 1 范围

当前复现只追求 Claude Code 的最小闭环，用来学习它的运行时结构，不追求下面这些内容:
- 完整 UI
- 完整插件市场
- 完整云端执行环境
- 浏览器远控或 hosted 运行面板
- 官方生态里的全部扩展协议一次性补齐

这一阶段只需要把“任务输入 -> 上下文收集 -> 工具执行 -> 结果验证 -> 会话记录”这条链路钉住。

这样收缩范围，是因为学习文档“9. 可复现的最小子集”和“9.1 第一阶段必须有”都把重点放在 agent runtime、工具调用、事件流、权限门和 checkpoint，而不是产品壳层。

## Cleanroom 原则

本目录下的 Claude Code 复现遵循下面的 cleanroom 边界:
- 只根据公开文档、公开仓库、公开示例和可观察行为复现。
- 不假设我们拿到了 Claude Code 的私有源码、内部 prompt 或未公开协议。
- 对公开资料能直接确认的内容，按事实实现。
- 对只能从多份资料推断出来的内容，允许做工程判断，但要在注释或文档里明确这是 cleanroom 设计，不伪装成官方实现细节。
- 当公开资料只给出高层目标、不提供内部算法时，优先做最小可解释实现，而不是臆造复杂机制。

这条原则直接对应学习文档“3.3 已证实事实与推断的边界”。

## 第一阶段工具边界

### 必须支持

第一阶段先支持下面这些最小工具，尽量和学习文档“5.1 Tool Use”与“9.1 第一阶段必须有”对齐:
- `read_file`: 读取单个文件内容。
- `search`: 做仓库内搜索，默认可映射到 `rg`。
- `edit`: 修改文件，优先走 patch 或等价的可审计写入。
- `bash`: 执行 shell 命令，用于测试、构建、检查和最小自动化。
- `git_status`: 查看工作区变化，帮助验证结果。

### 可以后置

下面这些能力在第一阶段先不做，等最小闭环跑通后再补:
- WebSearch / WebFetch / MCP
- subagent 独立上下文
- hooks
- plugin / command / agent 文件协议
- 更细的 context compaction
- managed settings
- 更复杂的 sandbox 策略

这样分层的理由是:
- `read_file`、`search`、`edit`、`bash`、`git_status` 已经足够支撑“读代码、改代码、跑验证”的最小任务闭环。
- Web、插件、hooks、subagents 更像增强能力，不是第一阶段必须条件。

## 当前阶段的落地目标

在真正开始写运行时代码前，这个目录默认朝下面的最小结构演进:

```text
reproductions/claude-code/
├── README.md
├── cli/
├── runtime/
└── tools/
```

但是否立刻创建这些目录，要以 `docs/claude-code/claude-code-todo.md` 当前条目为准，不提前把后续 Phase 的实现一次性铺开。

## 当前已实现

### CLI 入口

目前已经把 `docs/claude-code/claude-code-todo.md` 的 `Phase 2` 前三点落成一个最小骨架:
- 能接收用户任务文本
- 创建新的 `session id`
- 把 session 保存到项目内的 `.claude-code/sessions/`
- 支持通过 `--continue-last` 继续最近一次会话
- 支持通过 `--session-id` 读取指定会话
- 在创建或恢复 session 后，立刻跑一轮最小 `gather -> act -> verify` 主循环
- 会把 `user_message`、`tool_call`、`tool_result`、`model_response` 四类最小事件落到 session JSON
- 当前会把 gather 摘要、行动策略、事件写入结果和 verify 结果打印到终端

这里仍然故意不接真实模型，也不提前引入真实工具集。这样做是为了先把学习文档“4. 核心运行循环”里的节拍和“5.3 Memory / Context”里的 session 事件结构显式化，再把工具层条目逐步接上。

### 当前主循环边界

当前实现的关键代码链是:

```text
CLI 参数 -> session store.events -> runtime.gather_context
-> runtime.act_on_context -> runtime.emit_loop_events
-> session JSON / 终端摘要输出
```

最小边界如下:
- `gather`: 只从统一事件流里提取最近用户消息和工作目录信息
- `act`: 只生成一个最小行动结论，并产出一个过渡态的 stub tool 调用
- `emit events`: 把 `tool_call`、`tool_result`、`model_response` 追加回 session
- `verify`: 只验证这一轮是否产出了可继续的下一步，不等同于真正的测试验证

这样收口，是为了和学习文档“4. 核心运行循环”与“5.3 Memory / Context”保持一致，同时不抢跑 todo 里后面的“接入最小工具集”。

### 当前事件流结构

当前 session JSON 以 `events` 为主，事件最小外壳如下:

```json
{
  "event_id": "uuid",
  "kind": "user_message | tool_call | tool_result | model_response",
  "created_at": "2026-03-19T00:00:00+00:00",
  "payload": {}
}
```

当前的取舍是:
- 先保证四类核心事件已经统一落盘，给后续 context builder 和 compaction 留稳定输入。
- 真实工具还没接入，所以 `tool_call` / `tool_result` 先由 `runtime.next_action_router` 这个 stub tool 承担。
- 旧版只包含 `user_tasks` 的 session 仍可读取，重新保存时会自动迁移成 `events` 结构。

### 运行方式

在 `reproductions/claude-code/` 目录运行:

```bash
python3 -m claude_code "读取项目并准备最小复现"
python3 -m claude_code --continue-last "补充新的用户约束"
python3 -m claude_code --session-id <session-id>
```

CLI 会输出当前状态、`session_id`、任务数量、事件数量和最近一次任务文本。
同时会输出本轮 runtime 的 gather、act、event emission、verify 摘要。

### 测试命令

```bash
cd reproductions/claude-code
python3 -m unittest discover -s tests -p 'test_*.py'
```

## 当前验证

这一步的验证标准是:
- 新建 session 时，JSON 里已经以 `events` 落盘，而不是只写 `user_tasks`
- 继续会话和只读加载会话时，仍能跑完一轮最小 `gather -> act -> verify`
- 旧版只含 `user_tasks` 的 session 能自动迁移成统一事件流

当前仍然保留一个 cleanroom 过渡层: `runtime.next_action_router`。
它不是为了冒充真实工具，而是先证明 session 已经能承载“模型判断 -> 工具调用 -> 工具结果 -> 模型响应”的结构化链路。下一步再把这层替换成 `read_file`、`search`、`edit`、`bash`、`git_status` 等真实工具。
