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
- 已跑通基于 Responses API 的最小 live 只读 agent 闭环，支持 `read_file`、`search`、`git_status`。
- 已补齐统一事件流、`CLAUDE.md` / `MEMORY.md` / 用户规则加载，以及“旧工具输出先裁掉、旧会话再摘要”的最小 compaction。
- 已提供一个基础 Web UI 工作台用于查看 session、继续会话和检查 `gather -> act -> verify` 摘要。
- 仍未完成的核心控制层包括 permission gate、checkpoint，以及 live 模式下的写入型工具开放。
