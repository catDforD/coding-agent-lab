# Claude Code 最小实现 Todo

关联文档: [Claude Code 学习文档](./claude-code-study.md)

## Phase 1. 补齐输入和边界
- [ ] 确认这次复现只追求最小闭环，不追求完整 UI、完整插件市场和完整云端环境。见学习文档的“9. 可复现的最小子集”。
- [ ] 先固定 cleanroom 原则：只依据公开行为和公开接口复现，不假设拿到了 Claude Code 内核源码。见“3.3 已证实事实与推断的边界”。
- [ ] 列出第一阶段必须支持的工具和可以后置的能力。见“5.1 Tool Use”和“9.1 第一阶段必须有”。

## Phase 2. 搭最小架构
- [ ] 建一个 CLI 入口，能接收任务、保存 session id，并支持继续上一次会话。见“4. 核心运行循环”和“9.1 第一阶段必须有”。
- [ ] 实现 `gather -> act -> verify` 主循环，先不做复杂 planning。见“4. 核心运行循环”。
- [ ] 定义统一事件流结构，至少记录用户消息、模型响应、工具调用、工具结果。见“5.3 Memory / Context”和“9.1 第一阶段必须有”。
- [ ] 接入最小工具集：`read_file`、`search`、`edit`、`bash`、`git_status`。见“5.1 Tool Use”和“9.1 第一阶段必须有”。

## Phase 3. 补上下文和规则层
- [ ] 启动时加载项目 `CLAUDE.md`、用户级规则和一个简化版 `MEMORY.md`。见“5.3 Memory / Context”。
- [ ] 设计 prompt/context builder，把会话历史、最近工具输出和规则文件拼成统一输入。见“3.1 官方可直接确认的高层结构”和“5.3 Memory / Context”。
- [ ] 加一个最小 compaction 策略：优先裁掉旧工具输出，再保留一段摘要。见“5.3 Memory / Context”。

## Phase 4. 补控制层
- [ ] 给 `bash` 和 `edit` 加 permission gate，先做最简单的 confirm/deny 交互。见“5.5 Safety / Boundaries”。
- [ ] 做文件 checkpoint：写入前备份、支持撤销最近一次修改。见“5.5 Safety / Boundaries”和“9.1 第一阶段必须有”。
- [ ] 把 allowlist / denylist 配置做成单独模块，避免以后和 runtime 耦合。见“5.5 Safety / Boundaries”。

## Phase 5. 验证最小闭环
- [ ] 用一个“读代码并解释”的任务验证只读闭环。见“4. 核心运行循环”。
- [ ] 用一个“修复测试失败并重跑”的任务验证写入闭环。见“4.1 一个最小闭环”。
- [ ] 记录至少一个失败样例，说明是检索、上下文、权限还是工具反馈出了问题。见“8. 评测、效果与局限”。

## Phase 6. 增强到 Claude Code 风格
- [ ] 增加 `plan mode`，先输出计划再等待执行批准。见“5.2 Planning”。
- [ ] 增加最小 subagent 机制：独立 prompt、独立工具集、返回摘要。见“6.3 Subagents 是独立上下文，不是同一窗口分身”。
- [ ] 增加 hooks 机制，至少支持 `PreToolUse` 拦截。见“6.4 Hooks 的真实价值”。
- [ ] 把 commands / agents / hooks 做成文件协议，为后续插件化留接口。见“6.2 插件系统说明了什么”。

## Phase 7. 后续研究
- [ ] 对照 Southbridge cleanroom 分析，把 compaction、缓存、检索剪枝实现成可替换策略。见“7. 第三方 cleanroom 分析：哪些值得信，哪些要保留”。
- [ ] 再补 WebSearch / MCP / managed settings，逐步逼近官方扩展生态。见“6.2 插件系统说明了什么”。
- [ ] 为复现版写一份 smoke test 清单，固定 2-3 个代表性任务做回归验证。见“8. 评测、效果与局限”。
