---
name: study-coding-agents
description: Research and study coding agents from architecture, prompting, planning, tool use, memory, execution, evaluation, and implementation angles. Use when the user asks to 系统性学习, research, compare, or拆解 a coding-agent topic, repo, paper, blog post, benchmark, or design pattern and wants Chinese Markdown study notes plus a minimal linked todo list in this repository.
---

# Study Coding Agents

## Overview

把一个 coding-agent 主题整理成两份中文 Markdown 文件:
- 一份详细学习文档，解释设计目标、核心机制、实现结构、权衡与局限
- 一份最小实现 todo 清单，按可执行顺序关联回学习文档

默认把产物落在当前仓库的 `docs/` 下:
- `docs/<topic-slug>/<topic-slug>-study.md`
- `docs/<topic-slug>/<topic-slug>-todo.md`
- `docs/<topic-slug>/images/` 用于需要时保存页面截图

先读 [references/research-playbook.md](references/research-playbook.md) 获取检索、筛选、截图、委派规则。
写文档前读 [references/output-templates.md](references/output-templates.md) 获取两份文件的建议结构。

## Workflow

1. 固定范围
- 提取主题、用户给的链接/资料、重点维度、预期深度。
- 用户未指定输出路径时，默认用上面的 `docs/<topic-slug>/` 结构。
- 若用户写的是 `todolit` 但没有给出更严格语法，按 Markdown checklist 解释。

2. 先读用户资料，再补网上资料
- 优先读用户直接提供的 repo、论文、文章、issue、talk。
- 再检索缺失角度。优先官方文档、官方仓库、论文、维护者文章/演讲、可靠 benchmark、严肃拆解文章。
- 把二手总结当成线索来源，不把它们当作核心事实的唯一依据。

3. 组织研究面
- 默认覆盖这些维度，按主题裁剪: 问题定义、系统架构、agent loop、tool use、planning、memory/context、prompting、safety、evaluation、实现细节、局限、可复现部分。
- 区分“已证实事实”和“基于多源信息的推断”。推断必须显式标注。
- 对同质、低质量、重复内容做裁剪，不要把搜到的东西全搬进去。

4. 条件性使用 subagent
- 只有当前用户明确允许 delegation / parallel work，或明确要求大范围并行研究时，才使用 subagent。
- 只委派互不阻塞的侧面任务，例如:
  - 一个子任务看官方 repo / docs / code structure
  - 一个子任务看论文 / blog / talk
  - 一个子任务看 benchmark / eval / third-party reverse engineering
- 主线程负责统一归并、交叉验证和最终写作，不把最终判断外包出去。

5. 条件性使用截图
- 仅在图示、架构图、表格、界面流程、仓库结构图确实提升理解时，才用 Playwright 截图。
- 截图放在 `docs/<topic-slug>/images/`。
- 每张图都要在正文附近有用途说明、简短图注、来源链接。不要放装饰性图片。

6. 写详细学习文档
- 用中文写。
- 先给高层摘要，再按主题展开。
- 重要结论要附来源链接。时间敏感信息写清具体日期。
- 结尾补一个“先复现什么”小节，帮助从学习过渡到动手。

7. 写最小 todo 清单
- 用 Markdown checklist，保持任务粒度小且可验证。
- 每个 todo 尽量回链到学习文档对应章节，形式如“见《...》的 xxx 节”或使用相对锚点链接。
- 优先顺序通常是: 补资料、搭最小架构、跑通 tool loop、补 memory/planning、做评测、再做增强。

8. 完成前检查
- 两个文件都存在，且都为中文。
- 学习文档里的关键结论都有来源，或明确标成推断。
- todo 清单是“最小可做版本”，不是把研究大纲重新抄一遍。
- 两份文件术语一致，交叉引用可读。

## Output Rules

- 默认使用当前仓库内路径，不把资料写到仓库外。
- 学习文档应偏事实和分析，不写成宣传稿。
- 保留 repo、paper、issue、blog、talk、benchmark 的原始链接。
- 如果不同来源冲突，说明冲突点，并解释为何更信任某个来源。
- 不要长段复制来源内容，优先总结、比较、抽象。

## Example Requests

- “系统性学习 OpenHands 的设计实现，生成中文笔记和最小复现清单。”
- “我给你几个仓库和博客，帮我整理 Claude Code / Codex CLI / Cursor Agent 的架构差异。”
- “围绕 planning、memory、tool use 三个主题，研究现有 coding agent 并产出学习材料。”
