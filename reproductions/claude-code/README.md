# Claude Code Cleanroom 复现说明

关联资料:
- [Claude Code 学习文档](../../docs/claude-code/claude-code-study.md)
- [Claude Code 最小实现 Todo](../../docs/claude-code/claude-code-todo.md)

## 当前状态

截至 `2026-03-28`，当前 cleanroom 复现已经具备下面这些稳定基线：
- 真实 Responses API 驱动的最小 live 只读 agent 闭环。
- 统一 session 事件流，以及 `--continue-last` / `--session-id` 会话续跑。
- `CLAUDE.md`、用户规则、`MEMORY.md` 前 200 行的统一加载。
- “旧工具输出优先裁掉，再摘要更老会话”的最小 deterministic compaction。
- 基础 Web UI 工作台，用来查看 session、继续会话和检查本轮 `gather -> act -> verify` 摘要。

当前仍未完成的核心模块：
- permission gate
- checkpoint / undo
- live 模式下的写入型工具开放
- plan mode、subagent、hooks、文件协议化扩展层

## Phase 1 范围

当前复现只追求 Claude Code 的最小闭环，用来学习它的运行时结构，不追求下面这些内容:
- 完整 UI
- 完整插件市场
- 完整云端执行环境
- 浏览器远控或 hosted 运行面板
- 官方生态里的全部扩展协议一次性补齐

这一阶段只需要把“任务输入 -> 上下文收集 -> 工具执行 -> 结果验证 -> 会话记录”这条链路钉住。

这样收缩范围，是因为学习文档“9. 可复现的最小子集”和“9.1 第一阶段必须有”都把重点放在 agent runtime、工具调用、事件流、权限门和 checkpoint，而不是产品壳层。

更新:
- 现在额外提供了一个基础 Web UI 工作台，用来观察和驱动当前 cleanroom runtime。
- 这个 UI 仍然是最小前端壳层，不等于完整 Claude Code 产品界面。

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

目前已经把 `docs/claude-code/claude-code-todo.md` 的 `Phase 2` 前四点落成一个可运行骨架，并额外接上了真实 Responses API:
- 能接收用户任务文本
- 创建新的 `session id`
- 把 session 保存到项目内的 `.claude-code/sessions/`
- 支持通过 `--continue-last` 继续最近一次会话
- 支持通过 `--session-id` 读取指定会话
- 默认会使用官方 `openai` SDK 的 Responses API 跑最小多轮只读代理
- 保留 `--tool-direct` 调试入口，继续走显式工具命令
- 会把 `user_message`、`tool_call`、`tool_result`、`model_response` 四类最小事件落到 session JSON
- 当前会把 gather 摘要、模型模式、步数、工具执行情况、最终回答和 verify 结果打印到终端

当前的 cleanroom 取舍是:
- live 模式先只开放只读工具 `read_file`、`search`、`git_status`
- `edit` 和 `bash` 仍然只在 `--tool-direct` 下可用，避免在 permission gate 落地前直接开放写入和命令执行

### 当前主循环边界

当前实现的关键代码链是:

```text
CLI 参数 -> session store.events -> runtime.gather_context
-> context_builder.load_rules/build_prompt_context
-> live Responses agent / tool-direct planner
-> tool execution
-> session events / terminal summary
-> session JSON / 终端摘要输出
```

最小边界如下:
- `gather`: 从统一事件流里提取最近用户消息，并把最近事件折叠成 resume transcript
- `rules`: 启动时会从 workspace 向上查找 `CLAUDE.md`，读取用户级规则文件，并读取 workspace 下 `MEMORY.md` 的前 200 行
- `context builder`: 首轮 live 输入会统一拼接当前任务、规则文件、最近会话历史和最近工具输出
- `compaction`: 旧工具输出优先不再原样进入 transcript，更老的会话会收成一段 deterministic 摘要；最近少量工具结果仍单独保留原文
- `act/live`: 通过 Responses API 让模型决定是否调用只读工具，并在多轮 `function_call -> function_call_output` 后返回最终答案
- `act/tool-direct`: 把任务文本折叠成显式工具调用，作为 deterministic/debug 入口
- `emit events`: 把 live/tool-direct 产生的 `tool_call`、`tool_result`、`model_response` 追加回 session
- `verify`: 区分 `completed`、`api-error`、`invalid-tool-call`、`max-steps-reached`

这一步已经能看到真实模型效果，且已经补上最小 compaction；但还没有做 permission gate 和 checkpoint。

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
- live 模式会在 payload 里额外记录 `mode`、`model`、`finish_reason`、`step_index`、可选 `usage`。
- 旧版只包含 `user_tasks` 的 session 仍可读取，重新保存时会自动迁移成 `events` 结构。

### 配置与运行

