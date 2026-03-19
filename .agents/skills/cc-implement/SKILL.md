---
name: cc-implement
description: Implement one specified item from docs/claude-code/claude-code-todo.md for the Claude Code reproduction in this repository. Use when the user asks to 实现 claude-code-todo.md 某个 Phase/第几点, continue the Claude Code reproduction, or wants repository-consistent Chinese docs/code/comments tied back to docs/claude-code/claude-code-study.md. Do not use for unrelated research-only requests.
---

# CC Implement

把用户指定的 `docs/claude-code/claude-code-todo.md` 条目落实为当前仓库里的文档、代码和最小验证结果。

默认假设用户会这样提需求:
- `请实现 claude-code-todo.md Phase X 第 X 点`
- `实现 Claude Code Todo 的 Phase X 第 X 点`

## Required Context

开始动手前，必须先读:
- `docs/claude-code/claude-code-todo.md`
- `docs/claude-code/claude-code-study.md`

如果用户已经明确给出 `Phase X 第 X 点`，直接以 todo 文件中的该条目为准，不要求用户重复粘贴需求。

## Workflow

1. 锁定条目
- 从用户请求中解析 `Phase` 和条目序号。
- 若用户表述略有偏差，以 todo 文件内最接近的条目为准，并在最终说明里写清楚你实际实现的是哪一条。

2. 回看学习文档
- 找到该条目在 `claude-code-study.md` 中引用的相关章节。
- 实现时保持和学习文档结论一致，尤其注意:
  - 最小闭环优先，不抢跑后续 Phase
  - cleanroom 边界
  - 文件协议化、可替换策略、最小可验证实现

3. 判断产物类型
- 如果条目主要是在澄清范围、列边界、补规则、补说明、补验证记录，优先写或改文档。
- 如果条目要求可运行能力、CLI、循环、工具、上下文、权限、checkpoint 等，优先写代码。
- 如果代码实现需要补充说明才能自洽，允许同时补少量文档，但不要把实现任务改写成纯文档任务。

4. 选目标路径
- Claude Code 复现代码默认放在 `reproductions/claude-code/`。
- Claude Code 学习和说明默认放在 `docs/claude-code/`。
- 新增测试或验证脚本时，优先放在 `reproductions/claude-code/` 附近，保持目录自包含。

5. 完成实现
- 直接动手，不先输出长篇方案。
- 保持改动最小、闭环完整、便于后续 Phase 继续迭代。
- 不主动实现当前条目之外的大块增强能力。

6. 做最小验证
- 有代码时，运行一个最小可执行验证命令。
- 没有现成测试框架时，至少运行能证明入口或核心行为工作的命令。
- 如果因为环境限制无法验证，要明确写出缺口。

## Writing Rules

### 文档任务

- 用中文写。
- 风格保持简洁直接，优先用短标题和短段落。
- 优先沿用当前仓库文档语气，不写成宣传稿。
- 建议结构按需裁剪:
  - 这条要解决什么
  - 为什么现在这样做
  - 最小实现边界
  - 和学习文档哪几节相关
  - 怎么验证或后续怎么接着做

### 代码任务

- 默认用中文注释，并把“帮助后来者读懂代码链”当成交付物的一部分，不把注释当成可有可无的润色。
- 不要求逐行解释，但关键代码链必须能让刚进入仓库的人顺着注释读下去。
- 注释要偏教学向，优先解释:
  - 这段代码在整条链路里负责什么
  - 为什么这样设计，而不是别的更复杂写法
  - 它对应学习文档的哪几节结论
  - 当前实现为什么只做到这个边界，下一步会往哪扩
- 避免无信息量注释，例如“给变量赋值”或简单复述代码字面意思。
- 保持同一文件里的注释口吻统一、简短、可读。

代码任务至少覆盖下面三类注释:
- 文件级入口注释：说明这个文件在 Claude Code cleanroom 复现中的角色，必要时点明对应的 todo 条目和学习文档章节。
- 关键函数注释：主流程函数、状态转换函数、持久化函数、边界判断函数前，应说明输入、输出和它在代码链中的位置。
- 边界注释：当实现是“先做最小版本”“暂时不用更复杂方案”时，要写清当前取舍和后续扩展点。

如果一次实现跨多个文件，优先把“关键代码链”讲清楚:
- 在关键入口函数或连接点旁边，用中文注释标出链路，例如“CLI 参数 -> session store -> 后续 runtime”。
- 如果链路跨文件明显，允许在 README 或相关文档里补一个很短的“关键代码链”小节，用 `A -> B -> C` 的形式说明。
- 注释或文档里尽量直接写学习文档章节号，例如“对应《claude-code-study.md》的 4. 核心运行循环”和“9.1 第一阶段必须有”，不要只写模糊的“见 study 文档”。

适合重点写中文注释的地方:
- CLI 入口和参数解析
- 主循环和阶段切分
- context builder / compaction / event schema
- session 持久化和 resume / fork 相关逻辑
- permission gate / checkpoint / hooks 边界
- 和学习文档章节一一对应的关键结构

## Implementation Heuristics

- 如果 `reproductions/claude-code/` 还是空目录，允许先搭最小骨架，但只补当前条目真正需要的文件。
- 如果条目偏“先确认边界”，实现可以是文档、配置、README、验证清单，而不一定是代码。
- 如果条目偏“最小架构”，优先选择最小而清晰的模块切分，不要一开始过度抽象。
- 如果条目要求“记录失败样例”或“验证闭环”，产物可以是 markdown 记录加一个可复现命令。

## Final Response

最终说明保持简洁，并包含:
- 实际实现的 todo 条目
- 改了什么
- 关键代码链从哪到哪，以及它关联学习文档的哪几节
- 验证命令是否运行
- 如果有取舍，说明为什么先这样做

## Example Requests

- `用 $cc-implement 实现 claude-code-todo.md Phase 2 第 1 点`
- `用 $cc-implement 继续做 Claude Code Todo 的 Phase 3 第 2 点`
- `用 $cc-implement 落实 docs/claude-code/claude-code-todo.md Phase 4 第 1 点`
