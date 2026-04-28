# Claude Code

Claude Code 的系统化学习材料已整理到专题目录：

- [学习文档](./claude-code/claude-code-study.md)
- [最小实现 Todo](./claude-code/claude-code-todo.md)
- [复现说明](../reproductions/claude-code/README.md)

这一专题聚焦三个问题：
- Claude Code 公开了哪些可直接验证的工作机制
- 哪些“实现细节”只能做 cleanroom 推断
- 如果要自己复现，一个最小可行版本应该先做什么

当前仓库进展截至 `2026-03-28`：
- 已跑通基于 Responses API 的最小 live agent 闭环，支持 `read_file`、`search`、`git_status`、`edit`、`bash`。
- 已补齐统一事件流、`CLAUDE.md` / `MEMORY.md` / 用户规则加载，以及“旧工具输出先裁掉、旧会话再摘要”的最小 compaction。
- 已补齐最小控制层：`edit` / `bash` permission gate、allowlist / denylist 规则模块，以及最近一次 `edit` checkpoint / undo。
- 已完成 Phase 5 最小闭环验证，并记录了一个 permission denylist 导致 `bash` 在 act 阶段被拦截的失败样例。
- 已提供一个基础 Web UI 工作台用于查看 session、继续会话和检查 `gather -> act -> verify` 摘要。
- 下一阶段重点是 Claude Code 风格增强：plan mode、subagent、hooks，以及 commands / agents / hooks 的文件协议化扩展层。
