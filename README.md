# Coding Agent Lab

English version: [README.en.md](./README.en.md)

这是一个用于学习、拆解、复现和比较各类 coding agent 的实验仓库。

## 仓库在做什么

- 学习不同 coding agent 的产品形态、交互方式和实现思路
- 记录公开资料整理、cleanroom 分析和实验笔记
- 从最小可运行原型开始，逐步复现核心 agent 工作流

## 当前关注

- `Claude Code`
- `OpenCode`
- 通用的 agent loop、tool use、context/memory、planning 与安全边界

## 目录说明

- `docs/`：学习文档、专题拆解、对比分析
- `reproductions/`：各个 agent 的复现目录与实验代码
- `notes/`：阶段结论、问题记录、日常笔记
- `assets/`：截图、流程图等静态资源
- `.agents/skills/`：本仓库内使用的本地 skills

## 快速导航

- [Claude Code 专题](./docs/claude-code.md)
- [Overview](./docs/overview.md)
- [Comparisons](./docs/comparisons.md)
- [OpenCode](./docs/opencode.md)
- [Findings](./notes/findings.md)

## 使用方式

这个仓库目前没有统一的全局构建命令，优先按各子目录自己的说明执行。

常用检查命令：

```bash
git status
rg "keyword" .
find reproductions -maxdepth 2 -type f | sort
```

## 目标

先把研究和最小复现做扎实，再逐步补齐工具调用、上下文管理、执行流程和评测方法。
