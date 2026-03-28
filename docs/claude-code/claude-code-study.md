# Claude Code：实现原理、外部机制与 cleanroom 复现路线

## TL;DR
- Claude Code 更像一个 `agentic harness`，而不是单纯的“聊天 + 写代码”界面：模型负责决策，工具负责行动，产品负责把上下文、权限、会话、沙箱和扩展点粘合成一个循环系统。
- 官方公开资料能直接验证的主要是“外部行为与扩展表面”，例如主循环、权限模式、session/compaction、`CLAUDE.md` / memory、hooks、subagents、plugins、settings hierarchy；核心内核源码并未完整开源。
- `anthropics/claude-code` 官方仓库公开的是插件、agent、skill、hook、settings 示例和发布资产，不是 Claude Code CLI 的完整核心实现；复现时应按 cleanroom 思路自己搭 agent harness。
- 从官方文档与第三方 cleanroom 分析交叉看，Claude Code 的关键工程价值不在某一个 prompt，而在“任务循环 + 多层上下文 + 权限/沙箱 + 可扩展代理生态”的整体编排。
- 第一次复现不必追求完整功能。最小可行版本只需要：CLI 主循环、少量核心工具、事件流 session、文件 checkpoint、`CLAUDE.md`/`MEMORY.md`、简单 compaction、可选 subagent。
- 对内部 prompt、检索策略、压缩算法、工具选择启发式等，第三方文章给出了高质量推断，但官方没有公开足够细节，复现时应把它们当“可替换策略模块”，不要当成已知事实硬编码。

## 当前仓库复现进展（截至 2026-03-28）
- 当前 cleanroom 复现已经跑通最小 live 只读闭环：真实 Responses API、多轮工具调用、统一 session 事件流、CLI 会话续跑，以及基础 Web UI 工作台。实现入口见 [reproductions/claude-code/README.md](../../reproductions/claude-code/README.md)。
- 上下文层已经补到可用状态：会加载项目 `CLAUDE.md`、用户级规则、`MEMORY.md` 前 200 行，并把最近 transcript、最近工具输出和压缩后的旧会话摘要一起打包进首轮输入。实现见 [reproductions/claude-code/claude_code/context_builder.py](../../reproductions/claude-code/claude_code/context_builder.py)。
- compaction 已落实成一个 deterministic 策略：先裁掉较老工具输出，再对更老会话做摘要；该行为已经有针对性的 live runtime 测试覆盖。验证见 [reproductions/claude-code/tests/test_runtime_live.py](../../reproductions/claude-code/tests/test_runtime_live.py)。
- 当前仍未落地的关键模块是 permission gate、checkpoint、live 模式写入工具、plan mode、subagent、hooks 和文件协议化扩展层；因此它已经是一个可信的最小学习样机，但还不是接近 Claude Code 交互质感的完整复现。

## 1. 本文涉及资料范围
- 研究对象: Claude Code 的工作机制、扩展模型和最小 cleanroom 复现路径。
- 用户提供资料:
  - 官方文档: https://code.claude.com/docs/en/how-claude-code-works
  - 第三方分析: https://southbridge-research.notion.site/claude-code-an-agentic-cleanroom-analysis
  - 官方仓库: https://github.com/anthropics/claude-code
  - 中文镜像文档: https://claudecn.com/docs/claude-code/
- 本文覆盖维度: 背景与目标、总体架构、核心运行循环、tool use、planning、memory/context、prompting/policies、safety、工程边界、局限与最小复现路径。
- 未覆盖或证据不足的部分:
  - Claude Code CLI 内核源码与内部系统 prompt 全文
  - 内部检索/重排/压缩算法的精确实现
  - 官方统一 benchmark、量化 eval 和失败率数据