当前推荐用 `uv` 管理这个复现目录自己的虚拟环境和依赖。

先安装依赖:

```bash
cd reproductions/claude-code
uv sync
```

`uv` 会在当前目录下创建并维护 `.venv/`。如果你只想先确认工具链是否可用，也可以执行:

```bash
cd reproductions/claude-code
uv run claude-code --help
uv run claude-code-web
```

然后配置 `reproductions/claude-code/.env`:

```bash
cp .env.example .env
```

`.env` 使用标准 OpenAI 风格变量:

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
OPENAI_MODEL=...
```

默认 live 模式会读取进程环境变量，若不存在再读取项目内 `.env`。

### Web UI

前端工作台放在 `reproductions/claude-code/ui/`，当前固定接入:
- live 只读工具链
- 历史 session 列表
- 继续已有会话
- 本轮 `gather -> act -> verify` 摘要和工具事件检查面板

不包含:
- `edit` / `bash` 写操作 UI
- 真流式 token 输出
- 文件树 / diff / IDE 编辑器

推荐直接用一键脚本同时启动前后端:

```bash
cd reproductions/claude-code
./scripts/dev_ui.sh
```

脚本会自动:
- 执行 `uv sync`
- 在缺少 `node_modules` 时执行 `npm install`
- 默认回收占用 `8000` 和 `5173` 的旧进程
- 启动 Python API
- 启动 Vite 前端开发服务
- 把日志写到 `reproductions/claude-code/.runtime/`

默认固定端口:

```bash
cd reproductions/claude-code
./scripts/dev_ui.sh
```

如果你不想让脚本自动关旧进程:

```bash
cd reproductions/claude-code
KILL_CONFLICTING_PORTS=0 ./scripts/dev_ui.sh
```

如果你想改端口，也可以覆盖:

```bash
cd reproductions/claude-code
BACKEND_PORT=8010 FRONTEND_PORT=5174 ./scripts/dev_ui.sh
```

如果你想分别启动，也可以继续用下面的手动方式。

先启动 Python API:

```bash
cd reproductions/claude-code
uv run claude-code-web
```

再单独启动前端开发服务:

```bash
cd reproductions/claude-code/ui
npm install
npm run dev
```

前端默认请求 `http://127.0.0.1:8000`。

如果你需要补充依赖，建议直接修改 `pyproject.toml` 后重新执行:

```bash
uv sync
```

#### Live 模式

在 `reproductions/claude-code/` 目录运行:

```bash
uv run claude-code "请解释 README.md 在这个复现里的作用"
uv run claude-code "搜索 SessionStore，然后总结它现在负责什么"
uv run claude-code --session-id <session-id>
uv run claude-code --continue-last "继续总结刚才的结果"
```

CLI 会输出当前状态、`session_id`、任务数量、事件数量和最近一次任务文本。
同时会输出本轮 runtime 的 `mode`、`model`、`step_count`、`executed_tools`、`finish_reason` 和最终回答。

#### Tool-direct 调试模式

如果要继续使用显式工具命令，传 `--tool-direct`:

```bash
uv run claude-code --tool-direct read_file README.md
uv run claude-code --tool-direct search SessionStore
uv run claude-code --tool-direct "edit notes.md -- before -- after"
uv run claude-code --tool-direct "bash python -m unittest"
uv run claude-code --tool-direct git_status
```

`--tool-direct` 仍支持下面这组显式任务格式:
- `read_file <path>`
- `search <query>`
- `edit <path> -- <old> -- <new>`
- `bash <command>`
- `git_status`

### 测试命令

```bash
cd reproductions/claude-code
uv run python -m unittest discover -s tests -p 'test_*.py'
```

前端测试:

```bash
cd reproductions/claude-code/ui
npm test
```

如果你临时不想走脚本入口，也可以继续用模块方式:

```bash
uv run python -m claude_code "搜索 SessionStore"
```

## 当前验证

这一步的验证标准是:
- 新建 session 时，JSON 里已经以 `events` 落盘，而不是只写 `user_tasks`
- 继续会话和只读加载会话时，仍能跑完一轮 live 或 tool-direct `gather -> act -> verify`
- `read_file`、`search`、`edit`、`bash`、`git_status` 都能在临时 workspace 里形成真实 `tool_call` / `tool_result`
- fake live client 能覆盖“直接回答”“search -> read_file -> final answer”“未知工具”“达到 max steps”这几类循环
- 旧版只含 `user_tasks` 的 session 能自动迁移成统一事件流

当前仍然保留几个明确边界:
- live 模式已经有 `CLAUDE.md` / `MEMORY.md` / 最小 compaction，但还没有更细的 relevance packing、缓存和策略切换。
- live 模式不开放 `edit` 和 `bash`。
- tool-direct 仍然是调试入口，不是最终的 Claude Code 风格交互。
