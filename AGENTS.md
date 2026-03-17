# Repository Guidelines

## 仓库结构

本仓库用于学习、拆解、复现和比较各类 coding agent。

- `docs/`：架构理解、拆解记录、对比分析
- `reproductions/`：各个 agent 的复现代码与实验目录
- `notes/`：学习日志、阶段结论、问题记录
- `assets/`：截图、流程图等静态资源

新增复现项目时，统一放在 `reproductions/<agent-name>/` 下，并尽量把代码、说明、测试放在同一个子目录内。

## 开发与运行

当前仓库没有统一的全局构建命令，优先使用各子项目自己的命令，并在对应目录的 `README.md` 中写清楚。

常用检查命令：

```bash
git status
rg "keyword" .
find reproductions -maxdepth 2 -type f | sort
```

示例：

```bash
cd reproductions/opencode
python -m pytest
node --test
```

## 代码风格

- 文件编码：`UTF-8`
- 行尾：`LF`
- Markdown：标题清晰，内容简短直接
- Python：4 空格缩进，函数/文件用 `snake_case`，类名用 `PascalCase`
- JS/TS：遵循子项目现有风格，变量和函数用 `camelCase`

如果某个复现目录已有自己的风格或格式化配置，以该目录为准。

## 测试约定

有可运行代码时，尽量补测试，并放在对应复现目录附近，例如 `reproductions/<agent>/tests/`。

- Python：`test_*.py`
- JS/TS：`*.test.js`、`*.test.ts`

每个可运行的复现目录，至少提供一个可执行的测试或验证命令。

## 提交与 PR

提交信息尽量简短，建议使用：

- `feat: ...`
- `fix: ...`
- `docs: ...`
- `chore: ...`

PR 说明至少包含：

- 改动目的
- 影响目录
- 运行或测试说明

涉及界面或流程变化时，再附截图。

## 安全与边界

- 不要提交密钥、Token、`.env` 或大体积生成文件
- 尽量保持每个复现项目依赖独立，避免相互污染
- 不要在同一个 PR 中混入无关实验改动

## 自定义约定

下面内容留给仓库维护者后续补充：

- 当前优先复现的 agent 列表
- 各实验目录的技术栈选择
- 通用脚手架或模板约定
- 个人偏好的提交、评审与记录方式