## 2. 背景与目标
Claude Code 试图解决的不是“如何让模型写一段代码”，而是“如何让模型在真实工程环境里持续完成任务”。官方把它定义为一个运行在终端中的 agentic coding 工具，能够理解代码库、调用本地环境、执行命令、验证结果，并在必要时反复迭代，直到任务完成或被用户中断。[官方 how it works](https://code.claude.com/docs/en/how-claude-code-works)

从产品边界看，Claude Code 解决的是三类问题：
- 把模型的推理和终端/文件系统/搜索/网络等工具连接起来。
- 把一次性对话变成可恢复、可压缩、可授权、可审计的长任务会话。
- 把“个人 prompt 工程”上升为团队可复用的 commands、agents、skills、hooks、plugins 和 managed settings。

这也是它与普通 IDE chat 的主要差异：Claude Code 的中心是“在权限边界内持续行动”。

## 3. 总体架构

### 3.1 官方可直接确认的结构设计
官方文档把 Claude Code 拆成两个核心组件：
- Models：负责理解目标、规划下一步、选择工具、综合观察结果。
- Tools：负责行动与观测，包括文件读取/写入、搜索、命令执行、Web 查询、以及通过插件扩展的能力。[官方 how it works](https://code.claude.com/docs/en/how-claude-code-works)

围绕模型与工具，该产品又加上了四层基础设施：
- Context layer：对话历史、文件内容、命令输出、`CLAUDE.md`、memory、skills、系统指令等组成可供模型消费的上下文窗口。
- Execution layer：Local、本地受控的 Remote Control、以及 Anthropic 托管的 Cloud 环境。
- Control layer：permissions、sandbox、checkpoints、settings hierarchy、hooks。
- Extension layer：plugins、slash commands、subagents、skills、MCP servers、custom hooks。

可以把它抽象成下面这张 cleanroom 结构：

- User Task
  - Session / Event Log
    - Context Builder
      - conversation history
      - CLAUDE.md + MEMORY.md
      - selected files / command outputs
      - loaded skills / subagent summaries
    - Model
      - plan / decide / choose tool
    - Tool Runtime
      - read / write / search / bash / web / git
      - hooks
      - permission gate
      - sandbox / checkpoint
    - Observation
    - verify / compact / continue / stop

### 3.2 公开仓库暴露出的“外部接口”
`anthropics/claude-code` 仓库很容易被误读成“Claude Code 源码仓库”，但实际公开内容更像“官方插件与工作流资产仓库”。仓库根目录公开的是：
- `plugins/`: 官方插件示例，包含 commands、agents、skills、hooks、`.mcp.json` 等。
- `examples/`: hooks 和 settings 示例。
- `README.md` / `CHANGELOG.md`: 安装、定位和发布记录。

这说明 Anthropic 至少把 Claude Code 设计成“核心执行器 + 文件化扩展协议”的体系：很多工作流能力不是硬编码在二进制里，而是通过 markdown frontmatter、JSON 配置和目录约定注入的。[官方仓库 README](https://github.com/anthropics/claude-code/blob/main/README.md) [官方 plugins 文档](https://docs.claude.com/en/docs/claude-code/plugins)

### 3.3 已证实事实与工程推断的边界
已证实事实：
- Claude Code 有多种 execution environment，支持本地、云端和浏览器远控。
- 它有 settings、hooks、subagents、plugins、skills、memory 等正式机制。
- subagent 可以有自己的系统 prompt、工具白名单和独立上下文窗口。

工程推断：
- Claude Code 的内核大概率是一个围绕工具调用和事件流状态机组织的 agent runtime；这与官方公开的 `gather -> act -> verify` 行为边界一致，也与当前 cleanroom 复现采用的组织方式一致。
- prompt 和上下文编排大概率比公开文档描述的更复杂，但无法从公开资料精确还原；复现时更稳妥的做法是实现“行为等价的上下文层”，而不是假定官方内部 prompt 结构。

## 4. 核心运行循环
官方对运行循环的描述非常明确：`gather context -> take action -> verify results`，并可重复多轮直到完成。[官方 how it works](https://code.claude.com/docs/en/how-claude-code-works)

### 4.1 一个最小闭环
以“修复 failing tests”为例，官方给出的过程是：
1. 收集上下文：读取项目文件、命令输出、测试失败信息、项目指令。
2. 采取行动：运行测试、读取相关文件、改代码、再次执行命令。
3. 验证结果：重新跑测试或检查 git diff，确认目标是否完成。

这已经足够指导 cleanroom 复现：主循环本质上不是“一次大 prompt”，而是一串交替出现的“模型决策”和“工具执行事件”。

### 4.2 用户交互如何插入循环
用户可以在循环中途打断、追加约束、改方向。官方还提供了：
- `Plan mode`: 仅生成计划，不执行写入或危险操作，等用户批准后再进入执行。
- `Auto-accept edits`: 编辑文件可自动放行，但执行命令仍需审批。
- session resume / continue / fork: 在历史基础上继续或分叉尝试不同路径。

这意味着 Claude Code 不是纯自动代理，而是默认把“人类审批”当成主循环的一部分。

## 5. 关键机制

### 5.1 Tool Use
官方 how-works 页面把工具大致分为：
- 文件操作：读写文件、目录检查。
- 搜索：代码库搜索、文件定位。
- 执行：shell、git、测试、服务启动。
- Web：搜索或抓取外部信息。
- Code intelligence：在某些环境下通过插件或 IDE 集成提供。

从官方 hooks 和 settings 示例可进一步确认两件关键事实：
- Bash 是受专门权限和 sandbox 设置管理的高风险工具。
- hooks 可以在工具调用前后对工具进行观察、变换或拦截。

例如官方 `bash_command_validator_example.py` 展示了一个 `PreToolUse` hook：Claude Code 会把工具名和工具输入以 JSON 形式传给外部脚本；脚本若返回特定退出码，就能阻止这次 Bash 调用。[官方 hooks 文档](https://code.claude.com/docs/en/hooks) [官方 hooks 示例](https://github.com/anthropics/claude-code/blob/main/examples/hooks/bash_command_validator_example.py)

这对复现非常重要，因为它说明 Claude Code 的工具运行时至少满足：
- 工具调用是结构化事件，不只是“把 shell 命令直接拼进提示词”。
- 工具层支持前置和后置策略插桩。
- 策略结果可以反馈给模型，形成下一轮决策条件。

### 5.2 Planning
官方文档公开了三类 planning 线索：
- 主循环本身包含“先收集上下文、再行动、再验证”的隐式计划框架。
- `Plan mode` 让模型先产生只读计划，再由用户批准执行。
- subagents 可并行处理不同子问题，并把摘要回传给主上下文。

官方没有公开更细的规划算法，但公开仓库中的插件样例能看到 Anthropic 团队自己是如何把 planning 外显的。比如 `plugins/feature-dev/commands/feature-dev.md` 把一个复杂开发任务拆成 discovery、exploration、clarifying questions、architecture design、implementation、quality review、summary 七个阶段，并明确要求“并行启动 2-3 个 explorer/architect/reviewer agents”。[feature-dev command](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/commands/feature-dev.md)

这至少说明：
- Claude Code 的 planning 不一定只依赖模型内部链路，也可以被文件化 workflow 显式约束。
- 官方推荐的复杂任务范式，是“主 agent 编排 + 子 agent 分工 + 用户在关键阶段审批”。

### 5.3 Memory / Context
这是 Claude Code 在工程上最值得复现的一层，因为它直接决定长任务是否能在有限窗口里继续稳定推进。

官方 how-works 页面明确指出，上下文窗口会包含：
- 对话历史
- 文件内容
- 命令输出
- `CLAUDE.md`
- skills
- 系统提示与其他辅助上下文

接近窗口上限时会发生 compaction：
- 先优先移除较老的工具输出
- 再在必要时对旧对话做摘要
- 用户可用 `/context` 查看占用，用 `/compact` 指定压缩关注点

官方 memory 文档进一步给出了持久上下文层级：
- project memory: 从当前目录递归向上读取 `CLAUDE.md`
- user memory: `~/.claude/CLAUDE.md`
- local project memory: `CLAUDE.local.md`
- Auto memory: `MEMORY.md`

其中 project memory 会自动导入 user memory；会话开始时，Claude Code 还会加载 `MEMORY.md` 的前 200 行。[官方 how it works](https://code.claude.com/docs/en/how-claude-code-works) [官方 memory 文档](https://code.claude.com/docs/en/memory)

对复现者来说，最重要的不是“完全照搬文件名”，而是理解这里有三层不同职责：
- 持久规则层：像 `CLAUDE.md` 这种长期项目约定。
- 会话事件层：本次对话、工具结果和中间产物。
- 压缩摘要层：为长任务保留最小必要状态。

第三方 cleanroom 分析把这层进一步解释为“prompt cache + context packing + query-based pruning”的组合，并指出 Claude Code 会把近期会话、`CLAUDE.md`、相关文件和工具结果拼装成一个动态上下文包。这个方向与官方公开行为是一致的，但具体打包算法没有被官方证实，因此更适合作为复现时的设计启发，而不是官方实现描述。[Southbridge 分析](https://southbridge.ai/claude-code-an-agentic-cleanroom-analysis)

### 5.4 Prompting / Policies
Claude Code 的系统 prompt 本身没有公开，但公开资料足够说明“策略层”不是单一 prompt，而是多层叠加：
- 内置系统行为：产品级默认策略，外界不可见。
- 项目级规则：`CLAUDE.md`、`CLAUDE.local.md`、`MEMORY.md`。
- 工作流级规则：slash commands、skills、subagents 的 markdown frontmatter 和正文。
- 运行时策略：hooks、managed settings、permission rules。

官方仓库中的 agent 文件是一个非常直观的证据。例如 `plugins/feature-dev/agents/code-explorer.md` 用 YAML frontmatter 定义了 `name`、`description`、`tools`、`model`、`color`，正文则是该 agent 的角色说明与输出规范。[code-explorer agent](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/agents/code-explorer.md)

这说明 Claude Code 的不少“智能体行为”其实是文件化的、可版本化的策略资产，而不是藏在程序内部。

### 5.5 Safety / Boundaries
Claude Code 的安全边界主要由四类机制组成：

1. permissions
- Default: 编辑和执行都需确认。
- Auto-accept edits: 编辑自动放行，命令仍需确认。
- Plan mode: 只做分析与计划，不执行修改。

2. checkpoints
- 在关键修改前保存可恢复状态，便于撤销。

3. sandbox
- 官方 settings 示例明确说明 `sandbox` 主要作用于 Bash 工具，而不是所有工具。
- 可配置网络访问、Unix socket、本地绑定、是否允许未沙箱命令等。

4. managed settings / hooks
- settings hierarchy 覆盖 enterprise policy、command-line、project、local 等多层。
- hooks 可限制或审核工具调用。

这里显而易见：Claude Code 的安全模型不是只靠强沙箱，也不是只靠人工审批，而是“审批 + Bash 沙箱 + hooks + setting policy + checkpoint”多层组合。[官方 settings 文档](https://code.claude.com/docs/en/settings) [官方 hooks 文档](https://code.claude.com/docs/en/hooks)

## 6. 实现与工程细节

### 6.1 执行环境
官方 how-works 页面列出了三种执行环境：
- Local
- Cloud
- Remote Control

这说明 Claude Code 的核心逻辑不依赖单一宿主形式。换句话说，真正稳定的抽象边界应该是：
- 任务如何进入 session
- 上下文如何构建
- 工具如何被调用和授权
- 结果如何压缩并写回 session

而不是“终端 UI 长什么样”。

### 6.2 插件系统说明了什么
官方插件文档把插件结构描述为一个目录协议，常见组件包括：
- slash commands
- hooks
- subagents
- MCP servers
- settings
- status line components
- output styles

插件目录下的 `.claude-plugin/plugin.json` 提供元数据，commands/agents/skills/hooks 等目录再放具体能力定义。[官方 plugins 文档](https://docs.claude.com/en/docs/claude-code/plugins) [feature-dev plugin.json](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/.claude-plugin/plugin.json)

这对 cleanroom 复现的启发是：
- 不要把所有工作流写死在代码里。
- 把 commands、agent roles、hook policies 和 settings 设计成文件协议，更容易达到 Claude Code 那种“产品内核小、工作流资产可外部演化”的结构。

### 6.3 Subagents 是独立上下文，不是同一窗口分身
官方 subagents 文档明确写到：
- 每个 subagent 都有自己的 system prompt、description 和 tools。
- 它们拥有独立上下文窗口。
- 使用 subagent 的目的之一是避免主上下文膨胀，并让专门 agent 处理专门任务。

这意味着复现 subagent 时，重点不是“并发”本身，而是：
- 独立 context
- 受限 toolset
- 明确结果摘要接口

如果只是在同一上下文里插入另一个 prompt，并不能得到同等的工程效果。

### 6.4 Hooks 的真实价值
很多人会把 Claude Code 理解成“模型 + 工具调用”，但 hooks 其实说明它已经接近一个“可编排的代理运行时”：
- `PreToolUse` 可以做策略拦截和改写。
- `PostToolUse` 可以做日志、校验、派生行为。
- `SessionStart` / `Stop` 可以在会话生命周期做初始化与收尾。

这和传统 IDE assistant 的差别很大。它意味着 Claude Code 允许团队把组织规则、审计规则、安全规则编码成外部程序。

## 7. 第三方分析：哪些值得信，哪些存疑

### 7.1 与官方材料高度一致的部分
Southbridge 的 cleanroom 分析有几条判断与官方资料高度一致：
- Claude Code 的核心是 agentic harness，而不是单轮问答。
- 真实任务依赖上下文打包、工具调用和结果回灌。
- `CLAUDE.md`、最近会话、项目文件和工具结果会共同构成模型输入。
- subagents 和工具编排是它扩大任务规模的重要手段。

这些结论即使不依赖第三方文章，也能从官方文档得到支持。

### 7.2 高可能性但仍属推断的部分
Southbridge 文章提出了若干很有启发的实现假设：
- 存在 prompt caching 以降低长任务成本。
- 存在 query-based file pruning 或 relevance packing，避免把整个仓库塞进窗口。
- 会对旧对话和工具输出进行多层摘要。
- 工具选择和搜索流程中存在产品级启发式，而不是把“先 grep 再 read 再 edit”完全交给裸模型。

这些推断非常适合指导复现，因为它们告诉我们“应该把上下文管理和检索当成一等模块”。但官方没有公开足以逐项证实的细节，所以实现时最好：
- 先做行为等价，不追求内部同构。
- 把缓存、剪枝、摘要、排序设计成可替换策略。

### 7.3 纯粹靠猜的的部分
下列内容目前不应写成“Claude Code 就是这样实现的”：
- 系统 prompt 的精确结构
- prompt cache 的命中条件和分层策略
- 检索排序器的具体特征工程
- 压缩摘要的 token 配额与算法细节

更合理的表述方式是：“基于第三方 cleanroom 分析，这些机制大概率存在，且与官方公开行为一致，但具体实现未知。”

## 8. 评测、效果与局限

### 8.1 公开评测信息有限
在这次研究使用的资料里，官方没有提供一份系统的 Claude Code benchmark/eval 报告。公开文档更偏产品机制、配置和使用方法，仓库更偏插件生态和发布资产。

这带来两个后果：
- 学习 Claude Code 时，重点应放在“架构与行为机制”，而不是性能数字。
- 复现时更适合做任务级 smoke tests，而不是试图对齐不存在的官方基准。

### 8.2 已知局限
- 官方未开源完整内核，无法逐模块对照实现。
- 文档公开的是行为边界，不是完整设计文档。
- 产品持续快速演化，文档和发布说明可能不同步。以 2026-03-17 的最新 release `v2.1.77` 为例，仍在持续修复 subagent resume、compaction、sandbox 和 permissions 相关问题，说明这些模块仍在活跃迭代。[GitHub release v2.1.77](https://github.com/anthropics/claude-code/releases/tag/v2.1.77)
- 第三方 cleanroom 分析虽然有价值，但天然无法替代核心源码。

### 8.3 与当前仓库实现的对照
- 已落地: CLI 会话创建与续跑、统一事件流、真实 Responses API 驱动的只读 live agent、`CLAUDE.md` / 用户规则 / `MEMORY.md` 加载、最小 compaction、基础 Web UI。
- 已验证: live 模式可以覆盖“直接回答”“search -> read_file -> final answer”“未知工具”“达到 max steps”“规则/历史/工具输出进入初始输入”“旧工具输出优先被压缩”这些关键路径。
- 尚未落地: permission gate、checkpoint、live 模式写入工具、plan mode、subagent、hooks、plugin / command / agent 文件协议。
- 因此，当前仓库更适合回答“Claude Code 的最小运行时边界是什么”，还不适合回答“Claude Code 的完整交互质感如何复现”。

## 9. 可复现的最小子集
如果目标是“学习 Claude Code 的实现原理并复现一个最小版本”，建议只做下面这几个模块。

### 9.1 第一阶段必须有
1. CLI 主循环  
    - 输入用户任务
    - 调模型决定下一步
    - 执行工具
    - 把工具输出回灌给模型
    - 判断继续/完成/请求审批

2. 最小工具集
    - `read_file`
    - `search`（例如 `rg`）
    - `write_file` / patch
    - `bash`
    - `git_status` 或 diff

3. Session 事件流
    - 记录用户消息、模型响应、工具调用、工具结果
    - 支持 resume / fork

4. 权限门
    - 命令执行是否需要确认
    - 文件写入是否需要确认
    - 简单 allowlist / denylist

5. Checkpoint
    - 写文件前保存原始内容
    - 支持撤销最近一次批量修改

6. `CLAUDE.md` + `MEMORY.md`
    - 启动时加载项目规则与用户规则
    - 长任务时保留最小摘要

### 9.2 第二阶段再补
  - context compaction
  - subagent 独立上下文
  - hooks
  - plugin/command/agent 文件协议
  - WebSearch / WebFetch / MCP
  - 更细的 sandbox 和 managed settings

### 9.3 一个推荐的 cleanroom 架构
可以按下面的目录切分：

```text
reproductions/claude-code-cleanroom/
├── cli/                 # 命令入口与 session 控制
├── runtime/
│   ├── loop.py          # gather -> act -> verify
│   ├── context.py       # 上下文构建与压缩
│   ├── permissions.py   # 审批与规则
│   ├── checkpoints.py   # 文件快照与撤销
│   └── events.py        # session 事件流
├── tools/
│   ├── read.py
│   ├── search.py
│   ├── edit.py
│   ├── bash.py
│   └── git.py
├── policies/
│   ├── claude_md.py
│   ├── memory.py
│   └── hooks.py
└── plugins/             # 后续再做 commands/agents/skills 协议
```

这套划分不保证与 Anthropic 内部实现相同，但它能覆盖公开资料暴露出的主要系统边界，是一种合理的 cleanroom 起点。

## 10. 先复现什么
如果你准备真正动手，建议优先复现下面三件事：
- `事件流主循环`：先把 gather -> act -> verify 跑通，哪怕只有 `read/search/bash/edit` 四个工具。
- `上下文层级`：让 `CLAUDE.md`、会话历史和最近工具输出一起进入 prompt；再补最小 compaction。
- `权限与可撤销`：把“会行动”和“敢行动”分开。没有 checkpoint 和 permission gate，就很难复现 Claude Code 的真实交互质感。

等这三件事成立后，再加 subagent、hooks、插件协议，系统就会开始接近 Claude Code 的味道。

## 11. 结论
- Claude Code 最值得学的不是某个神秘 prompt，而是“模型、工具、上下文、权限和扩展点”如何被编排成一个可持续任务系统。
- 官方仓库真正公开的是扩展生态和文件协议，这反而给复现提供了很好的外部接口样本。
- subagent、memory、compaction 和 checkpoint 是它区别于普通代码助手的关键工程点。
- 第三方 cleanroom 分析的价值主要在于指出“内部很可能存在缓存、剪枝和上下文打包策略”，但这些应该被实现成可替换模块，而不是硬编码假设。
- 最适合学习和复现的路径，是从行为等价入手，而不是从猜测 Anthropic 内核源码入手。

## Sources
- [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works) - 访问于 2026-03-17 - 官方工作机制总览，提供主循环、执行环境、权限模式、session 和 compaction 线索。
- [Settings](https://code.claude.com/docs/en/settings) - 访问于 2026-03-17 - 官方 settings hierarchy、权限规则和 sandbox 范围说明。
- [Memory](https://code.claude.com/docs/en/memory) - 访问于 2026-03-17 - 官方持久上下文层级、`CLAUDE.md` / `MEMORY.md` 说明。
- [Hooks](https://code.claude.com/docs/en/hooks) - 访问于 2026-03-17 - 官方 hook 生命周期、JSON 输入输出与拦截机制。
- [Subagents](https://code.claude.com/docs/en/subagents) - 访问于 2026-03-17 - 官方 subagent 定义、独立上下文与工具限制。
- [Claude Code plugins](https://docs.claude.com/en/docs/claude-code/plugins) - 访问于 2026-03-17 - 官方插件结构、组件类型与安装方式。
- [anthropics/claude-code README](https://github.com/anthropics/claude-code/blob/main/README.md) - 访问于 2026-03-17 - 官方仓库定位、安装与文档入口。
- [anthropics/claude-code plugins README](https://github.com/anthropics/claude-code/blob/main/plugins/README.md) - 访问于 2026-03-17 - 官方插件生态概览，展示 commands/agents/skills/hooks/MCP 的外部协议。
- [feature-dev command](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/commands/feature-dev.md) - 访问于 2026-03-17 - 官方工作流样例，展示复杂 planning 如何文件化。
- [code-explorer agent](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/agents/code-explorer.md) - 访问于 2026-03-17 - 官方 subagent frontmatter 样例，说明 agent 是 markdown 策略资产。
- [feature-dev plugin metadata](https://github.com/anthropics/claude-code/blob/main/plugins/feature-dev/.claude-plugin/plugin.json) - 访问于 2026-03-17 - 官方插件元数据样例。
- [bash command validator example](https://github.com/anthropics/claude-code/blob/main/examples/hooks/bash_command_validator_example.py) - 访问于 2026-03-17 - 官方 hook 示例，展示工具调用如何被外部策略脚本拦截。
- [Claude Code: An Agentic Cleanroom Analysis](https://southbridge.ai/claude-code-an-agentic-cleanroom-analysis) - 2025-05-26 - 高质量第三方 cleanroom 分析，用于补充上下文打包、缓存与实现推断。
- [Claude Code 中文文档镜像](https://claudecn.com/docs/claude-code/) - 访问于 2026-03-17 - 中文镜像，用于辅助交叉阅读，不作为核心事实唯一来源。
- [Claude Code release v2.1.77](https://github.com/anthropics/claude-code/releases/tag/v2.1.77) - 2026-03-17 - 用于确认产品仍在高频演化，特别是 permissions、sandbox、resume、compaction 等机制持续变动。
